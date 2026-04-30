"""
Backtest harness v2 that replays the full main_ib.py trading logic.

This harness mirrors production behavior in a deterministic, daily-bar simulation:
- SPY market-regime detection (BULL/BEAR/NEUTRAL)
- Dynamic universe refresh with momentum + revenue-growth scoring
- Risk circuit breakers (daily loss and drawdown)
- Production entry/exit rules and position sizing
"""

import sys
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytz

sys.path.insert(0, str(Path(__file__).parent))

from bot_config import BOT_Config
from mock_ib import Contract, MockIB


class MainIBBacktestHarness:
    """Daily replay harness implementing the same trading logic as main_ib.py."""

    def __init__(self, symbols, start_date, end_date, starting_cash=11_000):
        self.config = BOT_Config()
        self.et = pytz.timezone("US/Eastern")

        self.start_date = pd.to_datetime(start_date).tz_localize(self.et)
        self.end_date = pd.to_datetime(end_date).tz_localize(self.et)
        self.starting_cash = float(starting_cash)

        self.ib = MockIB(self.starting_cash, self.start_date)

        # Universe state
        self._dynamic_symbols = set()
        self._active_universe = list(dict.fromkeys(list(symbols or []) + self.config.universe.core_symbols))
        self._last_universe_refresh = None

        # Trading state
        self._starting_cash = self.starting_cash
        self.peak_equity = self.starting_cash
        self.market_regime = "NEUTRAL"
        self._trading_enabled = True

        self._algo_managed_positions = set()
        self.entry_time = {}
        self.entry_price = {}
        self.highest_price = {}
        self._entry_strategy_reason = {}

        self._strategy_weights = {
            "momentum": 0.35,
            "mean_reversion": 0.25,
            "orb": 0.20,
            "vwap_twap": 0.20,
            "market_making": 0.00,
            "stat_arb": 0.00,
            "sentiment": 0.00,
        }
        self._min_portfolio_signal_score = 0.30

        self.trade_history = deque(maxlen=200)
        self.winning_trades = deque(maxlen=100)
        self.losing_trades = deque(maxlen=100)

        self.symbol_performance = defaultdict(
            lambda: {
                "trades": 0,
                "wins": 0,
                "total_pnl": 0.0,
                "consecutive_losses": 0,
                "win_rate": 0.0,
            }
        )

        self.trades = []
        self.equity_history = []
        self.loaded_symbols = set()

    # ---------------------------------------------------------------------
    # Data + indicator prep
    # ---------------------------------------------------------------------
    @staticmethod
    def _ema(series, span):
        return series.ewm(span=span, adjust=False).mean()

    @staticmethod
    def _rsi(series, length=14):
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, pd.NA)
        return 100 - (100 / (1 + rs))

    def _prepare_indicators(self, df):
        work = df.copy()
        work["ema_20"] = self._ema(work["close"], 20)
        work["ema_50"] = self._ema(work["close"], 50)
        work["rsi"] = self._rsi(work["close"], 14)

        ema_fast = self._ema(work["close"], 12)
        ema_slow = self._ema(work["close"], 26)
        work["macd"] = ema_fast - ema_slow
        work["macd_signal"] = self._ema(work["macd"], 9)

        return work

    def _normalize_ohlcv(self, df):
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df.columns = [str(c).lower() for c in df.columns]
        required = ["open", "high", "low", "close", "volume"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            return pd.DataFrame()

        if df.index.tz is None:
            df.index = df.index.tz_localize(self.et)
        else:
            df.index = df.index.tz_convert(self.et)

        out = df[required].copy()
        out["adjclose"] = out["close"]
        return out

    def _load_data(self, symbol):
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

            clean = self._normalize_ohlcv(raw)
            if clean.empty:
                return pd.DataFrame()

            return self._prepare_indicators(clean)
        except Exception:
            return pd.DataFrame()

    def load_all_data(self):
        universe_cfg = self.config.universe

        data_symbols = set(self._active_universe)
        data_symbols.update(universe_cfg.core_symbols)
        if getattr(universe_cfg, "dynamic_enabled", False):
            data_symbols.update(getattr(universe_cfg, "candidate_symbols", []))
        data_symbols.add("SPY")

        print(f"Loading data for {len(data_symbols)} symbols...")
        for symbol in sorted(data_symbols):
            df = self._load_data(symbol)
            if df.empty:
                print(f"  x {symbol}: no data")
                continue
            self.ib.load_historical_data(symbol, df)
            self.loaded_symbols.add(symbol)
            print(f"  ok {symbol}: {len(df)} bars")

        # Keep active universe constrained to loaded symbols
        self._active_universe = [s for s in self._active_universe if s in self.loaded_symbols]

    # ---------------------------------------------------------------------
    # Time-series access helpers
    # ---------------------------------------------------------------------
    def _to_ts(self, date_value):
        ts = pd.to_datetime(date_value)
        if ts.tzinfo is None:
            return ts.tz_localize(self.et)
        return ts.tz_convert(self.et)

    def _get_bar_slice(self, symbol, date_value):
        df = self.ib.historical_data.get(symbol, pd.DataFrame())
        if df.empty:
            return pd.DataFrame()
        ts = self._to_ts(date_value)
        if ts not in df.index:
            return pd.DataFrame()
        return df.loc[:ts].copy()

    def _get_bar(self, symbol, date_value):
        sl = self._get_bar_slice(symbol, date_value)
        if sl.empty:
            return None
        row = sl.iloc[-1]
        return {
            "symbol": symbol,
            "date": sl.index[-1],
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": int(row["volume"]),
        }

    def _advance_day_prices(self, date_value):
        for symbol in self.loaded_symbols:
            bar = self._get_bar(symbol, date_value)
            if not bar:
                continue
            px = bar["close"]
            self.ib.update_ticker_price(
                symbol,
                close=px,
                bid=px * 0.9999,
                ask=px * 1.0001,
                volume=bar["volume"],
            )

    # ---------------------------------------------------------------------
    # IB-style helpers
    # ---------------------------------------------------------------------
    def _get_account_value(self, tag):
        preferred = None
        fallback = None
        for av in self.ib.accountValues():
            if av.tag != tag:
                continue
            try:
                val = float(av.value)
            except (TypeError, ValueError):
                continue
            if av.currency == "USD":
                preferred = val
                break
            if fallback is None:
                fallback = val
        if preferred is not None:
            return preferred
        if fallback is not None:
            return fallback
        return 0.0

    @property
    def cash(self):
        return self._get_account_value("TotalCashValue")

    @property
    def net_liquidation(self):
        return self._get_account_value("NetLiquidation")

    def _get_positions(self):
        positions = {}
        for pos in self.ib.positions():
            if pos.position == 0:
                continue
            ticker = pos.contract.symbol
            market_price = self._get_price(ticker)
            positions[ticker] = {
                "qty": abs(pos.position),
                "avg_cost": float(pos.avgCost),
                "contract": pos.contract,
                "market_price": market_price,
                "side": "LONG" if pos.position > 0 else "SHORT",
            }
        return positions

    def _get_price(self, ticker):
        t = self.ib.ticker(Contract(ticker))
        if not t:
            return 0.0
        px = t.marketPrice()
        if px and px == px and px > 0:
            return float(px)
        if t.close and t.close > 0:
            return float(t.close)
        return 0.0

    # ---------------------------------------------------------------------
    # Universe logic
    # ---------------------------------------------------------------------
    @staticmethod
    def _safe_float(value, default=0.0):
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _build_active_universe(self):
        merged = list(self.config.universe.core_symbols) + sorted(self._dynamic_symbols)
        seen = set()
        self._active_universe = [t for t in merged if t in self.loaded_symbols and not (t in seen or seen.add(t))]

    def _score_dynamic_candidate(self, ticker, date_value):
        cfg = self.config.universe
        lookback = int(getattr(cfg, "momentum_lookback_days", 22))
        vol_window = int(getattr(cfg, "avg_volume_window_days", 20))
        min_required = max(lookback + 1, vol_window)

        sl = self._get_bar_slice(ticker, date_value)
        if sl.empty or len(sl) < min_required + 1:
            return None

        close_series = sl["close"].dropna()
        if len(close_series) < min_required + 1:
            return None

        current_price = self._safe_float(close_series.iloc[-1])
        lookback_price = self._safe_float(close_series.iloc[-(lookback + 1)])
        if current_price <= 0 or lookback_price <= 0:
            return None

        min_price = self._safe_float(getattr(cfg, "min_price", 0.0), 0.0)
        if current_price < min_price:
            return None

        recent = sl.tail(vol_window).copy()
        recent["dollar_vol"] = recent["close"] * recent["volume"]
        avg_dollar_vol = self._safe_float(recent["dollar_vol"].mean())
        min_dollar_vol = self._safe_float(getattr(cfg, "min_avg_dollar_volume", 0.0), 0.0)
        if avg_dollar_vol < min_dollar_vol:
            return None

        momentum = (current_price / lookback_price) - 1.0

        rev_map = getattr(cfg, "revenue_growth_1y", {})
        revenue_growth = self._safe_float(rev_map.get(ticker, 0.0), 0.0)

        w_mom = self._safe_float(getattr(cfg, "momentum_weight", 0.6), 0.6)
        w_rev = self._safe_float(getattr(cfg, "revenue_growth_weight", 0.4), 0.4)
        score = (w_mom * momentum) + (w_rev * revenue_growth)

        return score, momentum, revenue_growth

    def _refresh_dynamic_universe(self, date_value, force=False):
        cfg = self.config.universe

        if not getattr(cfg, "dynamic_enabled", False):
            self._dynamic_symbols = set()
            self._build_active_universe()
            return

        current_ts = self._to_ts(date_value)
        refresh_days = max(1, int(getattr(cfg, "refresh_days", 14)))

        if not force and self._last_universe_refresh is not None:
            days_since = (current_ts.date() - self._last_universe_refresh.date()).days
            if days_since < refresh_days:
                return

        core = set(self.config.universe.core_symbols)
        excludes = set(getattr(cfg, "exclude_symbols", []))
        candidates = [
            t for t in getattr(cfg, "candidate_symbols", []) if t not in core and t not in excludes
        ]

        scored = []
        for ticker in candidates:
            try:
                scored_tuple = self._score_dynamic_candidate(ticker, current_ts)
                if scored_tuple is None:
                    continue
                score, momentum, revenue_growth = scored_tuple
                scored.append((ticker, score, momentum, revenue_growth))
            except Exception:
                continue

        scored.sort(key=lambda x: x[1], reverse=True)
        top_n = max(0, int(getattr(cfg, "top_n_dynamic", 10)))
        selected = [t for t, _, _, _ in scored[:top_n]]
        self._dynamic_symbols = set(selected)
        self._build_active_universe()
        self._last_universe_refresh = current_ts

    # ---------------------------------------------------------------------
    # Indicators + regime
    # ---------------------------------------------------------------------
    def _compute_spy_indicators(self, date_value):
        sl = self._get_bar_slice("SPY", date_value)
        if sl.empty or len(sl) < 55:
            return None

        latest = sl.iloc[-1]
        prev = sl.iloc[-2]

        def valid(v):
            return float(v) if pd.notna(v) else None

        return {
            "price": float(latest["close"]),
            "ema_20": valid(latest["ema_20"]),
            "ema_50": valid(latest["ema_50"]),
            "ema_20_prev": valid(prev["ema_20"]),
            "ema_50_prev": valid(prev["ema_50"]),
            "rsi": valid(latest["rsi"]),
        }

    def _compute_symbol_indicators(self, ticker, date_value):
        sl = self._get_bar_slice(ticker, date_value)
        if sl.empty or len(sl) < 52:
            return None

        latest = sl.iloc[-1]

        bb_mid = sl["close"].rolling(20).mean().iloc[-1]
        bb_std = sl["close"].rolling(20).std(ddof=0).iloc[-1]
        zscore = 0.0 if pd.isna(bb_mid) or pd.isna(bb_std) or bb_std == 0 else float((latest["close"] - bb_mid) / bb_std)

        vol_sma = sl["volume"].rolling(20).mean().iloc[-1]
        volume_ratio = 1.0 if pd.isna(vol_sma) or vol_sma <= 0 else float(latest["volume"] / vol_sma)

        vwap_num = (sl["close"] * sl["volume"]).rolling(20).sum().iloc[-1]
        vwap_den = sl["volume"].rolling(20).sum().iloc[-1]
        vwap = float(latest["close"]) if pd.isna(vwap_den) or vwap_den <= 0 else float(vwap_num / vwap_den)

        orb_high = float(sl["high"].tail(20).max())
        orb_low = float(sl["low"].tail(20).min())

        vals = {
            "price": latest.get("close"),
            "macd": latest.get("macd"),
            "macd_signal": latest.get("macd_signal"),
            "rsi": latest.get("rsi"),
            "ema_50": latest.get("ema_50"),
        }

        if any(pd.isna(v) for v in vals.values()):
            return None

        out = {k: float(v) for k, v in vals.items()}
        out.update(
            {
                "vwap": vwap,
                "zscore": zscore,
                "volume_ratio": volume_ratio,
                "opening_range_high": orb_high,
                "opening_range_low": orb_low,
            }
        )
        return out

    def _compute_portfolio_signal(self, ticker, ind):
        price = ind["price"]
        macd = ind["macd"]
        macd_sig = ind["macd_signal"]
        rsi_val = ind["rsi"]
        ema_val = ind["ema_50"]
        vwap = ind["vwap"]
        zscore = ind["zscore"]
        volume_ratio = ind["volume_ratio"]
        orb_high = ind["opening_range_high"]

        component_scores = {
            "momentum": 0.0,
            "mean_reversion": 0.0,
            "orb": 0.0,
            "vwap_twap": 0.0,
            "market_making": 0.0,
            "stat_arb": 0.0,
            "sentiment": 0.0,
        }

        if price > ema_val and macd > macd_sig and 55 <= rsi_val <= 72:
            raw = ((macd - macd_sig) * 100) + min(1.0, max(0.0, volume_ratio - 1.0))
            component_scores["momentum"] = min(1.0, max(0.0, raw))

        if zscore < -1.8 and rsi_val < 42 and price < vwap:
            component_scores["mean_reversion"] = min(1.0, abs(zscore) / 3.0)

        if price > orb_high and volume_ratio >= 1.2:
            component_scores["orb"] = min(1.0, (price / max(orb_high, 1e-9)) - 1.0 + 0.5)

        if price > vwap and macd > macd_sig:
            component_scores["vwap_twap"] = min(1.0, ((price / max(vwap, 1e-9)) - 1.0) * 200)

        weighted = 0.0
        active = []
        for name, score in component_scores.items():
            weight = self._strategy_weights.get(name, 0.0)
            if score > 0 and weight > 0:
                active.append(f"{name}:{score:.2f}")
            weighted += weight * score

        return weighted, ", ".join(active) if active else "none"

    def detect_market_regime(self, date_value):
        spy = self._compute_spy_indicators(date_value)
        if not spy:
            return

        ema_20 = spy["ema_20"]
        ema_50 = spy["ema_50"]
        ema_20_prev = spy["ema_20_prev"]
        spy_price = spy["price"]

        if ema_20 is None or ema_50 is None or ema_20_prev is None:
            return

        previous_regime = self.market_regime

        price_above_50 = spy_price > ema_50
        price_below_50 = spy_price < ema_50
        ema_structure_bull = ema_20 > ema_50
        ema_structure_bear = ema_20 < ema_50
        ema_20_rising = ema_20 > ema_20_prev
        momentum_bear = (not ema_20_rising) and spy_price < ema_20

        if price_above_50 and ema_structure_bull:
            self.market_regime = "BULL"
        elif price_below_50 and ema_structure_bear and momentum_bear:
            self.market_regime = "BEAR"
        else:
            if previous_regime == "BULL" and price_above_50:
                self.market_regime = "BULL"
            elif previous_regime == "BEAR" and price_below_50:
                self.market_regime = "BEAR"
            else:
                self.market_regime = "NEUTRAL"

    # ---------------------------------------------------------------------
    # Execution + risk
    # ---------------------------------------------------------------------
    @staticmethod
    def _estimate_fee(qty, price):
        if qty <= 0 or price <= 0:
            return 0.0
        trade_value = qty * price
        fee = max(0.0035 * qty, 0.35)
        return min(fee, trade_value * 0.01)

    def _calculate_position_size(self, price):
        available_cash = self.cash
        safe_available = available_cash * 0.75
        target_value = min(safe_available * 0.85, 6500)
        if target_value < 4000:
            return 0

        qty = int(target_value / price)
        if qty <= 0:
            return 0

        fee = self._estimate_fee(qty, price)
        total_cost = qty * price + fee
        if total_cost > target_value:
            qty = int((target_value - fee) / price)
            fee = self._estimate_fee(qty, price)
            total_cost = qty * price + fee

        return qty if qty > 0 and total_cost >= 4000 else 0

    def _place_market_buy(self, ticker, qty):
        contract = Contract(symbol=ticker)
        order = type("Order", (), {"action": "BUY", "totalQuantity": qty})()
        trade = self.ib.placeOrder(contract, order)
        return trade.orderStatus.status == "Filled"

    def _place_market_sell(self, ticker, qty, reason=""):
        contract = Contract(symbol=ticker)
        order = type("Order", (), {"action": "SELL", "totalQuantity": qty})()
        trade = self.ib.placeOrder(contract, order)
        return trade.orderStatus.status == "Filled"

    def _update_symbol_performance(self, ticker, pnl):
        perf = self.symbol_performance[ticker]
        perf["trades"] += 1
        perf["total_pnl"] += pnl
        if pnl > 0:
            perf["wins"] += 1
            perf["consecutive_losses"] = 0
        else:
            perf["consecutive_losses"] += 1
        perf["win_rate"] = (perf["wins"] / perf["trades"] * 100) if perf["trades"] > 0 else 0

    def update_equity(self, date_value):
        total = self.ib.portfolio.cash
        for ticker, pos in self._get_positions().items():
            px = pos["market_price"]
            if px > 0:
                total += pos["qty"] * px

        self.ib.portfolio._total_value = total
        self.ib.account_values.update_liquidation(total)
        self.equity_history.append(
            {
                "date": self._to_ts(date_value),
                "equity": float(total),
                "cash": float(self.ib.portfolio.cash),
                "regime": self.market_regime,
            }
        )

    def _record_trade(self, date_value, symbol, action, qty, price, pnl_pct=0.0, reason=""):
        row = {
            "date": pd.to_datetime(date_value).date(),
            "symbol": symbol,
            "action": action,
            "qty": int(qty),
            "price": float(price),
            "pnl_pct": float(pnl_pct),
        }
        if action == "BUY":
            row["entry_reason"] = reason
        else:
            row["exit_reason"] = reason
        self.trades.append(row)

    def evaluate_signals(self, date_value):
        if not self._trading_enabled:
            return

        current_equity = self.net_liquidation
        if current_equity <= 0:
            return

        daily_loss = self._starting_cash - current_equity
        if daily_loss > self.config.risk.max_daily_loss:
            self._trading_enabled = False
            return

        if current_equity > self.peak_equity:
            self.peak_equity = current_equity

        drawdown = ((self.peak_equity - current_equity) / self.peak_equity) if self.peak_equity > 0 else 0
        if drawdown > self.config.risk.max_drawdown_pct:
            self._trading_enabled = False
            return

        now = self._to_ts(date_value).to_pydatetime()
        positions = self._get_positions()
        algo_positions = {t: p for t, p in positions.items() if t in self._algo_managed_positions}

        # Exit logic
        for ticker, pos in list(algo_positions.items()):
            current_price = pos["market_price"]
            if current_price <= 0:
                continue

            avg_entry = pos["avg_cost"]
            qty = int(pos["qty"])
            pnl_pct = (current_price - avg_entry) / avg_entry if avg_entry > 0 else 0
            pnl_dollar = qty * (current_price - avg_entry)
            held_time = now - self.entry_time.get(ticker, now)

            self.highest_price[ticker] = max(self.highest_price.get(ticker, current_price), current_price)

            should_exit = False
            reason = ""
            if pnl_pct <= -self.config.trading.stop_loss_pct:
                should_exit, reason = True, "stop_loss"
            elif pnl_pct >= self.config.trading.take_profit_pct:
                should_exit, reason = True, "take_profit"
            elif (
                held_time >= timedelta(hours=self.config.trading.profit_lock_hours)
                and pnl_pct >= self.config.trading.profit_lock_min_gain_pct
            ):
                should_exit, reason = True, "profit_lock"
            elif held_time >= timedelta(days=self.config.trading.max_hold_days):
                should_exit, reason = True, "time_exit"

            if pnl_pct <= -0.05:
                should_exit, reason = True, "gap_protection"

            if should_exit and self._place_market_sell(ticker, qty, reason):
                self._algo_managed_positions.discard(ticker)
                self._entry_strategy_reason.pop(ticker, None)
                self._record_trade(date_value, ticker, "SELL", qty, current_price, pnl_pct * 100, reason)

                msg = (
                    f"{now.strftime('%Y-%m-%d')} SELL {ticker} - Qty: {qty} @ ${current_price:.2f} "
                    f"| P&L: ${pnl_dollar:.2f} ({pnl_pct:.2%})"
                )
                self.trade_history.append(msg)
                if pnl_dollar > 0:
                    self.winning_trades.append(msg)
                else:
                    self.losing_trades.append(msg)

                self._update_symbol_performance(ticker, pnl_dollar)

        # Entry logic
        if self.market_regime == "BEAR":
            return

        portfolio_return = ((current_equity - self._starting_cash) / self._starting_cash) if self._starting_cash > 0 else 0

        max_positions = self.config.trading.max_positions
        if self.market_regime == "BULL" and portfolio_return > 0.02:
            max_allowed = max_positions
        elif self.market_regime == "BULL":
            max_allowed = min(3, max_positions)
        elif self.market_regime == "NEUTRAL":
            max_allowed = min(2, max_positions)
        else:
            max_allowed = 1

        algo_count = len([t for t in self._algo_managed_positions if t in positions])
        if algo_count >= max_allowed:
            return

        if self.cash < 4000:
            return

        candidates = []
        for ticker in self._active_universe:
            if ticker == "SPY" or ticker in positions:
                continue

            indicators = self._compute_symbol_indicators(ticker, date_value)
            if not indicators:
                continue

            price = indicators["price"]
            if price <= 0:
                continue

            score, reason = self._compute_portfolio_signal(ticker, indicators)
            if score >= self._min_portfolio_signal_score:
                candidates.append((ticker, score, price, reason))

        if not candidates:
            return

        candidates.sort(key=lambda x: x[1], reverse=True)
        best_ticker, best_score, current_price, reason = candidates[0]

        live_price = self._get_price(best_ticker)
        if live_price > 0:
            current_price = live_price

        qty = self._calculate_position_size(current_price)
        if qty <= 0:
            return

        if self._place_market_buy(best_ticker, qty):
            self._algo_managed_positions.add(best_ticker)
            self.entry_time[best_ticker] = now
            self.entry_price[best_ticker] = current_price
            self.highest_price[best_ticker] = current_price
            self._entry_strategy_reason[best_ticker] = reason

            self._record_trade(date_value, best_ticker, "BUY", qty, current_price, 0.0, reason)
            self.trade_history.append(
                f"{now.strftime('%Y-%m-%d')} BUY {best_ticker} - Qty: {qty} @ ${current_price:.2f} "
                f"| score={best_score:.2f} | {reason}"
            )

    # ---------------------------------------------------------------------
    # Backtest loop + report
    # ---------------------------------------------------------------------
    def process_day(self, date_value):
        ts = self._to_ts(date_value)
        self.ib.update_current_time(ts)
        self._advance_day_prices(ts)

        self._refresh_dynamic_universe(ts, force=False)
        self.detect_market_regime(ts)
        self.evaluate_signals(ts)
        self.update_equity(ts)

    def run_backtest(self):
        print("=" * 80)
        print("RUNNING MAIN_IB LOGIC BACKTEST (v2)")
        print("=" * 80)
        print(f"Period: {self.start_date.date()} -> {self.end_date.date()}")
        print(f"Starting capital: ${self.starting_cash:,.2f}")

        self.load_all_data()

        # Initial refresh aligned with production startup.
        self._refresh_dynamic_universe(self.start_date, force=True)

        current = self.start_date
        while current <= self.end_date:
            if current.weekday() < 5:
                self.process_day(current)
            current += timedelta(days=1)

    def report(self):
        if not self.equity_history:
            print("No equity history generated. Backtest did not run.")
            return {}

        eq_df = pd.DataFrame(self.equity_history)
        trades_df = pd.DataFrame(self.trades)

        start_eq = float(eq_df["equity"].iloc[0])
        end_eq = float(eq_df["equity"].iloc[-1])
        ret_pct = ((end_eq - start_eq) / start_eq * 100) if start_eq > 0 else 0.0

        eq_df["peak"] = eq_df["equity"].cummax()
        eq_df["dd"] = (eq_df["equity"] - eq_df["peak"]) / eq_df["peak"].replace(0, pd.NA)
        max_dd_pct = float(abs(eq_df["dd"].min() * 100)) if not eq_df["dd"].isna().all() else 0.0

        daily_returns = eq_df["equity"].pct_change().dropna()
        sharpe = float((daily_returns.mean() / daily_returns.std()) * (252 ** 0.5)) if len(daily_returns) > 1 and daily_returns.std() > 0 else 0.0

        buy_count = int((trades_df["action"] == "BUY").sum()) if not trades_df.empty else 0
        sell_mask = (trades_df["action"] == "SELL") if not trades_df.empty else pd.Series([], dtype=bool)
        sell_count = int(sell_mask.sum()) if not trades_df.empty else 0
        win_count = int((trades_df.loc[sell_mask, "pnl_pct"] > 0).sum()) if sell_count > 0 else 0
        win_rate = (win_count / sell_count * 100) if sell_count > 0 else 0.0

        print("\n" + "=" * 80)
        print("BACKTEST REPORT - MAIN_IB LOGIC")
        print("=" * 80)
        print(f"Starting Equity: ${start_eq:,.2f}")
        print(f"Ending Equity:   ${end_eq:,.2f}")
        print(f"Total Return:    {ret_pct:+.2f}%")
        print(f"Max Drawdown:    {max_dd_pct:.2f}%")
        print(f"Sharpe Ratio:    {sharpe:.2f}")
        print(f"Market Regime:   {self.market_regime}")

        print("\nTrades")
        print(f"Total BUY:       {buy_count}")
        print(f"Total SELL:      {sell_count}")
        print(f"Win Rate:        {win_rate:.1f}%")

        open_positions = self._get_positions()
        open_algo = [t for t in open_positions if t in self._algo_managed_positions]
        print(f"Open Positions:  {len(open_positions)} (algo-managed: {len(open_algo)})")

        if not trades_df.empty:
            print("\nRecent Trades (last 12):")
            for _, row in trades_df.tail(12).iterrows():
                reason = row.get("entry_reason", "")
                if pd.isna(reason) or reason == "":
                    reason = row.get("exit_reason", "")
                if pd.isna(reason):
                    reason = ""
                pnl_txt = f" {row['pnl_pct']:+.2f}%" if row["action"] == "SELL" else ""
                print(
                    f"  {row['date']} {row['action']:4s} {int(row['qty']):4d}x {row['symbol']:6s} "
                    f"@ ${row['price']:.2f}{pnl_txt} {reason}".rstrip()
                )

        return {
            "start_equity": start_eq,
            "end_equity": end_eq,
            "return_pct": ret_pct,
            "max_drawdown_pct": max_dd_pct,
            "sharpe_ratio": sharpe,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "win_rate_pct": win_rate,
            "final_regime": self.market_regime,
        }


if __name__ == "__main__":
    cfg = BOT_Config()

    harness = MainIBBacktestHarness(
        symbols=cfg.universe.core_symbols,
        start_date="2024-01-01",
        end_date="2025-12-31",
        starting_cash=cfg.general.starting_capital,
    )

    harness.run_backtest()
    harness.report()
