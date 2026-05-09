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
import math
import sys
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytz

sys.path.insert(0, str(Path(__file__).parent))

from new_bot_config import BOT_Config
from new_mock_alpaca import MockTradingClient, MockHistoricalDataClient


class AlpacaBacktestHarness:
    """
    Daily-bar replay harness implementing the same trading logic as
    main_alpaca.py, but driven by yfinance data and mock Alpaca clients.
    """

    def __init__(self, symbols: list[str], start_date: str, end_date: str,
                 starting_cash: float = 11_000.0, trading_style: str = "INTRADAY",
                 intraday_interval: str | None = None):
        self.config   = BOT_Config()
        self.et       = pytz.timezone("US/Eastern")

        self.start_date = pd.to_datetime(start_date).tz_localize(self.et)
        self.end_date   = pd.to_datetime(end_date).tz_localize(self.et)
        self.starting_cash = float(starting_cash)
        self.trading_style = str(trading_style or "INTRADAY").strip().upper()
        if self.trading_style not in {"INTRADAY", "SWING"}:
            self.trading_style = "INTRADAY"
        self.intraday_interval = str(
            intraday_interval
            or getattr(self.config.trading, "backtest_intraday_interval", "5m")
        ).strip()
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
        self._risk_day = self.start_date.date()
        self._session_start_equity = self.starting_cash

        self._algo_managed_positions:  set[str]            = set()
        self.entry_time:               dict[str, datetime] = {}
        self.entry_price:              dict[str, float]    = {}
        self.highest_price:            dict[str, float]    = {}
        self._entry_strategy_reason:   dict[str, str]      = {}
        self._entry_timestamps:        deque               = deque(maxlen=200)
        self._last_entry_time:         dict[str, datetime] = {}
        self._last_exit_time:          dict[str, datetime] = {}
        self._entry_side:              dict[str, str]      = {}
        self._daily_entry_counts:      dict[datetime.date, int] = {}

        self._strategy_weights: dict[str, float] = {
            "momentum":       0.40,   # primary trend signal
            "mean_reversion": 0.00,   # disabled — contradicts intraday momentum strategy
            "orb":            0.20,   # breakout confirmation
            "vwap_twap":      0.30,   # pullback/proximity confirmation
            "market_making":  0.05,
            "stat_arb":       0.00,
            "sentiment":      0.00,   # disabled in backtest (no live news)
        }
        self._min_portfolio_signal_score = float(
            getattr(self.config.trading, "entry_signal_score_threshold", 0.18)
        )

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
        self.loaded_intraday_symbols: set[str] = set()
        self._intraday_mode_available = False
        self.symbols_to_trade: list[str] = []

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

    def _is_tech_momentum_strategy(self) -> bool:
        return str(
            getattr(self.config.trading, "strategy_mode", "")
        ).strip().upper() == "TECH_MOMENTUM"

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

    def _load_intraday_symbol(self, symbol: str) -> pd.DataFrame:
        try:
            import yfinance as yf
            raw = yf.download(
                symbol,
                start=self.start_date.date(),
                end=(self.end_date + timedelta(days=1)).date(),
                interval=self.intraday_interval,
                progress=False,
                auto_adjust=False,
                prepost=False,
            )
            if raw.empty:
                return pd.DataFrame()
            clean = self._normalize_df(raw, self.et)
            if clean.empty:
                return pd.DataFrame()
            clean = clean.between_time("09:30", "16:00").copy()
            return clean
        except Exception as exc:
            print(f"  [INTRADAY ERROR] {symbol}: {exc}")
            return pd.DataFrame()

    def load_all_data(self):
        cfg = self.config.universe
        data_symbols = set(self._active_universe)
        data_symbols.update(cfg.core_symbols)
        if getattr(cfg, "dynamic_enabled", False):
            data_symbols.update(getattr(cfg, "candidate_symbols", []))
        data_symbols.update(getattr(cfg, "benchmark_symbols", ["SPY", "QQQ", "XLK"]))

        print(f"Loading data for {len(data_symbols)} symbols...")
        for symbol in sorted(data_symbols):
            df = self._load_symbol(symbol)
            if df.empty:
                print(f"  ✗ {symbol}")
                continue
            intraday_df = pd.DataFrame()
            if self.trading_style == "INTRADAY":
                intraday_df = self._load_intraday_symbol(symbol)
                if not intraday_df.empty:
                    self.loaded_intraday_symbols.add(symbol)
            self.data_client.load_data(
                symbol,
                df,
                hourly_df=intraday_df if not intraday_df.empty else None,
            )
            self.loaded_symbols.add(symbol)
            intraday_msg = (
                f", {len(intraday_df)} {self.intraday_interval} bars"
                if not intraday_df.empty else ""
            )
            print(f"  ✓ {symbol}: {len(df)} daily bars{intraday_msg}")

        self._active_universe = [s for s in self._active_universe if s in self.loaded_symbols]
        benchmark_symbols = set(getattr(cfg, "benchmark_symbols", ["SPY", "QQQ", "XLK"]))
        self._intraday_mode_available = (
            self.trading_style == "INTRADAY"
            and bool(self.loaded_intraday_symbols)
            and bool(benchmark_symbols.intersection(self.loaded_intraday_symbols))
        )
        if self.trading_style == "INTRADAY" and not self._intraday_mode_available:
            print(
                f"  ! No usable {self.intraday_interval} intraday bars loaded; "
                "falling back to daily-bar intraday proxy."
            )

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

    def _get_intraday_slice(self, symbol: str, up_to) -> pd.DataFrame:
        df = self.data_client._hourly_data.get(symbol, pd.DataFrame())
        if df.empty:
            return pd.DataFrame()
        ts = self._to_ts(up_to)
        return df[df.index <= ts].copy()

    def _get_intraday_bar(self, symbol: str, up_to) -> dict | None:
        sl = self._get_intraday_slice(symbol, up_to)
        if sl.empty:
            return None
        row = sl.iloc[-1]
        return {
            "symbol": symbol, "date": sl.index[-1],
            "open": float(row["open"]), "high": float(row["high"]),
            "low": float(row["low"]), "close": float(row["close"]),
            "volume": int(row["volume"]),
        }

    def _session_slice(self, df: pd.DataFrame, current_ts) -> pd.DataFrame:
        if df.empty:
            return pd.DataFrame()
        ts = self._to_ts(current_ts)
        return df[df.index.map(lambda x: x.date() == ts.date())].copy()

    def _session_vwap_from_slice(self, session: pd.DataFrame) -> float:
        if session.empty:
            return 0.0
        vol = session["volume"].replace(0, pd.NA)
        vwap = ((session["close"] * vol).cumsum() / vol.cumsum()).iloc[-1]
        if pd.isna(vwap):
            return self._safe_float(session["close"].iloc[-1])
        return float(vwap)

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
        if self._intraday_mode_available:
            intraday_bar = self._get_intraday_bar(symbol, current_ts)
            if intraday_bar:
                return float(intraday_bar["close"])
        bar = self._get_bar(symbol, current_ts)
        if not bar:
            return 0.0
        if self._style_settings["flatten_eod"]:
            return float(bar["open"])
        return float(bar["close"])

    def _advance_prices(self, ts):
        for symbol in self.loaded_symbols:
            bar = (
                self._get_intraday_bar(symbol, ts)
                if self._intraday_mode_available and symbol in self.loaded_intraday_symbols
                else self._get_bar(symbol, ts)
            )
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

    def _benchmark_return(self, symbol: str, current_ts, lookback: int,
                          exclude_current: bool = False) -> float:
        sl = self._get_bar_slice(symbol, current_ts)
        if exclude_current and len(sl) > 1:
            sl = sl.iloc[:-1]
        if len(sl) < lookback + 1:
            return 0.0
        current = self._safe_float(sl["close"].iloc[-1])
        prior = self._safe_float(sl["close"].iloc[-(lookback + 1)])
        if current <= 0 or prior <= 0:
            return 0.0
        return (current / prior) - 1.0

    def _benchmark_intraday_return(self, symbol: str, current_ts) -> float:
        sl = self._get_intraday_slice(symbol, current_ts)
        session = self._session_slice(sl, current_ts)
        if len(session) < 2:
            return 0.0
        current = self._safe_float(session["close"].iloc[-1])
        session_open = self._safe_float(session["open"].iloc[0])
        if current <= 0 or session_open <= 0:
            return 0.0
        return (current / session_open) - 1.0

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

    def _get_tech_momentum_universe(self) -> list[str]:
        cfg = self.config.universe
        excludes = set(getattr(cfg, "exclude_symbols", []))
        symbols = list(getattr(cfg, "candidate_symbols", [])) or list(getattr(cfg, "core_symbols", []))
        seen: set[str] = set()
        return [
            str(symbol).upper()
            for symbol in symbols
            if str(symbol).upper() not in excludes
            and str(symbol).upper() in self.loaded_symbols
            and not (str(symbol).upper() in seen or seen.add(str(symbol).upper()))
        ]

    def _score_tech_momentum_symbol(self, ticker: str, current_ts) -> tuple[float, float] | None:
        cfg = self.config.trading
        lookback = int(getattr(cfg, "tech_momentum_lookback_days", 126))
        min_dollar_volume = float(getattr(cfg, "tech_momentum_min_dollar_volume", 10_000_000))
        sl = self._get_bar_slice(ticker, current_ts)
        if sl.empty or len(sl) < lookback + 1:
            return None
        close = sl["close"].dropna()
        if len(close) < lookback + 1:
            return None
        current_price = self._safe_float(close.iloc[-1])
        lookback_price = self._safe_float(close.iloc[-(lookback + 1)])
        if current_price <= 0 or lookback_price <= 0:
            return None
        recent = sl.tail(20).copy()
        avg_dollar_volume = self._safe_float((recent["close"] * recent["volume"]).mean())
        if avg_dollar_volume < min_dollar_volume:
            return None
        return (current_price / lookback_price) - 1.0, current_price

    def rebalance_tech_momentum(self, current_ts):
        scored = []
        for ticker in self._get_tech_momentum_universe():
            result = self._score_tech_momentum_symbol(ticker, current_ts)
            if result is None:
                continue
            momentum, price = result
            scored.append((ticker, momentum, price))

        scored.sort(key=lambda item: item[1], reverse=True)
        top_count = int(getattr(self.config.trading, "tech_momentum_top_count", 10))
        selected = [ticker for ticker, _, _ in scored[:top_count]]
        self.symbols_to_trade = selected
        selected_set = set(selected)
        positions = self._get_positions()

        for ticker, pos in list(positions.items()):
            if ticker not in self._algo_managed_positions or ticker in selected_set:
                continue
            qty = float(pos.get("qty", 0))
            price = self._safe_float(pos.get("market_price"))
            if qty > 0 and self._place_market_sell(ticker, qty, "weekly_momentum_rebalance", price):
                avg_entry = self._safe_float(pos.get("avg_cost"), price)
                pnl_pct = ((price / avg_entry) - 1.0) * 100 if avg_entry > 0 else 0.0
                self._record_trade(current_ts, ticker, "SELL", qty, price, pnl_pct, "weekly_momentum_rebalance")
                self._algo_managed_positions.discard(ticker)
                self.highest_price.pop(ticker, None)
                self.entry_price.pop(ticker, None)
                self.entry_time.pop(ticker, None)

        if not selected:
            return

        equity = self.net_liquidation
        target_exposure = float(getattr(self.config.trading, "tech_momentum_target_exposure", 1.0))
        target_value = equity * target_exposure / len(selected)
        positions = self._get_positions()
        now = self._to_ts(current_ts).to_pydatetime()

        for ticker, _, price in scored[:top_count]:
            pos = positions.get(ticker)
            current_value = 0.0
            if pos:
                current_value = self._safe_float(pos.get("qty")) * self._safe_float(pos.get("market_price"), price)
            delta_value = target_value - current_value
            if pos and delta_value <= max(25.0, price):
                if pos:
                    self._algo_managed_positions.add(ticker)
                    self.highest_price[ticker] = max(self.highest_price.get(ticker, price), price)
                continue
            if not pos and delta_value <= 25.0:
                continue
            qty = round(delta_value / price, 6)
            if qty <= 0:
                continue
            if self._place_market_buy(ticker, qty, price):
                self._algo_managed_positions.add(ticker)
                self.entry_time.setdefault(ticker, now)
                self.entry_price.setdefault(ticker, price)
                self.highest_price[ticker] = max(self.highest_price.get(ticker, price), price)
                self._entry_strategy_reason[ticker] = "tech_universe_126d_momentum"
                self._entry_side[ticker] = "LONG"
                self._record_trade(current_ts, ticker, "BUY", qty, price, 0.0, "weekly_momentum_rebalance")

    def check_tech_momentum_trailing_stops(self, current_ts):
        stop_pct = float(getattr(self.config.trading, "tech_momentum_stop_loss_pct", 0.05))
        positions = self._get_positions()
        for ticker in list(self._algo_managed_positions):
            pos = positions.get(ticker)
            if not pos:
                self.highest_price.pop(ticker, None)
                continue
            qty = float(pos.get("qty", 0))
            price = self._safe_float(pos.get("market_price"))
            if qty <= 0 or price <= 0:
                continue
            self.highest_price[ticker] = max(self.highest_price.get(ticker, price), price)
            stop_price = self.highest_price[ticker] * (1 - stop_pct)
            if price < stop_price and self._place_market_sell(ticker, qty, "tech_momentum_trailing_stop", price):
                avg_entry = self._safe_float(pos.get("avg_cost"), price)
                pnl_pct = ((price / avg_entry) - 1.0) * 100 if avg_entry > 0 else 0.0
                self._record_trade(current_ts, ticker, "SELL", qty, price, pnl_pct, "tech_momentum_trailing_stop")
                self._algo_managed_positions.discard(ticker)
                self.highest_price.pop(ticker, None)
                self.entry_price.pop(ticker, None)
                self.entry_time.pop(ticker, None)

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
        use_intraday = self._intraday_mode_available and ticker in self.loaded_intraday_symbols
        sl = self._get_intraday_slice(ticker, current_ts) if use_intraday else self._get_bar_slice(ticker, current_ts)
        if not use_intraday and self._style_settings["flatten_eod"] and len(sl) > 1:
            sl = sl.iloc[:-1]
        if sl.empty or len(sl) < 52:
            return None

        if use_intraday:
            w = sl.copy()
            ema_fast = self._ema(w["close"], 12)
            ema_slow = self._ema(w["close"], 26)
            w["macd"] = ema_fast - ema_slow
            w["macd_signal"] = self._ema(w["macd"], 9)
            w["rsi"] = self._rsi(w["close"], 14)
            w["ema_50"] = self._ema(w["close"], 50)
            sl = w

        latest = sl.iloc[-1]
        prev_close = float(sl["close"].iloc[-2]) if len(sl) >= 2 else float(latest["close"])

        bb_mid  = sl["close"].rolling(20).mean().iloc[-1]
        bb_std  = sl["close"].rolling(20).std(ddof=0).iloc[-1]
        zscore  = (0.0 if pd.isna(bb_mid) or pd.isna(bb_std) or bb_std == 0
                   else float((latest["close"] - bb_mid) / bb_std))

        vol_sma = sl["volume"].rolling(20).mean().iloc[-1]
        vol_ratio = 1.0 if pd.isna(vol_sma) or vol_sma <= 0 else float(latest["volume"] / vol_sma)

        if use_intraday:
            session = self._session_slice(sl, current_ts)
            vwap = self._session_vwap_from_slice(session)
            orb_bars = max(1, int(getattr(self.config.trading, "intraday_orb_minutes", 30)) // 5)
            opening_range = session.head(orb_bars) if not session.empty else pd.DataFrame()
            orb_high = float(opening_range["high"].max()) if not opening_range.empty else float(latest["high"])
            orb_low = float(opening_range["low"].min()) if not opening_range.empty else float(latest["low"])
            session_open = float(session["open"].iloc[0]) if not session.empty else prev_close
            daily_return_1 = ((float(latest["close"]) / session_open) - 1.0) if session_open > 0 else 0.0
        else:
            vwap_n = (sl["close"] * sl["volume"]).rolling(20).sum().iloc[-1]
            vwap_d = sl["volume"].rolling(20).sum().iloc[-1]
            vwap   = (float(latest["close"]) if pd.isna(vwap_d) or vwap_d <= 0
                      else float(vwap_n / vwap_d))
            orb_high = float(sl["high"].tail(20).max())
            orb_low = float(sl["low"].tail(20).min())
            daily_return_1 = ((float(latest["close"]) / prev_close) - 1.0) if prev_close > 0 else 0.0

        needed = {"close": "price", "macd": "macd", "macd_signal": "macd_signal",
                  "rsi": "rsi", "ema_50": "ema_50"}
        out = {}
        for col, key in needed.items():
            val = latest.get(col)
            if val is None or pd.isna(val):
                return None
            out[key] = float(val)

        range_span = max(float(latest["high"] - latest["low"]), 1e-9)
        close_location = (float(latest["close"]) - float(latest["low"])) / range_span
        benchmark_symbol = str(
            getattr(self.config.trading, "intraday_benchmark_symbol", "QQQ")
        ).upper()
        benchmark_return_1 = (
            self._benchmark_intraday_return(benchmark_symbol, current_ts)
            if use_intraday else
            self._benchmark_return(
                benchmark_symbol, current_ts, 1, exclude_current=self._style_settings["flatten_eod"]
            )
        )

        out.update({"vwap": vwap, "zscore": zscore, "volume_ratio": vol_ratio,
                    "opening_range_high": orb_high, "opening_range_low": orb_low,
                    "close_location": close_location, "daily_return_1": daily_return_1,
                    "benchmark_return_1": benchmark_return_1,
                    "session_vwap": vwap,
                "atr_14": 0.0})
        return out

    def _compute_swing_indicators(self, ticker: str, current_ts) -> dict | None:
        sl = self._get_bar_slice(ticker, current_ts)
        if sl.empty or len(sl) < 55:
            return None

        ema20 = self._ema(sl["close"], 20)
        latest = sl.iloc[-1]
        prev = sl.iloc[-2]
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
        ret_20 = self._benchmark_return(ticker, current_ts, 20, exclude_current=False)
        ret_60 = self._benchmark_return(ticker, current_ts, 60, exclude_current=False)
        benchmark_symbol = str(getattr(self.config.trading, "swing_benchmark_symbol", "XLK")).upper()
        benchmark_return_20 = self._benchmark_return(benchmark_symbol, current_ts, 20, exclude_current=False)
        benchmark_return_60 = self._benchmark_return(benchmark_symbol, current_ts, 60, exclude_current=False)

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
                    "ema_20": float(ema20.iloc[-1]),
                    "ema_20_prev": float(ema20.iloc[-2]),
                    "return_20": ret_20,
                    "return_60": ret_60,
                    "benchmark_return_20": benchmark_return_20,
                    "benchmark_return_60": benchmark_return_60,
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

    def _can_open_new_entry(self, ticker: str, now: datetime) -> tuple[bool, str]:
        cfg = self.config.trading
        max_daily_entries = int(getattr(cfg, "max_new_entries_per_day", 2))
        entries_today = self._daily_entry_counts.get(now.date(), 0)
        if entries_today >= max_daily_entries:
            return False, f"daily_entry_cap({entries_today}/{max_daily_entries})"

        min_gap_hours = int(getattr(cfg, "min_hours_between_symbol_entries", 4))
        if min_gap_hours > 0:
            last_entry = self._last_entry_time.get(ticker)
            if last_entry and now - last_entry < timedelta(hours=min_gap_hours):
                return False, f"recent_entry_gap<{min_gap_hours}h"

        reentry_gap_hours = int(getattr(cfg, "reentry_cooldown_after_exit_hours", 6))
        if reentry_gap_hours > 0:
            last_exit = self._last_exit_time.get(ticker)
            if last_exit and now - last_exit < timedelta(hours=reentry_gap_hours):
                return False, f"recent_exit_gap<{reentry_gap_hours}h"

        return True, ""

    def _compute_portfolio_signal(self, ticker: str, ind: dict) -> tuple[float, str]:
        price        = ind["price"]
        macd         = ind["macd"]
        macd_sig     = ind["macd_signal"]
        rsi_val      = ind["rsi"]
        ema_val      = ind["ema_50"]
        vwap         = ind["vwap"]
        volume_ratio = ind["volume_ratio"]
        orb_high     = ind["opening_range_high"]
        close_location = float(ind.get("close_location", 0.5))
        daily_return_1 = float(ind.get("daily_return_1", 0.0))

        comp: dict[str, float] = {k: 0.0 for k in self._strategy_weights}

        cfg = self.config.trading
        if self._is_swing_style():
            ema_20 = float(ind.get("ema_20", price))
            ema_20_prev = float(ind.get("ema_20_prev", ema_20))
            rel_20 = float(ind.get("return_20", 0.0)) - float(ind.get("benchmark_return_20", 0.0))
            rel_60 = float(ind.get("return_60", 0.0)) - float(ind.get("benchmark_return_60", 0.0))
            max_extension = float(getattr(cfg, "swing_max_ema20_extension_pct", 0.06))
            min_rel_20 = float(getattr(cfg, "swing_min_relative_strength_20d", 0.01))
            min_rel_60 = float(getattr(cfg, "swing_min_relative_strength_60d", 0.0))
            min_volume = float(getattr(cfg, "swing_min_volume_ratio", 0.9))
            extension = (price / max(ema_20, 1e-9)) - 1.0

            if not (
                price > ema_20 > ema_val
                and ema_20 > ema_20_prev
                and macd > macd_sig
                and 45 <= rsi_val <= 68
                and -0.01 <= extension <= max_extension
                and rel_20 >= min_rel_20
                and rel_60 >= min_rel_60
                and volume_ratio >= min_volume
            ):
                return 0.0, (
                    f"swing_filter_fail(rel20={rel_20:.2%}, rel60={rel_60:.2%}, "
                    f"ext={extension:.2%}, rsi={rsi_val:.1f})"
                )

            rel_score = min(1.0, max(0.0, rel_20 / 0.08))
            trend_score = min(1.0, max(0.0, (price / max(ema_val, 1e-9) - 1.0) / 0.12))
            pullback_score = max(0.0, 1.0 - max(0.0, extension) / max(max_extension, 1e-9))
            volume_score = min(1.0, max(0.0, (volume_ratio - min_volume) / 0.8))
            score = (
                0.40 * rel_score
                + 0.25 * trend_score
                + 0.25 * pullback_score
                + 0.10 * volume_score
            )
            return score, (
                f"swing_rel_xlk:{rel_score:.2f}, trend:{trend_score:.2f}, "
                f"pullback:{pullback_score:.2f}, rel20={rel_20:.2%}"
            )

        # RSI window: healthy momentum zone, not overbought (config: 55-72)
        momentum_rsi_min = float(getattr(cfg, "momentum_rsi_min", 55))
        momentum_rsi_max = float(getattr(cfg, "momentum_rsi_max", 72))
        orb_min_volume_ratio = float(getattr(cfg, "orb_min_volume_ratio", 1.05))
        min_relative_strength = float(
            getattr(cfg, "intraday_min_relative_strength_pct", 0.0015)
        )
        max_vwap_distance = float(getattr(cfg, "intraday_max_vwap_distance_pct", 0.018))
        min_volume_ratio = float(getattr(cfg, "intraday_min_volume_ratio", 1.05))
        max_close_location = float(getattr(cfg, "intraday_max_close_location", 0.72))
        require_vwap_component = bool(getattr(cfg, "intraday_require_vwap_component", True))
        relative_strength = daily_return_1 - float(ind.get("benchmark_return_1", 0.0))

        # Percentage-based gaps — scale-consistent between daily and hourly bars
        volume_support  = min(1.0, max(0.0, (volume_ratio - 1.0) / 0.6))
        trend_gap       = max(0.0, macd - macd_sig)
        ema_gap         = max(0.0, (price / max(ema_val, 1e-9)) - 1.0)
        vwap_dist       = (price / max(vwap, 1e-9)) - 1.0

        # ── MOMENTUM: confirmed trend + day-direction confirmation ────
        # RSI 55-72: relaxed from [60,78] — catches early momentum builds,
        #            excludes overbought chasing.
        if (
            price > ema_val
            and macd > macd_sig
            and momentum_rsi_min <= rsi_val <= momentum_rsi_max
            and volume_ratio >= min_volume_ratio
            and relative_strength >= min_relative_strength
            and close_location >= 0.50              # relaxed from 0.60
            and close_location <= max_close_location
        ):
            comp["momentum"] = min(
                1.0,
                (0.50 * math.tanh(trend_gap * 6.0))
                + (0.30 * math.tanh(ema_gap * 80.0))
                + (0.20 * volume_support),
            )

        # ── VWAP/TWAP: trend-direction confirmation ───────────────────
        # mean_reversion removed (weight = 0) — buying falling stocks with intraday
        # EOD exits is structurally loss-making. Weight redistributed to momentum/orb.
        if (
            price > vwap
            and macd > macd_sig
            and volume_ratio >= min_volume_ratio
            and relative_strength >= min_relative_strength
            and 0 <= vwap_dist <= max_vwap_distance
            and close_location >= 0.50
            and close_location <= max_close_location
        ):
            vwap_gap = max(0.0, vwap_dist)
            proximity = max(0.0, 1.0 - (vwap_gap / max(max_vwap_distance, 1e-9)))
            comp["vwap_twap"] = min(
                1.0,
                (0.55 * proximity)
                + (0.15 * math.tanh(vwap_gap * 60.0))
                + (0.30 * volume_support),
            )

        # ── ORB: genuine breakout above opening range ──────────────────
        if price > orb_high and volume_ratio >= orb_min_volume_ratio:
            comp["orb"] = min(1.0, (price / max(orb_high, 1e-9)) - 1.0 + 0.5)

        if require_vwap_component and comp["vwap_twap"] <= 0:
            return 0.0, (
                f"await_vwap_pullback(vwap_dist={vwap_dist:.2%}, "
                f"bar_loc={close_location:.2f}, rel_qqq={relative_strength:.2%})"
            )

        # No compound gate: with mean_reversion removed and redistributed weights
        # (momentum 0.40, vwap 0.30, orb 0.20), no single component alone reaches
        # the 0.45 threshold — confluence is enforced by the scoring arithmetic.

        weighted = 0.0
        active: list[str] = []
        for name, score in comp.items():
            weight = self._strategy_weights.get(name, 0.0)
            if score > 0 and weight > 0:
                active.append(f"{name}:{score:.2f}")
            weighted += weight * score

        reason = ", ".join(active) if active else "none"
        if active:
            reason = f"{reason}, rel_qqq={relative_strength:.2%}"
        return weighted, reason

    def _compute_bearish_portfolio_signal(self, ticker: str, ind: dict) -> tuple[float, str]:
        price = ind["price"]
        macd = ind["macd"]
        macd_sig = ind["macd_signal"]
        rsi_val = ind["rsi"]
        ema_val = ind["ema_50"]
        vwap = ind["vwap"]
        volume_ratio = ind["volume_ratio"]
        orb_low = ind["opening_range_low"]
        close_location = float(ind.get("close_location", 0.5))

        cfg = self.config.trading
        # Relaxed bear RSI range: [25, 55] vs prior [28, 48]
        # — Lower bound catches extreme selloffs, upper bound catches confirmed downtrends
        bear_rsi_min = float(getattr(cfg, "bear_rsi_min", 25))
        bear_rsi_max = float(getattr(cfg, "bear_rsi_max", 55))
        bear_orb_min_volume_ratio = float(getattr(cfg, "bear_orb_min_volume_ratio", 1.05))

        volume_support  = min(1.0, max(0.0, (volume_ratio - 1.0) / 0.6))
        trend_gap       = max(0.0, macd_sig - macd)
        ema_gap         = max(0.0, (ema_val / max(price, 1e-9)) - 1.0)
        vwap_dist_below = max(0.0, (vwap / max(price, 1e-9)) - 1.0)

        comp: dict[str, float] = {k: 0.0 for k in self._strategy_weights}

        # ── BEAR MOMENTUM: confirmed downtrend, not in extreme oversold ──
        # Removed: daily_return_1 <= -0.005 (chasing) and close_location <= 0.40 (chasing).
        # RSI [25, 55]: relaxed from [28, 48] — catches more confirmed bear setups.
        if (
            price < ema_val
            and macd < macd_sig
            and bear_rsi_min <= rsi_val <= bear_rsi_max
            and volume_ratio >= 1.0
            and close_location >= 0.18               # not short-selling at extreme bottom of bar
        ):
            comp["momentum"] = min(
                1.0,
                (0.50 * math.tanh(trend_gap * 6.0))
                + (0.30 * math.tanh(ema_gap * 80.0))
                + (0.20 * volume_support),
            )

        # ── BEAR VWAP: trend-direction short confirmation ──────────────
        if (
            price < vwap
            and macd < macd_sig
            and rsi_val <= 52
            and volume_ratio >= 0.85
        ):
            comp["vwap_twap"] = min(
                1.0,
                (0.70 * math.tanh(vwap_dist_below * 60.0))
                + (0.30 * volume_support),
            )

        # ── BEAR ORB: breakdown below opening range ────────────────────
        if price < orb_low and volume_ratio >= bear_orb_min_volume_ratio:
            comp["orb"] = min(1.0, ((max(orb_low, 1e-9) / price) - 1.0) + 0.5)

        # ── COMPOUND CONFIRMATION GATE ─────────────────────────────────
        active_count = sum(1 for s in comp.values() if s > 0)
        if active_count < 2:
            return 0.0, "insufficient_bear_confirmation"

        weighted = 0.0
        active: list[str] = []
        for name, score in comp.items():
            weight = self._strategy_weights.get(name, 0.0)
            if score > 0 and weight > 0:
                active.append(f"bear_{name}:{score:.2f}")
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
        risk = self.config.risk
        available_cash = self.cash
        min_notional = float(getattr(risk, "min_entry_notional", 2500))
        max_position_pct = float(getattr(risk, "max_position_size_pct", 0.25))
        max_position_value = float(getattr(risk, "max_position_value", 6500))
        target_value = min(available_cash * max_position_pct, max_position_value)
        if target_value < min_notional:
            return 0
        qty = int(target_value / price)
        if qty <= 0:
            return 0
        fee        = self._estimate_fee(qty, price)
        total_cost = qty * price + fee
        if total_cost > target_value:
            qty        = int((target_value - fee) / price)
            total_cost = qty * price + self._estimate_fee(qty, price)
        return qty if qty > 0 and total_cost >= min_notional else 0

    def _entry_score_threshold(self) -> float:
        cfg = self.config.trading
        if self.trading_style == "INTRADAY":
            return float(getattr(cfg, "intraday_entry_score_threshold", self._min_portfolio_signal_score))
        if self.trading_style == "SWING":
            return float(getattr(cfg, "swing_entry_score_threshold", self._min_portfolio_signal_score))
        if self.market_regime == "BULL":
            return float(getattr(cfg, "bull_entry_score_threshold", self._min_portfolio_signal_score))
        if self.market_regime == "NEUTRAL":
            return float(getattr(cfg, "neutral_entry_score_threshold", self._min_portfolio_signal_score))
        if self.market_regime == "BEAR":
            return float(getattr(cfg, "bear_entry_score_threshold", self._min_portfolio_signal_score))
        return self._min_portfolio_signal_score

    def _place_market_buy(self, ticker: str, qty: int, fill_price: float | None = None) -> bool:
        from new_mock_alpaca import _OrderStatus
        if fill_price is not None and fill_price > 0:
            self.trading_client.update_price(ticker, fill_price)
        req   = type("R", (), {"symbol": ticker, "qty": qty, "side": "buy",
                               "time_in_force": "day"})()
        order = self.trading_client.submit_order(req)
        return order.status == _OrderStatus.FILLED

    def _place_market_sell(self, ticker: str, qty: int, reason: str = "", fill_price: float | None = None) -> bool:
        from new_mock_alpaca import _OrderStatus
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
                      price: float, pnl_pct: float = 0.0, reason: str = "",
                      session_vwap: float = 0.0):
        vwap = self._safe_float(session_vwap)
        vwap_alpha = 0.0
        if vwap > 0 and price > 0:
            if action in {"BUY", "BUY_TO_COVER"}:
                vwap_alpha = ((vwap - price) / vwap) * 100
            elif action in {"SELL", "SELL_SHORT"}:
                vwap_alpha = ((price - vwap) / vwap) * 100
        self.trades.append({
            "date": pd.to_datetime(date_value).date(),
            "symbol": symbol, "action": action,
            "qty": float(qty), "price": float(price),
            "pnl_pct": float(pnl_pct),
            "reason": reason,
            "session_vwap": vwap,
            "vwap_alpha_pct": float(vwap_alpha),
        })

    def _resolve_intraday_ohlc_exit(self, pos: dict, day_bar: dict | None) -> tuple[float, str]:
        current_price = float(day_bar["close"]) if day_bar else float(pos.get("market_price", 0.0))
        avg_entry = float(pos.get("avg_cost", current_price) or current_price)
        if not day_bar or avg_entry <= 0:
            return current_price, "eod_flatten"

        side = str(pos.get("side", "LONG")).upper()
        stop_pct = float(self._style_settings["stop_loss_pct"])
        take_pct = float(self._style_settings["take_profit_pct"])
        high = float(day_bar["high"])
        low = float(day_bar["low"])

        if side == "LONG":
            stop_price = avg_entry * (1 - stop_pct)
            take_price = avg_entry * (1 + take_pct)
            if low <= stop_price:
                return stop_price, "intraday_stop_loss"
            if high >= take_price:
                return take_price, "intraday_take_profit"
        else:
            stop_price = avg_entry * (1 + stop_pct)
            take_price = avg_entry * (1 - take_pct)
            if high >= stop_price:
                return stop_price, "intraday_stop_loss"
            if low <= take_price:
                return take_price, "intraday_take_profit"

        return current_price, "eod_flatten"

    def _roll_daily_risk_baseline(self, now: datetime, current_equity: float):
        today = now.date()
        if today != self._risk_day:
            self._risk_day = today
            self._session_start_equity = current_equity
            self._trading_enabled = True

    # =========================================================================
    #  SIGNAL EVALUATION (entry + exit)
    # =========================================================================
    def evaluate_signals(self, current_ts):
        now = current_ts.to_pydatetime()
        if now.date() != self._risk_day:
            self._risk_day = now.date()
            self._trading_enabled = True

        if not self._trading_enabled:
            return

        current_equity = self.net_liquidation
        if current_equity <= 0:
            return

        self._roll_daily_risk_baseline(now, current_equity)

        daily_loss = self._session_start_equity - current_equity
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

        positions = self._get_positions()
        algo_pos  = {t: p for t, p in positions.items() if t in self._algo_managed_positions}

        # ── EXIT ───────────────────────────────────────────────────────
        for ticker, pos in list(algo_pos.items()):
            current_price = pos["market_price"]
            if current_price <= 0:
                continue

            avg_entry  = pos["avg_cost"]
            qty        = int(pos["qty"])
            side       = str(pos.get("side", "LONG")).upper()
            direction  = 1 if side == "LONG" else -1
            pnl_pct    = (direction * (current_price - avg_entry) / avg_entry) if avg_entry > 0 else 0
            pnl_dollar = direction * qty * (current_price - avg_entry)
            held       = now - self.entry_time.get(ticker, now)

            if side == "LONG":
                self.highest_price[ticker] = max(
                    self.highest_price.get(ticker, current_price), current_price
                )
            else:
                self.highest_price[ticker] = min(
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

            if side == "LONG":
                trail_activation = avg_entry * (1 + cfg["trailing_activation_pct"])
                trailing_stop = self.highest_price.get(ticker, current_price) * (1 - cfg["trailing_stop_pct"])
                if self.highest_price.get(ticker, current_price) >= trail_activation and current_price <= trailing_stop:
                    should_exit, reason = True, "trailing_stop"
            else:
                trail_activation = avg_entry * (1 - cfg["trailing_activation_pct"])
                trailing_stop = self.highest_price.get(ticker, current_price) * (1 + cfg["trailing_stop_pct"])
                if self.highest_price.get(ticker, current_price) <= trail_activation and current_price >= trailing_stop:
                    should_exit, reason = True, "trailing_stop"

            if pnl_pct <= -0.05:
                should_exit, reason = True, "gap_protection"

            # ── SIGNAL-REVERSAL EXIT ───────────────────────────────────
            # If the entry signal (MACD) has reversed while we hold a profit,
            # exit now rather than waiting for stop/time.  Locks in gains when
            # the trend that triggered entry has demonstrably ended.
            if not should_exit and pnl_pct >= 0.003:
                live_ind = self._compute_symbol_indicators(ticker, current_ts)
                if live_ind:
                    if side == "LONG" and live_ind["macd"] < live_ind["macd_signal"]:
                        should_exit, reason = True, "signal_reversal"
                    elif side == "SHORT" and live_ind["macd"] > live_ind["macd_signal"]:
                        should_exit, reason = True, "signal_reversal"

            if not should_exit:
                continue

            closed = (
                self._place_market_sell(ticker, qty, reason, current_price)
                if side == "LONG"
                else self._place_market_buy(ticker, qty, current_price)
            )
            if closed:
                exit_ind = self._compute_symbol_indicators(ticker, current_ts)
                session_vwap = self._safe_float((exit_ind or {}).get("session_vwap", 0.0))
                self._algo_managed_positions.discard(ticker)
                self._entry_strategy_reason.pop(ticker, None)
                self._entry_side.pop(ticker, None)
                self._last_exit_time[ticker] = now
                self._update_symbol_performance(ticker, pnl_dollar)
                exit_action = "SELL" if side == "LONG" else "BUY_TO_COVER"
                self._record_trade(
                    now, ticker, exit_action, qty, current_price,
                    pnl_pct * 100, reason, session_vwap=session_vwap
                )

                msg = (f"{now.strftime('%Y-%m-%d')} {exit_action} {ticker} - Qty: {qty} "
                       f"@ ${current_price:.2f} | P&L: ${pnl_dollar:.2f} ({pnl_pct:.2%})")
                self.trade_history.append(msg)
                (self.winning_trades if pnl_dollar > 0 else self.losing_trades).append(msg)

        # ── ENTRY ──────────────────────────────────────────────────────
        if self.market_regime == "BEAR" and not bool(getattr(self.config.trading, "bear_entry_enabled", False)):
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
        min_notional = float(getattr(self.config.risk, "min_entry_notional", 2500))
        if algo_count >= max_allowed or self.cash < min_notional:
            return

        if self._is_swing_style() and self._trade_count_this_week(now) >= self.config.trading.max_weekly_trades:
            return

        candidates: list[tuple] = []
        required_score = self._entry_score_threshold()
        for ticker in self._active_universe:
            if ticker == "SPY" or ticker in positions:
                continue
            if self._is_swing_style() and self._symbol_on_cooldown(ticker, now):
                continue
            allowed, _ = self._can_open_new_entry(ticker, now)
            if not allowed:
                continue
            ind = self._compute_symbol_indicators(ticker, current_ts)
            if not ind or ind["price"] <= 0:
                continue
            if self.market_regime == "BEAR":
                score, reason = self._compute_bearish_portfolio_signal(ticker, ind)
            else:
                score, reason = self._compute_portfolio_signal(ticker, ind)
            if score >= required_score:
                candidates.append((ticker, score, ind["price"], reason, self._safe_float(ind.get("session_vwap", 0.0))))

        if not candidates:
            return

        candidates.sort(key=lambda x: x[1], reverse=True)
        best_ticker, best_score, current_price, reason, session_vwap = candidates[0]

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

        opened = (
            self._place_market_sell(best_ticker, qty, "short_entry", current_price)
            if self.market_regime == "BEAR"
            else self._place_market_buy(best_ticker, qty, current_price)
        )
        if opened:
            self._algo_managed_positions.add(best_ticker)
            self.entry_time[best_ticker]             = now
            self.entry_price[best_ticker]            = current_price
            self.highest_price[best_ticker]          = current_price
            self._entry_strategy_reason[best_ticker] = reason
            self._entry_timestamps.append(now)
            self._last_entry_time[best_ticker]       = now
            self._entry_side[best_ticker]            = "SHORT" if self.market_regime == "BEAR" else "LONG"
            self._daily_entry_counts[now.date()] = self._daily_entry_counts.get(now.date(), 0) + 1

            entry_action = "SELL_SHORT" if self.market_regime == "BEAR" else "BUY"
            self._record_trade(
                now, best_ticker, entry_action, qty, current_price,
                0.0, reason, session_vwap=session_vwap
            )
            self.trade_history.append(
                f"{now.strftime('%Y-%m-%d')} {entry_action} {best_ticker} - Qty: {qty} "
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
            intraday_bar = self._get_intraday_bar(ticker, current_ts) if self._intraday_mode_available else None
            day_bar = intraday_bar or self._get_bar(ticker, current_ts)
            if self._intraday_mode_available and intraday_bar:
                current_price, reason = float(intraday_bar["close"]), "eod_flatten"
            else:
                current_price, reason = self._resolve_intraday_ohlc_exit(pos, day_bar)
            side = str(pos.get("side", "LONG")).upper()
            closed = (
                self._place_market_sell(ticker, qty, reason, current_price)
                if side == "LONG"
                else self._place_market_buy(ticker, qty, current_price)
            )
            if closed:
                self._algo_managed_positions.discard(ticker)
                self._entry_strategy_reason.pop(ticker, None)
                self._entry_side.pop(ticker, None)
                self._last_exit_time[ticker] = now
                avg_entry = float(pos.get("avg_cost", current_price) or current_price)
                direction = 1 if side == "LONG" else -1
                pnl_dollar = direction * qty * (current_price - avg_entry)
                pnl_pct = ((direction * (current_price - avg_entry) / avg_entry) * 100) if avg_entry > 0 else 0.0
                exit_action = "SELL" if side == "LONG" else "BUY_TO_COVER"
                self._update_symbol_performance(ticker, pnl_dollar)
                exit_ind = self._compute_symbol_indicators(ticker, current_ts)
                session_vwap = self._safe_float((exit_ind or {}).get("session_vwap", 0.0))
                self._record_trade(
                    now, ticker, exit_action, qty, current_price,
                    pnl_pct, reason, session_vwap=session_vwap
                )

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
        if self._is_tech_momentum_strategy():
            self.check_tech_momentum_trailing_stops(ts)
            rebalance_weekday = int(
                getattr(self.config.trading, "tech_momentum_rebalance_weekday", 0)
            )
            if ts.weekday() == rebalance_weekday:
                self.rebalance_tech_momentum(ts)
            self.update_equity(ts)
            return
        self._refresh_dynamic_universe(ts, force=False)
        self.detect_market_regime(ts)
        self.evaluate_signals(ts)
        self.flatten_intraday_positions(ts)
        self.update_equity(ts)

    def process_intraday_day(self, date_value):
        day_ts = self._to_ts(date_value)
        self.data_client.set_current_date(day_ts)
        self._refresh_dynamic_universe(day_ts, force=False)
        self.detect_market_regime(day_ts)

        timestamps: set[pd.Timestamp] = set()
        symbols = set(self._active_universe)
        symbols.update(getattr(self.config.universe, "benchmark_symbols", []))
        for symbol in symbols:
            df = self.data_client._hourly_data.get(symbol, pd.DataFrame())
            if df.empty:
                continue
            day_bars = df[df.index.map(lambda x: x.date() == day_ts.date())]
            timestamps.update(day_bars.index)

        if not timestamps:
            self.process_day(day_ts)
            return

        ordered = sorted(timestamps)
        last_ts = ordered[-1]
        for ts in ordered:
            self.data_client.set_current_date(ts)
            self._advance_prices(ts)
            self.evaluate_signals(ts)
            if ts == last_ts:
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

        if self._is_tech_momentum_strategy():
            self._active_universe = self._get_tech_momentum_universe()
        else:
            self._refresh_dynamic_universe(self.start_date, force=True)

        current = self.start_date
        while current <= self.end_date:
            if current.weekday() < 5:
                if self._intraday_mode_available and self.trading_style == "INTRADAY":
                    self.process_intraday_day(current)
                else:
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
            buys         = int(td["action"].isin(["BUY", "SELL_SHORT"]).sum())
            sells        = int(td["action"].isin(["SELL", "BUY_TO_COVER"]).sum())
            total_trades = len(td)
            wins         = int((td["action"].isin(["SELL", "BUY_TO_COVER"]) & (td["pnl_pct"] > 0)).sum())
            win_rate     = wins / sells * 100 if sells else 0.0

        print("\n" + "=" * 70)
        print("BACKTEST REPORT — Alpaca Bot")
        print("=" * 70)
        print(f"Style:       {self.trading_style}")
        print(f"\nCAPITAL:")
        print(f"  Starting:    ${self.starting_cash:,.2f}")
        print(f"  Ending:      ${end_eq:,.2f}")
        duration_days = (self.end_date.date() - self.start_date.date()).days + 1
        print(f"  Duration:    {self.start_date.date()} to {self.end_date.date()} ({duration_days} calendar days)")
        print(f"  Return:      {total_ret:+.2f}%")
        print(f"  Max DD:      {max_dd:.2f}%")
        print(f"  Sharpe:      {sharpe:.2f}")
        print(f"\nBENCHMARKS:")
        for benchmark in getattr(self.config.universe, "benchmark_symbols", ["SPY", "QQQ", "XLK"]):
            bars = self.data_client._daily_data.get(benchmark, pd.DataFrame())
            if bars.empty:
                continue
            window = bars[(bars.index >= self.start_date) & (bars.index <= self.end_date)]
            if len(window) < 2:
                continue
            bench_start = self._safe_float(window["close"].iloc[0])
            bench_end = self._safe_float(window["close"].iloc[-1])
            if bench_start <= 0:
                continue
            bench_ret = (bench_end / bench_start - 1.0) * 100
            marker = ""
            if self.trading_style == "INTRADAY" and benchmark == getattr(self.config.trading, "intraday_benchmark_symbol", "QQQ"):
                marker = " intraday context"
            elif self.trading_style == "SWING" and benchmark == getattr(self.config.trading, "swing_benchmark_symbol", "XLK"):
                marker = " swing context"
            print(f"  {benchmark:<5}        {bench_ret:+.2f}%{marker}")
        print(f"\nTRADES:")
        print(f"  Total:       {total_trades}")
        print(f"  Buys:        {buys}")
        print(f"  Sells:       {sells}")
        print(f"  Win Rate:    {win_rate:.1f}%")

        if not td.empty:
            exits = td[td["action"].isin(["SELL", "BUY_TO_COVER"])].copy()
            if not exits.empty:
                print(f"\nEXIT REASONS:")
                by_reason = exits.groupby("reason")["pnl_pct"].agg(["count", "mean", "sum"])
                by_reason = by_reason.sort_values("sum", ascending=False)
                for reason, row in by_reason.iterrows():
                    print(
                        f"  {reason:<22} | exits={int(row['count']):<3} "
                        f"| avg={row['mean']:+.2f}% | total={row['sum']:+.2f}%"
                    )

        if self.trading_style == "INTRADAY" and not td.empty and "vwap_alpha_pct" in td.columns:
            entries = td[td["action"].isin(["BUY", "SELL_SHORT"]) & (td["session_vwap"] > 0)]
            exits = td[td["action"].isin(["SELL", "BUY_TO_COVER"]) & (td["session_vwap"] > 0)]
            if not entries.empty or not exits.empty:
                print(f"\nVWAP EXECUTION:")
                if not entries.empty:
                    entry_beat = (entries["vwap_alpha_pct"] > 0).mean() * 100
                    print(
                        f"  Entries vs VWAP: avg={entries['vwap_alpha_pct'].mean():+.3f}% "
                        f"| beat={entry_beat:.1f}% | n={len(entries)}"
                    )
                if not exits.empty:
                    exit_beat = (exits["vwap_alpha_pct"] > 0).mean() * 100
                    print(
                        f"  Exits vs VWAP:   avg={exits['vwap_alpha_pct'].mean():+.3f}% "
                        f"| beat={exit_beat:.1f}% | n={len(exits)}"
                    )

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
                pnl_str = (
                    f"{row['pnl_pct']:+.2f}%"
                    if row["action"] in {"SELL", "BUY_TO_COVER"}
                    else ""
                )
                print(f"  {row['date']} {row['action']:<4} {int(row['qty'])}x "
                      f"{row['symbol']:<6} @ ${row['price']:.2f}  {pnl_str} {row['reason']}")

        print(f"\nOPEN POSITIONS:")
        positions = self._get_positions()
        if positions:
            for sym, pos in positions.items():
                side = str(pos.get("side", "LONG")).upper()
                direction = 1 if side == "LONG" else -1
                pnl = (direction * (pos["market_price"] - pos["avg_cost"]) / pos["avg_cost"]) * 100
                tag = "ALGO" if sym in self._algo_managed_positions else "MANUAL"
                print(f"  {sym:<6} | qty={int(pos['qty'])} | "
                      f"entry=${pos['avg_cost']:.2f} | now=${pos['market_price']:.2f} "
                      f"| {side} {pnl:+.2f}% | {tag}")
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
    parser.add_argument("--intraday-interval", default=None, help="yfinance intraday interval, e.g. 1m, 5m, 15m")
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
            intraday_interval = args.intraday_interval,
        )
        harness.run_backtest()
        harness.report()
