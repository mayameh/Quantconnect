"""
Backtest harness for EnhancedtradingBOTAlpaca.

Replays the full main_alpaca.py trading logic (regime detection, universe
scoring, signal evaluation, exit rules, risk circuit breakers) against
yfinance daily-bar data, using MockTradingClient and MockHistoricalDataClient
in place of the live Alpaca SDK clients.

Usage:
    python backtest_harness.py
    python backtest_harness.py --start 2024-01-01 --end 2024-12-31
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytz

sys.path.insert(0, str(Path(__file__).parent))

from bot_config import BOT_Config
from mock_alpaca import MockTradingClient, MockHistoricalDataClient


class AlpacaBacktestHarness:
    """
    Daily-bar replay harness implementing the same trading logic as
    main_alpaca.py, but driven by yfinance data and mock Alpaca clients.
    """

    def __init__(self, symbols: list[str], start_date: str, end_date: str,
                 starting_cash: float = 11_000.0, trading_style: str = "INTRADAY"):
        self.config   = BOT_Config()
        self.et       = pytz.timezone("US/Eastern")

        self.start_date = pd.to_datetime(start_date).tz_localize(self.et)
        self.end_date   = pd.to_datetime(end_date).tz_localize(self.et)
        self.starting_cash = float(starting_cash)
        self.trading_style = str(trading_style or "INTRADAY").strip().upper()
        if self.trading_style not in {"INTRADAY", "SWING"}:
            self.trading_style = "INTRADAY"
        self._style_settings = self._build_style_settings()

        # Mock clients (replace real Alpaca SDK)
        self.trading_client = MockTradingClient(self.starting_cash)
        self.data_client    = MockHistoricalDataClient()

        # Universe state
        self._dynamic_symbols:        set[str]              = set()
        self._active_universe:        list[str]             = list(
            dict.fromkeys(list(symbols or []) + self.config.universe.core_symbols)
        )
        self._last_universe_refresh:  datetime | None       = None

        # Trading state (mirrors main_alpaca.py __init__)
        self._starting_cash = self.starting_cash
        self.peak_equity    = self.starting_cash
        self.market_regime  = "NEUTRAL"
        self._trading_enabled = True

        self._algo_managed_positions:  set[str]            = set()
        self.entry_time:               dict[str, datetime] = {}
        self.entry_price:              dict[str, float]    = {}
        self.highest_price:            dict[str, float]    = {}
        self._entry_strategy_reason:   dict[str, str]      = {}
        self._entry_timestamps:        deque               = deque(maxlen=200)
        self._last_exit_time:          dict[str, datetime] = {}

        self._strategy_weights: dict[str, float] = {
            "momentum":       0.30,
            "mean_reversion": 0.20,
            "orb":            0.15,
            "vwap_twap":      0.20,
            "market_making":  0.05,
            "stat_arb":       0.00,
            "sentiment":      0.00,   # disabled in backtest (no live news)
        }
        self._min_portfolio_signal_score = 0.30

        self.trade_history  = deque(maxlen=500)
        self.winning_trades = deque(maxlen=200)
        self.losing_trades  = deque(maxlen=200)

        self.symbol_performance: dict = defaultdict(
            lambda: {"trades": 0, "wins": 0, "total_pnl": 0.0,
                     "consecutive_losses": 0, "win_rate": 0.0}
        )

        self.trades:        list[dict] = []
        self.equity_history: list[dict] = []
        self.loaded_symbols: set[str]  = set()

    def _build_style_settings(self) -> dict:
        cfg = self.config.trading
        risk = self.config.risk
        if self.trading_style == "SWING":
            return {
                "flatten_eod": False,
                "stop_loss_pct": float(getattr(cfg, "swing_stop_loss_pct", cfg.stop_loss_pct)),
                "take_profit_pct": float(getattr(cfg, "swing_take_profit_pct", cfg.take_profit_pct)),
                "profit_lock_hours": int(getattr(cfg, "swing_profit_lock_hours", cfg.profit_lock_hours)),
                "profit_lock_min_gain_pct": float(
                    getattr(cfg, "swing_profit_lock_min_gain_pct", cfg.profit_lock_min_gain_pct)
                ),
                "trailing_stop_pct": float(getattr(cfg, "swing_trailing_stop_pct", cfg.trailing_stop_pct)),
                "trailing_activation_pct": float(
                    getattr(cfg, "swing_trailing_activation_pct", cfg.trailing_activation_pct)
                ),
                "max_daily_loss": max(
                    float(getattr(risk, "max_daily_loss", 100)),
                    float(getattr(risk, "swing_max_daily_loss", 250)),
                ),
                "min_hold_hours": int(getattr(cfg, "min_hold_hours", 24)),
                "max_hold_days": int(getattr(cfg, "max_hold_days", 14)),
            }
        return {
            "flatten_eod": True,
            "stop_loss_pct": float(getattr(cfg, "intraday_stop_loss_pct", 0.01)),
            "take_profit_pct": float(getattr(cfg, "intraday_take_profit_pct", 0.025)),
            "profit_lock_hours": int(getattr(cfg, "intraday_profit_lock_hours", 3)),
            "profit_lock_min_gain_pct": float(getattr(cfg, "intraday_profit_lock_min_gain_pct", 0.006)),
            "trailing_stop_pct": float(getattr(cfg, "intraday_trailing_stop_pct", 0.008)),
            "trailing_activation_pct": float(getattr(cfg, "intraday_trailing_activation_pct", 0.012)),
            "max_daily_loss": max(
                float(getattr(risk, "max_daily_loss", 100)),
                float(getattr(risk, "intraday_max_daily_loss", 150)),
            ),
            "min_hold_hours": 0,
            "max_hold_days": 1,
        }

    def _is_swing_style(self) -> bool:
        return self.trading_style == "SWING"

    # =========================================================================
    #  DATA LOADING
    # =========================================================================
    @staticmethod
    def _normalize_df(df: pd.DataFrame, et) -> pd.DataFrame:
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = [str(c).lower() for c in df.columns]
        required   = ["open", "high", "low", "close", "volume"]
        if any(c not in df.columns for c in required):
            return pd.DataFrame()
        if df.index.tz is None:
            df.index = df.index.tz_localize(et)
        else:
            df.index = df.index.tz_convert(et)
        out          = df[required].copy()
        out["adjclose"] = out["close"]
        return out

    @staticmethod
    def _ema(series: pd.Series, span: int) -> pd.Series:
        return series.ewm(span=span, adjust=False).mean()

    @staticmethod
    def _rsi(series: pd.Series, length: int = 14) -> pd.Series:
        delta    = series.diff()
        gain     = delta.clip(lower=0)
        loss     = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
        rs       = avg_gain / avg_loss.replace(0, pd.NA)
        return 100 - (100 / (1 + rs))

    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        w = df.copy()
        w["ema_20"]      = self._ema(w["close"], 20)
        w["ema_50"]      = self._ema(w["close"], 50)
        w["rsi"]         = self._rsi(w["close"], 14)
        ema_fast         = self._ema(w["close"], 12)
        ema_slow         = self._ema(w["close"], 26)
        w["macd"]        = ema_fast - ema_slow
        w["macd_signal"] = self._ema(w["macd"], 9)
        return w

    def _load_symbol(self, symbol: str) -> pd.DataFrame:
        try:
            import yfinance as yf
            raw = yf.download(
                symbol,
                start=(self.start_date - timedelta(days=220)).date(),
                end=(self.end_date + timedelta(days=1)).date(),
                interval="1d",
                progress=False,
                auto_adjust=False,
            )
            if raw.empty:
                return pd.DataFrame()
            clean = self._normalize_df(raw, self.et)
            if clean.empty:
                return pd.DataFrame()
            return self._add_indicators(clean)
        except Exception as exc:
            print(f"  [ERROR] {symbol}: {exc}")
            return pd.DataFrame()

    def load_all_data(self):
        cfg = self.config.universe
        data_symbols = set(self._active_universe)
        data_symbols.update(cfg.core_symbols)
        if getattr(cfg, "dynamic_enabled", False):
            data_symbols.update(getattr(cfg, "candidate_symbols", []))
        data_symbols.add("SPY")

        print(f"Loading data for {len(data_symbols)} symbols...")
        for symbol in sorted(data_symbols):
            df = self._load_symbol(symbol)
            if df.empty:
                print(f"  ✗ {symbol}")
                continue
            self.data_client.load_data(symbol, df)
            self.loaded_symbols.add(symbol)
            print(f"  ✓ {symbol}: {len(df)} bars")

        self._active_universe = [s for s in self._active_universe if s in self.loaded_symbols]

    # =========================================================================
    #  HELPERS  (mirrors main_alpaca.py helpers, using data_client)
    # =========================================================================
    def _to_ts(self, value) -> pd.Timestamp:
        ts = pd.to_datetime(value)
        if ts.tzinfo is None:
            return ts.tz_localize(self.et)
        return ts.tz_convert(self.et)

    def _get_bar_slice(self, symbol: str, up_to) -> pd.DataFrame:
        df = self.data_client._daily_data.get(symbol, pd.DataFrame())
        if df.empty:
            return pd.DataFrame()
        ts = self._to_ts(up_to)
        return df[df.index <= ts].copy()

    def _get_bar(self, symbol: str, up_to) -> dict | None:
        sl = self._get_bar_slice(symbol, up_to)
        if sl.empty:
            return None
        row = sl.iloc[-1]
        return {
            "symbol": symbol, "date": sl.index[-1],
            "open":   float(row["open"]),  "high": float(row["high"]),
            "low":    float(row["low"]),   "close": float(row["close"]),
            "volume": int(row["volume"]),
        }

    def _get_entry_price(self, symbol: str, current_ts) -> float:
        bar = self._get_bar(symbol, current_ts)
        if not bar:
            return 0.0
        if self._style_settings["flatten_eod"]:
            return float(bar["open"])
        return float(bar["close"])

    def _advance_prices(self, ts):
        for symbol in self.loaded_symbols:
            bar = self._get_bar(symbol, ts)
            if not bar:
                continue
            self.trading_client.update_price(symbol, bar["close"])

    @staticmethod
    def _safe_float(value, default: float = 0.0) -> float:
        try:
            return default if value is None else float(value)
        except (TypeError, ValueError):
            return default

    # account helpers (mirrors properties in main_alpaca.py)
    @property
    def cash(self) -> float:
        return float(self.trading_client.get_account().cash)

    @property
    def net_liquidation(self) -> float:
        return float(self.trading_client.get_account().portfolio_value)

    def _get_positions(self) -> dict:
        result = {}
        for pos in self.trading_client.get_all_positions():
            qty = float(pos.qty)
            if qty == 0:
                continue
            result[pos.symbol] = {
                "qty":          abs(qty),
                "avg_cost":     float(pos.avg_entry_price),
                "market_price": float(pos.current_price),
                "side":         "LONG" if qty > 0 else "SHORT",
            }
        return result

    def _get_price(self, ticker: str) -> float:
        return self.trading_client._prices.get(ticker, 0.0)

    # =========================================================================
    #  UNIVERSE REFRESH
    # =========================================================================
    def _build_active_universe(self):
        merged = list(self.config.universe.core_symbols) + sorted(self._dynamic_symbols)
        seen: set = set()
        self._active_universe = [
            t for t in merged
            if t in self.loaded_symbols and not (t in seen or seen.add(t))
        ]

    def _score_dynamic_candidate(self, ticker: str, current_ts) -> tuple | None:
        cfg        = self.config.universe
        lookback   = int(getattr(cfg, "momentum_lookback_days", 22))
        vol_window = int(getattr(cfg, "avg_volume_window_days", 20))
        min_req    = max(lookback + 1, vol_window)

        sl = self._get_bar_slice(ticker, current_ts)
        if sl.empty or len(sl) < min_req + 1:
            return None

        close = sl["close"].dropna()
        if len(close) < min_req + 1:
            return None

        current_price  = self._safe_float(close.iloc[-1])
        lookback_price = self._safe_float(close.iloc[-(lookback + 1)])
        if current_price <= 0 or lookback_price <= 0:
            return None

        if current_price < self._safe_float(getattr(cfg, "min_price", 0.0)):
            return None

        recent        = sl.tail(vol_window).copy()
        recent["dv"]  = recent["close"] * recent["volume"]
        avg_dv        = self._safe_float(recent["dv"].mean())
        if avg_dv < self._safe_float(getattr(cfg, "min_avg_dollar_volume", 0.0)):
            return None

        momentum       = (current_price / lookback_price) - 1.0
        rev_map        = getattr(cfg, "revenue_growth_1y", {})
        revenue_growth = self._safe_float(rev_map.get(ticker, 0.0))

        w_mom  = self._safe_float(getattr(cfg, "momentum_weight", 0.6), 0.6)
        w_rev  = self._safe_float(getattr(cfg, "revenue_growth_weight", 0.4), 0.4)
        score  = w_mom * momentum + w_rev * revenue_growth
        return score, momentum, revenue_growth

    def _refresh_dynamic_universe(self, current_ts, force: bool = False):
        cfg = self.config.universe
        if not getattr(cfg, "dynamic_enabled", False):
            self._dynamic_symbols = set()
            self._build_active_universe()
            return

        refresh_days = max(1, int(getattr(cfg, "refresh_days", 14)))
        if not force and self._last_universe_refresh is not None:
            if (current_ts.date() - self._last_universe_refresh.date()).days < refresh_days:
                return

        core      = set(cfg.core_symbols)
        excludes  = set(getattr(cfg, "exclude_symbols", []))
        candidates = [
            t for t in getattr(cfg, "candidate_symbols", [])
            if t not in core and t not in excludes and t in self.loaded_symbols
        ]

        scored = []
        for ticker in candidates:
            try:
                res = self._score_dynamic_candidate(ticker, current_ts)
                if res:
                    scored.append((ticker, *res))
            except Exception:
                continue

        scored.sort(key=lambda x: x[1], reverse=True)
        top_n                       = max(0, int(getattr(cfg, "top_n_dynamic", 10)))
        self._dynamic_symbols       = {t for t, *_ in scored[:top_n]}
        self._build_active_universe()
        self._last_universe_refresh = current_ts

    # =========================================================================
    #  INDICATORS + REGIME
    # =========================================================================
    def _compute_spy_indicators(self, current_ts) -> dict | None:
        sl = self._get_bar_slice("SPY", current_ts)
        if sl.empty or len(sl) < 55:
            return None

        def v(row, col):
            val = row.get(col)
            return float(val) if val is not None and pd.notna(val) else None

        latest = sl.iloc[-1]
        prev   = sl.iloc[-2]
        return {
            "price":       float(latest["close"]),
            "ema_20":      v(latest, "ema_20"),
            "ema_50":      v(latest, "ema_50"),
            "ema_20_prev": v(prev, "ema_20"),
            "rsi":         v(latest, "rsi"),
        }

    def _compute_intraday_indicators(self, ticker: str, current_ts) -> dict | None:
        sl = self._get_bar_slice(ticker, current_ts)
        if self._style_settings["flatten_eod"] and len(sl) > 1:
            sl = sl.iloc[:-1]
        if sl.empty or len(sl) < 52:
            return None

        latest = sl.iloc[-1]

        bb_mid  = sl["close"].rolling(20).mean().iloc[-1]
        bb_std  = sl["close"].rolling(20).std(ddof=0).iloc[-1]
        zscore  = (0.0 if pd.isna(bb_mid) or pd.isna(bb_std) or bb_std == 0
                   else float((latest["close"] - bb_mid) / bb_std))

        vol_sma = sl["volume"].rolling(20).mean().iloc[-1]
        vol_ratio = 1.0 if pd.isna(vol_sma) or vol_sma <= 0 else float(latest["volume"] / vol_sma)

        vwap_n = (sl["close"] * sl["volume"]).rolling(20).sum().iloc[-1]
        vwap_d = sl["volume"].rolling(20).sum().iloc[-1]
        vwap   = (float(latest["close"]) if pd.isna(vwap_d) or vwap_d <= 0
                  else float(vwap_n / vwap_d))

        orb_high = float(sl["high"].tail(20).max())

        needed = {"close": "price", "macd": "macd", "macd_signal": "macd_signal",
                  "rsi": "rsi", "ema_50": "ema_50"}
        out = {}
        for col, key in needed.items():
            val = latest.get(col)
            if val is None or pd.isna(val):
                return None
            out[key] = float(val)

        out.update({"vwap": vwap, "zscore": zscore, "volume_ratio": vol_ratio,
                    "opening_range_high": orb_high, "opening_range_low": orb_high,
                    "atr_14": 0.0})
        return out

    def _compute_swing_indicators(self, ticker: str, current_ts) -> dict | None:
        sl = self._get_bar_slice(ticker, current_ts)
        if sl.empty or len(sl) < 55:
            return None

        latest = sl.iloc[-1]
        bb_mid = sl["close"].rolling(20).mean().iloc[-1]
        bb_std = sl["close"].rolling(20).std(ddof=0).iloc[-1]
        zscore = (0.0 if pd.isna(bb_mid) or pd.isna(bb_std) or bb_std == 0
                  else float((latest["close"] - bb_mid) / bb_std))
        vol_sma = sl["volume"].rolling(20).mean().iloc[-1]
        vol_ratio = 1.0 if pd.isna(vol_sma) or vol_sma <= 0 else float(latest["volume"] / vol_sma)
        vwap_n = (sl["close"] * sl["volume"]).rolling(20).sum().iloc[-1]
        vwap_d = sl["volume"].rolling(20).sum().iloc[-1]
        vwap = (float(latest["close"]) if pd.isna(vwap_d) or vwap_d <= 0
                else float(vwap_n / vwap_d))
        breakout_20 = sl["high"].rolling(20).max().iloc[-1]

        needed = {"close": "price", "macd": "macd", "macd_signal": "macd_signal",
                  "rsi": "rsi", "ema_50": "ema_50"}
        out = {}
        for col, key in needed.items():
            val = latest.get(col)
            if val is None or pd.isna(val):
                return None
            out[key] = float(val)

        out.update({"vwap": vwap, "zscore": zscore, "volume_ratio": vol_ratio,
                    "opening_range_high": float(breakout_20), "opening_range_low": float(latest["low"]),
                    "atr_14": 0.0})
        return out

    def _compute_symbol_indicators(self, ticker: str, current_ts) -> dict | None:
        if self._is_swing_style():
            return self._compute_swing_indicators(ticker, current_ts)
        return self._compute_intraday_indicators(ticker, current_ts)

    def _trade_count_this_week(self, now: datetime) -> int:
        week_start = (now - timedelta(days=now.weekday())).date()
        return sum(1 for ts in self._entry_timestamps if ts.date() >= week_start)

    def _symbol_on_cooldown(self, ticker: str, now: datetime) -> bool:
        cfg = self.config.trading
        last_exit = self._last_exit_time.get(ticker)
        if not last_exit:
            return False
        elapsed_days = (now.date() - last_exit.date()).days
        return elapsed_days < int(getattr(cfg, "min_days_between_trades", 0)) or elapsed_days < int(getattr(cfg, "symbol_cooldown_days", 0))

    def _compute_portfolio_signal(self, ticker: str, ind: dict) -> tuple[float, str]:
        price        = ind["price"]
        macd         = ind["macd"]
        macd_sig     = ind["macd_signal"]
        rsi_val      = ind["rsi"]
        ema_val      = ind["ema_50"]
        vwap         = ind["vwap"]
        zscore       = ind["zscore"]
        volume_ratio = ind["volume_ratio"]
        orb_high     = ind["opening_range_high"]

        comp: dict[str, float] = {k: 0.0 for k in self._strategy_weights}

        if price > ema_val and macd > macd_sig and 55 <= rsi_val <= 72:
            raw = ((macd - macd_sig) * 100) + min(1.0, max(0.0, volume_ratio - 1.0))
            comp["momentum"] = min(1.0, max(0.0, raw))

        if zscore < -1.8 and rsi_val < 42 and price < vwap:
            comp["mean_reversion"] = min(1.0, abs(zscore) / 3.0)

        if price > orb_high and volume_ratio >= 1.2:
            comp["orb"] = min(1.0, (price / max(orb_high, 1e-9)) - 1.0 + 0.5)

        if price > vwap and macd > macd_sig:
            comp["vwap_twap"] = min(1.0, ((price / max(vwap, 1e-9)) - 1.0) * 200)

        weighted = 0.0
        active: list[str] = []
        for name, score in comp.items():
            weight = self._strategy_weights.get(name, 0.0)
            if score > 0 and weight > 0:
                active.append(f"{name}:{score:.2f}")
            weighted += weight * score

        return weighted, ", ".join(active) if active else "none"

    def detect_market_regime(self, current_ts):
        spy = self._compute_spy_indicators(current_ts)
        if not spy:
            return

        ema_20     = spy["ema_20"]
        ema_50     = spy["ema_50"]
        ema_20_prev = spy["ema_20_prev"]
        spy_price  = spy["price"]

        if any(v is None for v in [ema_20, ema_50, ema_20_prev]):
            return

        prev = self.market_regime
        pa50 = spy_price > ema_50
        pb50 = spy_price < ema_50
        bull = ema_20 > ema_50
        bear = ema_20 < ema_50
        rising = ema_20 > ema_20_prev
        mb = (not rising) and spy_price < ema_20

        if pa50 and bull:
            self.market_regime = "BULL"
        elif pb50 and bear and mb:
            self.market_regime = "BEAR"
        else:
            if prev == "BULL" and pa50:
                self.market_regime = "BULL"
            elif prev == "BEAR" and pb50:
                self.market_regime = "BEAR"
            else:
                self.market_regime = "NEUTRAL"

    # =========================================================================
    #  EXECUTION + SIZING
    # =========================================================================
    @staticmethod
    def _estimate_fee(qty: int, price: float) -> float:
        if qty <= 0 or price <= 0:
            return 0.0
        return min(0.000008 * qty * price, 0.35)

    def _calculate_position_size(self, price: float) -> int:
        available_cash = self.cash
        target_value   = min(available_cash * 0.75 * 0.85, 6500)
        if target_value < 4000:
            return 0
        qty = int(target_value / price)
        if qty <= 0:
            return 0
        fee        = self._estimate_fee(qty, price)
        total_cost = qty * price + fee
        if total_cost > target_value:
            qty        = int((target_value - fee) / price)
            total_cost = qty * price + self._estimate_fee(qty, price)
        return qty if qty > 0 and total_cost >= 4000 else 0

    def _place_market_buy(self, ticker: str, qty: int, fill_price: float | None = None) -> bool:
        from mock_alpaca import _OrderStatus
        if fill_price is not None and fill_price > 0:
            self.trading_client.update_price(ticker, fill_price)
        req   = type("R", (), {"symbol": ticker, "qty": qty, "side": "buy",
                               "time_in_force": "day"})()
        order = self.trading_client.submit_order(req)
        return order.status == _OrderStatus.FILLED

    def _place_market_sell(self, ticker: str, qty: int, reason: str = "", fill_price: float | None = None) -> bool:
        from mock_alpaca import _OrderStatus
        if fill_price is not None and fill_price > 0:
            self.trading_client.update_price(ticker, fill_price)
        req   = type("R", (), {"symbol": ticker, "qty": qty, "side": "sell",
                               "time_in_force": "day"})()
        order = self.trading_client.submit_order(req)
        return order.status == _OrderStatus.FILLED

    def _update_symbol_performance(self, ticker: str, pnl: float):
        perf = self.symbol_performance[ticker]
        perf["trades"] += 1
        perf["total_pnl"] += pnl
        if pnl > 0:
            perf["wins"] += 1
            perf["consecutive_losses"] = 0
        else:
            perf["consecutive_losses"] += 1
        perf["win_rate"] = perf["wins"] / perf["trades"] * 100 if perf["trades"] else 0

    def _record_trade(self, date_value, symbol: str, action: str, qty: int,
                      price: float, pnl_pct: float = 0.0, reason: str = ""):
        self.trades.append({
            "date": pd.to_datetime(date_value).date(),
            "symbol": symbol, "action": action,
            "qty": int(qty), "price": float(price),
            "pnl_pct": float(pnl_pct),
            "reason": reason,
        })

    # =========================================================================
    #  SIGNAL EVALUATION (entry + exit)
    # =========================================================================
    def evaluate_signals(self, current_ts):
        if not self._trading_enabled:
            return

        current_equity = self.net_liquidation
        if current_equity <= 0:
            return

        daily_loss = self._starting_cash - current_equity
        if daily_loss > self._style_settings["max_daily_loss"]:
            self._trading_enabled = False
            return

        if current_equity > self.peak_equity:
            self.peak_equity = current_equity

        drawdown = ((self.peak_equity - current_equity) / self.peak_equity
                    if self.peak_equity > 0 else 0)
        if drawdown > self.config.risk.max_drawdown_pct:
            self._trading_enabled = False
            return

        now       = current_ts.to_pydatetime()
        positions = self._get_positions()
        algo_pos  = {t: p for t, p in positions.items() if t in self._algo_managed_positions}

        # ── EXIT ───────────────────────────────────────────────────────
        for ticker, pos in list(algo_pos.items()):
            current_price = pos["market_price"]
            if current_price <= 0:
                continue

            avg_entry  = pos["avg_cost"]
            qty        = int(pos["qty"])
            pnl_pct    = (current_price - avg_entry) / avg_entry if avg_entry > 0 else 0
            pnl_dollar = qty * (current_price - avg_entry)
            held       = now - self.entry_time.get(ticker, now)

            self.highest_price[ticker] = max(
                self.highest_price.get(ticker, current_price), current_price
            )

            should_exit, reason = False, ""
            cfg = self._style_settings

            if pnl_pct <= -cfg["stop_loss_pct"]:
                should_exit, reason = True, "stop_loss"
            elif held >= timedelta(hours=cfg["min_hold_hours"]) and pnl_pct >= cfg["take_profit_pct"]:
                should_exit, reason = True, "take_profit"
            elif held >= timedelta(hours=cfg["profit_lock_hours"]) and pnl_pct >= cfg["profit_lock_min_gain_pct"]:
                should_exit, reason = True, "profit_lock"
            elif held >= timedelta(days=cfg["max_hold_days"]):
                should_exit, reason = True, "time_exit"

            trail_activation = avg_entry * (1 + cfg["trailing_activation_pct"])
            trailing_stop = self.highest_price.get(ticker, current_price) * (1 - cfg["trailing_stop_pct"])
            if self.highest_price.get(ticker, current_price) >= trail_activation and current_price <= trailing_stop:
                should_exit, reason = True, "trailing_stop"

            if pnl_pct <= -0.05:
                should_exit, reason = True, "gap_protection"

            if should_exit and self._place_market_sell(ticker, qty, reason, current_price):
                self._algo_managed_positions.discard(ticker)
                self._entry_strategy_reason.pop(ticker, None)
                self._last_exit_time[ticker] = now
                self._update_symbol_performance(ticker, pnl_dollar)
                self._record_trade(now, ticker, "SELL", qty, current_price, pnl_pct * 100, reason)

                msg = (f"{now.strftime('%Y-%m-%d')} SELL {ticker} - Qty: {qty} "
                       f"@ ${current_price:.2f} | P&L: ${pnl_dollar:.2f} ({pnl_pct:.2%})")
                self.trade_history.append(msg)
                (self.winning_trades if pnl_dollar > 0 else self.losing_trades).append(msg)

        # ── ENTRY ──────────────────────────────────────────────────────
        if self.market_regime == "BEAR":
            return

        portfolio_return = ((current_equity - self._starting_cash) / self._starting_cash
                            if self._starting_cash > 0 else 0)
        cfg              = self.config.trading
        max_positions    = cfg.max_positions

        if self.market_regime == "BULL" and portfolio_return > 0.02:
            max_allowed = max_positions
        elif self.market_regime == "BULL":
            max_allowed = min(3, max_positions)
        elif self.market_regime == "NEUTRAL":
            max_allowed = min(2, max_positions)
        else:
            max_allowed = 1

        algo_count = len([t for t in self._algo_managed_positions if t in positions])
        if algo_count >= max_allowed or self.cash < 4000:
            return

        if self._is_swing_style() and self._trade_count_this_week(now) >= self.config.trading.max_weekly_trades:
            return

        candidates: list[tuple] = []
        for ticker in self._active_universe:
            if ticker == "SPY" or ticker in positions:
                continue
            if self._is_swing_style() and self._symbol_on_cooldown(ticker, now):
                continue
            ind = self._compute_symbol_indicators(ticker, current_ts)
            if not ind or ind["price"] <= 0:
                continue
            score, reason = self._compute_portfolio_signal(ticker, ind)
            if score >= self._min_portfolio_signal_score:
                candidates.append((ticker, score, ind["price"], reason))

        if not candidates:
            return

        candidates.sort(key=lambda x: x[1], reverse=True)
        best_ticker, best_score, current_price, reason = candidates[0]

        entry_price = self._get_entry_price(best_ticker, current_ts)
        if entry_price > 0:
            current_price = entry_price
        else:
            live_price = self._get_price(best_ticker)
            if live_price > 0:
                current_price = live_price

        qty = self._calculate_position_size(current_price)
        if qty <= 0:
            return

        if self._place_market_buy(best_ticker, qty, current_price):
            self._algo_managed_positions.add(best_ticker)
            self.entry_time[best_ticker]             = now
            self.entry_price[best_ticker]            = current_price
            self.highest_price[best_ticker]          = current_price
            self._entry_strategy_reason[best_ticker] = reason
            self._entry_timestamps.append(now)

            self._record_trade(now, best_ticker, "BUY", qty, current_price, 0.0, reason)
            self.trade_history.append(
                f"{now.strftime('%Y-%m-%d')} BUY {best_ticker} - Qty: {qty} "
                f"@ ${current_price:.2f} | score={best_score:.2f} | {reason}"
            )

    def flatten_intraday_positions(self, current_ts):
        if not self._style_settings["flatten_eod"]:
            return
        now = current_ts.to_pydatetime()
        positions = self._get_positions()
        for ticker in list(self._algo_managed_positions):
            pos = positions.get(ticker)
            if not pos:
                continue
            qty = int(pos.get("qty", 0))
            if qty <= 0:
                continue
            day_bar = self._get_bar(ticker, current_ts)
            current_price = float(day_bar["close"]) if day_bar else pos.get("market_price", 0.0)
            if self._place_market_sell(ticker, qty, "eod_flatten", current_price):
                self._algo_managed_positions.discard(ticker)
                self._entry_strategy_reason.pop(ticker, None)
                self._last_exit_time[ticker] = now
                avg_entry = float(pos.get("avg_cost", current_price) or current_price)
                pnl_pct = ((current_price - avg_entry) / avg_entry * 100) if avg_entry > 0 else 0.0
                self._record_trade(now, ticker, "SELL", qty, current_price, pnl_pct, "eod_flatten")

    # =========================================================================
    #  EQUITY UPDATE
    # =========================================================================
    def update_equity(self, current_ts):
        equity = self.net_liquidation
        self.equity_history.append({
            "date":   self._to_ts(current_ts),
            "equity": float(equity),
            "cash":   float(self.cash),
            "regime": self.market_regime,
        })

    # =========================================================================
    #  MAIN LOOP
    # =========================================================================
    def process_day(self, date_value):
        ts = self._to_ts(date_value)
        self.data_client.set_current_date(ts)
        self._advance_prices(ts)
        self._refresh_dynamic_universe(ts, force=False)
        self.detect_market_regime(ts)
        self.evaluate_signals(ts)
        self.flatten_intraday_positions(ts)
        self.update_equity(ts)

    def run_backtest(self):
        print("=" * 80)
        print("RUNNING ALPACA BOT BACKTEST")
        print("=" * 80)
        print(f"Period:  {self.start_date.date()} → {self.end_date.date()}")
        print(f"Capital: ${self.starting_cash:,.2f}")
        print(f"Style:   {self.trading_style}")

        self.load_all_data()

        self._refresh_dynamic_universe(self.start_date, force=True)

        current = self.start_date
        while current <= self.end_date:
            if current.weekday() < 5:
                self.process_day(current)
            current += timedelta(days=1)

    # =========================================================================
    #  REPORT
    # =========================================================================
    def report(self) -> dict:
        if not self.equity_history:
            print("No equity history — backtest may have failed.")
            return {}

        eq_df     = pd.DataFrame(self.equity_history)
        start_eq  = eq_df["equity"].iloc[0]
        end_eq    = eq_df["equity"].iloc[-1]
        total_ret = (end_eq - start_eq) / start_eq * 100

        max_eq  = eq_df["equity"].cummax()
        dd_ser  = (max_eq - eq_df["equity"]) / max_eq * 100
        max_dd  = dd_ser.max()

        daily_ret = eq_df["equity"].pct_change().dropna()
        sharpe    = (
            daily_ret.mean() / daily_ret.std() * (252 ** 0.5)
            if len(daily_ret) > 1 and daily_ret.std() > 0
            else 0.0
        )

        td = pd.DataFrame(self.trades)
        if td.empty:
            total_trades = buys = sells = wins = 0
            win_rate = 0.0
        else:
            buys         = int((td["action"] == "BUY").sum())
            sells        = int((td["action"] == "SELL").sum())
            total_trades = len(td)
            wins         = int(((td["action"] == "SELL") & (td["pnl_pct"] > 0)).sum())
            win_rate     = wins / sells * 100 if sells else 0.0

        print("\n" + "=" * 70)
        print("BACKTEST REPORT — Alpaca Bot")
        print("=" * 70)
        print(f"Style:       {self.trading_style}")
        print(f"\nCAPITAL:")
        print(f"  Starting:    ${self.starting_cash:,.2f}")
        print(f"  Ending:      ${end_eq:,.2f}")
        print(f"  Return:      {total_ret:+.2f}%")
        print(f"  Max DD:      {max_dd:.2f}%")
        print(f"  Sharpe:      {sharpe:.2f}")
        print(f"\nTRADES:")
        print(f"  Total:       {total_trades}")
        print(f"  Buys:        {buys}")
        print(f"  Sells:       {sells}")
        print(f"  Win Rate:    {win_rate:.1f}%")

        # Symbol performance table
        if self.symbol_performance:
            print(f"\nSYMBOL PERFORMANCE:")
            perf_sorted = sorted(
                self.symbol_performance.items(),
                key=lambda x: x[1]["total_pnl"], reverse=True
            )
            for sym, perf in perf_sorted:
                if perf["trades"] == 0:
                    continue
                print(f"  {sym:<6} | trades={perf['trades']:<3} | "
                      f"WR={perf['win_rate']:.0f}% | P&L=${perf['total_pnl']:,.0f}")

        if not td.empty:
            print(f"\nRECENT TRADES (last 10):")
            for _, row in td.tail(10).iterrows():
                pnl_str = f"{row['pnl_pct']:+.2f}%" if row["action"] == "SELL" else ""
                print(f"  {row['date']} {row['action']:<4} {int(row['qty'])}x "
                      f"{row['symbol']:<6} @ ${row['price']:.2f}  {pnl_str} {row['reason']}")

        print(f"\nOPEN POSITIONS:")
        positions = self._get_positions()
        if positions:
            for sym, pos in positions.items():
                pnl = (pos["market_price"] - pos["avg_cost"]) / pos["avg_cost"] * 100
                tag = "ALGO" if sym in self._algo_managed_positions else "MANUAL"
                print(f"  {sym:<6} | qty={int(pos['qty'])} | "
                      f"entry=${pos['avg_cost']:.2f} | now=${pos['market_price']:.2f} "
                      f"| {pnl:+.2f}% | {tag}")
        else:
            print("  (none)")

        print("=" * 70)

        return {
            "start_equity":  start_eq,
            "end_equity":    end_eq,
            "return_pct":    total_ret,
            "max_drawdown":  max_dd,
            "sharpe":        sharpe,
            "total_trades":  total_trades,
            "buys":          buys,
            "sells":         sells,
            "win_rate":      win_rate,
        }


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Alpaca Bot Backtest")
    parser.add_argument("--start",   default="2024-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end",     default="2024-12-31", help="End date YYYY-MM-DD")
    parser.add_argument("--capital", type=float, default=11_000, help="Starting capital")
    parser.add_argument("--style",   default="all", choices=["all", "intraday", "swing"], help="Trading style to backtest")
    args = parser.parse_args()

    cfg     = BOT_Config()
    symbols = cfg.universe.core_symbols

    styles = ["INTRADAY", "SWING"] if args.style == "all" else [args.style.upper()]
    for style in styles:
        harness = AlpacaBacktestHarness(
            symbols       = symbols,
            start_date    = args.start,
            end_date      = args.end,
            starting_cash = args.capital,
            trading_style = style,
        )
        harness.run_backtest()
        harness.report()
