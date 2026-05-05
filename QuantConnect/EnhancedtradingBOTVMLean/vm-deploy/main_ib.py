#!/usr/bin/env python3
"""
Trading Bot — ib_insync native version
Connects to an already-running IB Gateway on localhost:4002
Matches architecture: Laptop → SSH → VPS → Python Bot → IB Gateway → IBC → Xvfb → IBKR
"""

import logging
import logging.handlers
import signal
import sys
import os
import smtplib
import requests
from datetime import datetime, timedelta, time as dt_time
from collections import defaultdict, deque
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import pandas as pd
import pandas_ta as ta
import pytz
from ib_insync import IB, Stock, MarketOrder, LimitOrder, util
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from bot_config import BOT_Config
from live_feeds import FeedManager


class TradingBot:
    """
    Production trading bot using ib_insync.
    Connects to IB Gateway (already managed by IBC + systemd on this VM).
    """

    def __init__(self):
        self.config = BOT_Config()
        self._setup_logging()
        self.logger.info("Initializing Trading Bot...")

        self.ib = IB()
        self.ib.errorEvent += self._on_ib_error
        # Use pytz tzinfo directly to avoid system zoneinfo/tzdata dependency issues.
        self.scheduler = BackgroundScheduler(timezone=pytz.timezone("US/Eastern"))

        # ── Contracts cache ──────────────────────────────────────
        self._contracts: dict[str, Stock] = {}

        # ── State ────────────────────────────────────────────────
        self._starting_cash: float = self.config.general.starting_capital
        self.peak_equity: float = self._starting_cash
        self.market_regime: str = "NEUTRAL"

        # Universe state (core + dynamic selection)
        self._dynamic_symbols: set[str] = set()
        self._active_universe: list[str] = list(self.config.universe.core_symbols)
        self._last_universe_refresh: datetime | None = None
        self._revenue_growth_cache: dict[str, tuple[datetime, float]] = {}

        self._algo_managed_positions: set[str] = set()   # ticker strings
        self.entry_time: dict[str, datetime] = {}
        self.entry_price: dict[str, float] = {}
        self.highest_price: dict[str, float] = {}
        self._entry_strategy_reason: dict[str, str] = {}

        # Portfolio mix of intraday strategies from the deployment deck.
        # market_making and sentiment are now live-feed-backed.
        self._strategy_weights: dict[str, float] = {
            "momentum": 0.30,
            "mean_reversion": 0.20,
            "orb": 0.15,
            "vwap_twap": 0.20,
            "market_making": 0.05,  # driven by order-book imbalance feed
            "stat_arb": 0.00,
            "sentiment": 0.10,      # driven by Alpaca news + VADER NLP feed
        }

        # ── Live feeds (Alpaca News + IEX quote stream) ───────────────────
        feeds_cfg = self.config.feeds
        self._feeds = FeedManager(
            api_key=feeds_cfg.alpaca_api_key,
            api_secret=feeds_cfg.alpaca_api_secret,
            news_poll_interval_seconds=feeds_cfg.news_poll_interval_seconds,
        )
        self._min_portfolio_signal_score: float = 0.30

        self.trade_history: deque = deque(maxlen=50)
        self.winning_trades: deque = deque(maxlen=30)
        self.losing_trades: deque = deque(maxlen=20)
        self._recent_ib_errors: deque = deque(maxlen=10)
        self._last_closed_log_key: dict[str, str] = {}
        et_now = datetime.now(pytz.timezone("US/Eastern"))
        self._schedule_stats_date = et_now.date()
        self._schedule_trigger_count = 0
        self._schedule_market_closed_skips = 0
        self.symbol_performance: dict = defaultdict(
            lambda: {"trades": 0, "wins": 0, "total_pnl": 0.0,
                     "consecutive_losses": 0, "win_rate": 0.0}
        )

        # ── Risk state ───────────────────────────────────────────
        self._trading_enabled: bool = True
        self._running: bool = True

    # ================================================================
    #  LOGGING
    # ================================================================
    def _setup_logging(self):
        preferred_log_dir = self.config.monitoring.log_dir
        try:
            os.makedirs(preferred_log_dir, exist_ok=True)
            log_dir = preferred_log_dir
        except PermissionError:
            # Fallback to a writable local directory for manual runs.
            log_dir = os.path.join(os.getcwd(), "logs")
            os.makedirs(log_dir, exist_ok=True)

        self.logger = logging.getLogger("TradingBot")
        self.logger.setLevel(logging.DEBUG)

        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )

        # Console
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(fmt)
        self.logger.addHandler(ch)

        # Rotating file
        fh = logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, "tradingbot.log"),
            maxBytes=10_000_000,
            backupCount=5,
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        self.logger.addHandler(fh)

    # ================================================================
    #  IB CONNECTION
    # ================================================================
    def _on_ib_error(self, req_id, error_code, error_string, contract=None):
        self._recent_ib_errors.append((error_code, error_string))

        info_codes = {2104, 2106, 2108, 2158}
        log_fn = self.logger.info if error_code in info_codes else self.logger.error
        log_fn(f"IB API error {error_code} (reqId={req_id}): {error_string}")

    def _build_connect_failure_message(self, exc: Exception) -> str:
        exc_text = str(exc)
        recent_errors = list(self._recent_ib_errors)
        reasons = []

        if any(code == 10141 for code, _ in recent_errors):
            reasons.append(
                "Paper API access is blocked because the IBKR paper trading disclaimer has not "
                "been accepted. Open the paper IB Gateway/TWS UI, accept the disclaimer once, "
                "then retry."
            )

        client_id_in_use = "clientId" in exc_text and "already in use" in exc_text
        if client_id_in_use:
            reasons.append(
                f"IB clientId {self.config.ib.client_id} is already in use. Stop the existing bot "
                "session or set IB_CLIENT_ID to a different integer before retrying."
            )

        if not reasons:
            reasons.append(
                "IB Gateway connection failed. Verify the gateway is running, API access is enabled, "
                f"and the configured host/port ({self.config.ib.host}:{self.config.ib.port}) are correct."
            )

        if recent_errors:
            recent_summary = "; ".join(
                f"{code}: {message}" for code, message in recent_errors[-3:]
            )
            reasons.append(f"Recent IB errors: {recent_summary}")

        reasons.append(f"Underlying connect error: {exc_text}")
        return " ".join(reasons)

    def _connect(self):
        """Connect to IB Gateway (must already be running via IBC/systemd)."""
        self.logger.info(
            f"Connecting to IB Gateway at {self.config.ib.host}:{self.config.ib.port} "
            f"(clientId={self.config.ib.client_id})"
        )
        self._recent_ib_errors.clear()
        try:
            self.ib.connect(
                self.config.ib.host,
                self.config.ib.port,
                clientId=self.config.ib.client_id,
                readonly=False,
                timeout=30,
            )
        except Exception as exc:
            message = self._build_connect_failure_message(exc)
            self.logger.critical(message)
            raise RuntimeError(message) from exc

        self.ib.disconnectedEvent += self._on_disconnect
        self.logger.info(f"Connected. Account: {self.ib.managedAccounts()}")

        # Sync starting cash from actual account
        self._starting_cash = self._get_account_value("NetLiquidation")
        self.peak_equity = self._starting_cash
        self.logger.info(f"Account equity: ${self._starting_cash:,.2f}")

    def _on_disconnect(self):
        """Auto-reconnect handler."""
        self.logger.warning("Disconnected from IB Gateway")
        if not self._running:
            return
        self._reconnect()

    def _reconnect(self):
        for attempt in range(1, self.config.ib.reconnect_attempts + 1):
            try:
                self.logger.info(f"Reconnect attempt {attempt}...")
                import time
                time.sleep(self.config.ib.reconnect_delay)
                self._recent_ib_errors.clear()
                self.ib.connect(
                    self.config.ib.host,
                    self.config.ib.port,
                    clientId=self.config.ib.client_id,
                    readonly=False,
                    timeout=30,
                )
                self._refresh_dynamic_universe(force=False)
                self._subscribe_market_data()
                self.logger.info("Reconnected successfully")
                return
            except Exception as e:
                self.logger.error(
                    f"Reconnect attempt {attempt} failed: "
                    f"{self._build_connect_failure_message(e)}"
                )
        self.logger.critical("All reconnection attempts failed!")
        self._send_alert_email(
            "CRITICAL: IB Gateway Connection Lost",
            "Failed to reconnect. Manual intervention required.",
        )

    # ================================================================
    #  MARKET DATA SUBSCRIPTIONS
    # ================================================================
    def _subscribe_market_data(self):
        """Qualify contracts and subscribe to streaming market data."""
        all_tickers = ["SPY"] + list(self._active_universe)
        for ticker in all_tickers:
            if ticker not in self._contracts:
                contract = Stock(ticker, "SMART", "USD")
                self.ib.qualifyContracts(contract)
                self._contracts[ticker] = contract
            self.ib.reqMktData(self._contracts[ticker], "", False, False)
            self.logger.debug(f"Subscribed: {ticker}")
        self.ib.sleep(2)  # Let initial ticks arrive

    # ================================================================
    #  ACCOUNT HELPERS
    # ================================================================
    def _get_account_value(self, tag: str) -> float:
        """Read a single account value by tag (e.g. 'NetLiquidation')."""
        preferred_match = None
        fallback_match = None

        for av in self.ib.accountValues():
            if av.tag != tag:
                continue
            try:
                parsed = float(av.value)
            except (ValueError, TypeError):
                continue

            if av.currency == "USD":
                preferred_match = parsed
                break

            if fallback_match is None:
                fallback_match = parsed

        if preferred_match is not None:
            return preferred_match
        if fallback_match is not None:
            return fallback_match
        return 0.0

    @property
    def cash(self) -> float:
        return self._get_account_value("TotalCashValue")

    @property
    def net_liquidation(self) -> float:
        return self._get_account_value("NetLiquidation")

    def _get_positions(self) -> dict:
        """Return {ticker: {qty, avg_cost, contract, market_price}}"""
        result = {}
        for pos in self.ib.positions():
            if pos.position == 0:
                continue
            ticker = pos.contract.symbol
            market_price = self._get_price(ticker)
            result[ticker] = {
                "qty": abs(pos.position),
                "avg_cost": pos.avgCost,
                "contract": pos.contract,
                "market_price": market_price,
                "side": "LONG" if pos.position > 0 else "SHORT",
            }
        return result

    def _get_price(self, ticker: str) -> float:
        """Get latest price from streaming data."""
        contract = self._contracts.get(ticker)
        if not contract:
            return 0.0
        t = self.ib.ticker(contract)
        if t:
            price = t.marketPrice()
            if price and price == price and price > 0:  # NaN check
                return float(price)
            # Fallback to last close
            if t.close and t.close > 0:
                return float(t.close)
        return 0.0

    # ================================================================
    #  DYNAMIC UNIVERSE SELECTION
    # ================================================================
    def _safe_float(self, value, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _build_active_universe(self):
        """Build deduplicated active universe = core + dynamic symbols."""
        merged = list(self.config.universe.core_symbols) + sorted(self._dynamic_symbols)
        seen = set()
        self._active_universe = [t for t in merged if not (t in seen or seen.add(t))]
        # Keep live feeds subscribed to the current universe.
        self._feeds.update_symbols(self._active_universe)

    def _extract_revenue_growth_from_payload(self, payload) -> float | None:
        """Extract revenue growth from varied provider payload shapes."""
        if payload is None:
            return None

        # Direct growth fields, if provider already returns them.
        if isinstance(payload, dict):
            for key in [
                "revenueGrowth", "revenue_growth", "revenue_growth_yoy",
                "revenue_growth_1y", "revenue_growth_ttm",
            ]:
                if key in payload:
                    return self._safe_float(payload.get(key), 0.0)

            # Common nested patterns.
            for nested_key in ["fundamentals", "financials", "data", "results", "result"]:
                if nested_key in payload:
                    nested_val = payload.get(nested_key)
                    growth = self._extract_revenue_growth_from_payload(nested_val)
                    if growth is not None:
                        return growth

        # List/sequence of statements with revenues.
        if isinstance(payload, list) and len(payload) >= 2:
            latest = payload[0] if isinstance(payload[0], dict) else {}
            prior = payload[1] if isinstance(payload[1], dict) else {}
            latest_rev = self._safe_float(latest.get("revenue"), 0.0)
            prior_rev = self._safe_float(prior.get("revenue"), 0.0)
            if latest_rev > 0 and prior_rev > 0:
                return (latest_rev / prior_rev) - 1.0

        return None

    def _get_revenue_growth_external(self, ticker: str) -> float | None:
        """Fetch revenue growth from configured external provider (cached)."""
        cfg = self.config.universe
        if not getattr(cfg, "fundamentals_external_enabled", False):
            return None

        provider = str(getattr(cfg, "fundamentals_provider", "")).lower().strip()
        api_key = str(getattr(cfg, "fundamentals_api_key", "")).strip()
        api_secret = str(getattr(cfg, "fundamentals_api_secret", "")).strip()
        if provider != "alpaca" or not api_key or not api_secret:
            return None

        now_et = datetime.now(pytz.timezone("US/Eastern"))
        cache_hours = max(1, int(getattr(cfg, "fundamentals_cache_hours", 24)))
        cached = self._revenue_growth_cache.get(ticker)
        if cached:
            cached_time, cached_value = cached
            if now_et - cached_time < timedelta(hours=cache_hours):
                return cached_value

        try:
            timeout_sec = max(2, int(getattr(cfg, "fundamentals_timeout_seconds", 8)))
            base_url = str(getattr(cfg, "fundamentals_base_url", "https://data.alpaca.markets")).rstrip("/")
            path_template = str(getattr(cfg, "fundamentals_path_template", "/v1beta1/fundamentals/{symbol}"))
            url = f"{base_url}{path_template.format(symbol=ticker)}"
            headers = {
                "APCA-API-KEY-ID": api_key,
                "APCA-API-SECRET-KEY": api_secret,
                "accept": "application/json",
            }

            response = requests.get(url, headers=headers, timeout=timeout_sec)
            response.raise_for_status()
            payload = response.json()

            growth = self._extract_revenue_growth_from_payload(payload)
            if growth is None:
                self.logger.debug(f"Revenue growth unavailable for {ticker}: no parseable field in payload")
                return None

            self._revenue_growth_cache[ticker] = (now_et, growth)
            return growth
        except Exception as e:
            self.logger.debug(f"External fundamentals fetch failed for {ticker}: {e}")
            return None

    def _score_dynamic_candidate(self, ticker: str) -> tuple[float, float, float] | None:
        """Return (score, momentum_1m, revenue_growth_1y) for a candidate ticker."""
        cfg = self.config.universe
        lookback = int(getattr(cfg, "momentum_lookback_days", 22))
        vol_window = int(getattr(cfg, "avg_volume_window_days", 20))
        min_required = max(lookback + 1, vol_window)

        if ticker not in self._contracts:
            contract = Stock(ticker, "SMART", "USD")
            self.ib.qualifyContracts(contract)
            self._contracts[ticker] = contract

        bars = self.ib.reqHistoricalData(
            self._contracts[ticker],
            endDateTime="",
            durationStr="120 D",
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True,
        )
        if not bars or len(bars) < min_required + 1:
            self.logger.debug(f"Universe skip {ticker}: insufficient daily bars")
            return None

        df = util.df(bars)
        if df is None or df.empty or "close" not in df.columns:
            return None

        close_series = df["close"].dropna()
        if len(close_series) < min_required + 1:
            return None

        current_price = self._safe_float(close_series.iloc[-1])
        lookback_price = self._safe_float(close_series.iloc[-(lookback + 1)])
        if current_price <= 0 or lookback_price <= 0:
            return None

        min_price = self._safe_float(getattr(cfg, "min_price", 0.0), 0.0)
        if current_price < min_price:
            self.logger.debug(f"Universe skip {ticker}: price {current_price:.2f} < {min_price:.2f}")
            return None

        if "volume" not in df.columns:
            self.logger.debug(f"Universe skip {ticker}: missing volume")
            return None

        recent = df.tail(vol_window).copy()
        recent["dollar_vol"] = recent["close"] * recent["volume"]
        avg_dollar_vol = self._safe_float(recent["dollar_vol"].mean())
        min_dollar_vol = self._safe_float(getattr(cfg, "min_avg_dollar_volume", 0.0), 0.0)
        if avg_dollar_vol < min_dollar_vol:
            self.logger.debug(
                f"Universe skip {ticker}: avg dollar vol {avg_dollar_vol:,.0f} < {min_dollar_vol:,.0f}"
            )
            return None

        momentum = (current_price / lookback_price) - 1.0
        revenue_growth = self._get_revenue_growth_external(ticker)
        if revenue_growth is None:
            if getattr(cfg, "fundamentals_fallback_to_static", True):
                rev_growth_map = getattr(cfg, "revenue_growth_1y", {})
                revenue_growth = self._safe_float(rev_growth_map.get(ticker, 0.0), 0.0)
            else:
                revenue_growth = 0.0

        w_mom = self._safe_float(getattr(cfg, "momentum_weight", 0.6), 0.6)
        w_rev = self._safe_float(getattr(cfg, "revenue_growth_weight", 0.4), 0.4)
        score = (w_mom * momentum) + (w_rev * revenue_growth)
        return score, momentum, revenue_growth

    def refresh_dynamic_universe_if_due(self):
        """Scheduled refresh hook (safe wrapper)."""
        try:
            self._refresh_dynamic_universe(force=False)
        except Exception as e:
            self.logger.error(f"Universe refresh job error: {e}")

    def _refresh_dynamic_universe(self, force: bool = False):
        """Refresh dynamic universe every N days and subscribe to selected symbols."""
        cfg = self.config.universe
        if not getattr(cfg, "dynamic_enabled", False):
            self._dynamic_symbols = set()
            self._build_active_universe()
            return

        et = pytz.timezone("US/Eastern")
        now_et = datetime.now(et)
        refresh_days = max(1, int(getattr(cfg, "refresh_days", 14)))

        if not force and self._last_universe_refresh is not None:
            days_since = (now_et.date() - self._last_universe_refresh.date()).days
            if days_since < refresh_days:
                return

        core = set(self.config.universe.core_symbols)
        excludes = set(getattr(cfg, "exclude_symbols", []))
        candidates = [
            t for t in getattr(cfg, "candidate_symbols", [])
            if t not in core and t not in excludes
        ]

        if not candidates:
            self.logger.warning("Dynamic universe refresh skipped: no candidate symbols configured")
            self._dynamic_symbols = set()
            self._build_active_universe()
            self._subscribe_market_data()
            return

        scored: list[tuple[str, float, float, float]] = []
        pause_sec = self._safe_float(getattr(cfg, "request_pause_seconds", 0.25), 0.25)
        for ticker in candidates:
            try:
                scored_tuple = self._score_dynamic_candidate(ticker)
                if scored_tuple is None:
                    continue
                score, momentum, revenue_growth = scored_tuple
                scored.append((ticker, score, momentum, revenue_growth))
            except Exception as e:
                self.logger.debug(f"Universe candidate error {ticker}: {e}")
            if pause_sec > 0:
                self.ib.sleep(pause_sec)

        scored.sort(key=lambda x: x[1], reverse=True)
        top_n = max(0, int(getattr(cfg, "top_n_dynamic", 10)))
        selected = [t for t, _, _, _ in scored[:top_n]]
        self._dynamic_symbols = set(selected)
        self._build_active_universe()
        self._last_universe_refresh = now_et

        if selected:
            top_msg = ", ".join(
                [f"{t}(score={s:.3f}, mom={m:.1%}, rev={r:.1%})" for t, s, m, r in scored[:top_n]]
            )
            self.logger.info(f"Universe refreshed ({refresh_days}d cadence): {top_msg}")
        else:
            self.logger.warning("Universe refreshed but no dynamic symbols passed filters")

        self._subscribe_market_data()

    # ================================================================
    #  INDICATORS (computed from historical bars via pandas_ta)
    # ================================================================
    def _compute_spy_indicators(self) -> dict | None:
        """SPY daily: EMA20, EMA50, RSI14 — for regime detection."""
        try:
            contract = self._contracts.get("SPY")
            if not contract:
                return None
            bars = self.ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr="100 D",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True,
            )
            if not bars or len(bars) < 55:
                self.logger.warning("Insufficient SPY daily bars for indicators")
                return None

            df = util.df(bars)
            df["ema_20"] = ta.ema(df["close"], length=20)
            df["ema_50"] = ta.ema(df["close"], length=50)
            df["rsi"] = ta.rsi(df["close"], length=14)

            latest = df.iloc[-1]
            prev = df.iloc[-2]

            return {
                "price": float(latest["close"]),
                "ema_20": float(latest["ema_20"]),
                "ema_50": float(latest["ema_50"]),
                "ema_20_prev": float(prev["ema_20"]),
                "ema_50_prev": float(prev["ema_50"]),
                "rsi": float(latest["rsi"]) if pd.notna(latest["rsi"]) else None,
            }
        except Exception as e:
            self.logger.error(f"SPY indicator error: {e}")
            return None

    def _compute_symbol_indicators(self, ticker: str) -> dict | None:
        """Per-symbol hourly indicators used by strategy portfolio scoring."""
        try:
            contract = self._contracts.get(ticker)
            if not contract:
                return None
            bars = self.ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr="10 D",
                barSizeSetting="1 hour",
                whatToShow="TRADES",
                useRTH=True,
            )
            if not bars or len(bars) < 52:
                self.logger.debug(f"{ticker}: insufficient hourly bars ({len(bars) if bars else 0})")
                return None

            df = util.df(bars)
            macd_df = ta.macd(df["close"], fast=12, slow=26, signal=9)
            if macd_df is not None:
                df = pd.concat([df, macd_df], axis=1)
            df["rsi"] = ta.rsi(df["close"], length=14)
            df["ema_50"] = ta.ema(df["close"], length=50)
            df["atr_14"] = ta.atr(df["high"], df["low"], df["close"], length=14)

            vol = df["volume"].replace(0, pd.NA)
            df["vwap"] = (df["close"] * vol).cumsum() / vol.cumsum()
            df["bb_mid"] = df["close"].rolling(20).mean()
            df["bb_std"] = df["close"].rolling(20).std(ddof=0)
            df["zscore"] = (df["close"] - df["bb_mid"]) / df["bb_std"].replace(0, pd.NA)
            df["vol_sma_20"] = df["volume"].rolling(20).mean()

            latest = df.iloc[-1]
            macd_col = "MACD_12_26_9"
            signal_col = "MACDs_12_26_9"

            opening_range_high = float(latest["high"])
            opening_range_low = float(latest["low"])
            if "date" in df.columns:
                dt_series = pd.to_datetime(df["date"])
                current_day = dt_series.iloc[-1].date()
                day_df = df[dt_series.dt.date == current_day].head(2)
                if len(day_df) >= 1:
                    opening_range_high = float(day_df["high"].max())
                    opening_range_low = float(day_df["low"].min())

            vol_sma = float(latest.get("vol_sma_20", 0) or 0)
            volume_ratio = (float(latest["volume"]) / vol_sma) if vol_sma > 0 else 1.0

            zscore_val = latest.get("zscore", 0)
            if pd.isna(zscore_val):
                zscore_val = 0.0

            return {
                "price": float(latest["close"]),
                "macd": float(latest.get(macd_col, 0)),
                "macd_signal": float(latest.get(signal_col, 0)),
                "rsi": float(latest.get("rsi", 50)),
                "ema_50": float(latest.get("ema_50", 0)),
                "atr_14": float(latest.get("atr_14", 0)),
                "vwap": float(latest.get("vwap", 0)),
                "zscore": float(zscore_val),
                "volume_ratio": volume_ratio,
                "opening_range_high": opening_range_high,
                "opening_range_low": opening_range_low,
            }
        except Exception as e:
            self.logger.error(f"{ticker} indicator error: {e}")
            return None

    def _compute_portfolio_signal(self, ticker: str, ind: dict) -> tuple[float, str]:
        """Blend enabled strategy components into one entry score."""
        price = ind["price"]
        macd = ind["macd"]
        macd_sig = ind["macd_signal"]
        rsi_val = ind["rsi"]
        ema_val = ind["ema_50"]
        vwap = ind["vwap"]
        zscore = ind["zscore"]
        volume_ratio = ind["volume_ratio"]
        orb_high = ind["opening_range_high"]

        # ── Live feed signals ──────────────────────────────────────────────
        sentiment_raw = self._feeds.get_sentiment(ticker)        # [-1, 1]
        ob_imbalance  = self._feeds.get_ob_imbalance(ticker)     # [-1, 1]

        component_scores: dict[str, float] = {
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

        # Sentiment component: positive news consensus required (> +0.10 threshold)
        if sentiment_raw > 0.10:
            component_scores["sentiment"] = min(1.0, (sentiment_raw - 0.10) / 0.90)

        # Market-making / order-book component: bid-heavy quote stream required
        if ob_imbalance > 0.10:
            component_scores["market_making"] = min(1.0, (ob_imbalance - 0.10) / 0.90)

        weighted = 0.0
        active = []
        for name, score in component_scores.items():
            weight = self._strategy_weights.get(name, 0.0)
            if score > 0 and weight > 0:
                active.append(f"{name}:{score:.2f}")
            weighted += weight * score

        return weighted, ", ".join(active) if active else "none"

    def flatten_intraday_positions(self):
        """Flatten algorithm-managed positions before close for intraday risk control."""
        if not self._is_market_open():
            self._log_market_closed_skip("flatten_intraday_positions")
            return

        positions = self._get_positions()
        for ticker in list(self._algo_managed_positions):
            pos = positions.get(ticker)
            if not pos:
                continue
            qty = int(pos.get("qty", 0))
            if qty <= 0:
                continue
            sold = self._place_market_sell(ticker, qty, "eod_flatten")
            if sold:
                self._algo_managed_positions.discard(ticker)
                self._entry_strategy_reason.pop(ticker, None)
                self.logger.info(f"EOD FLATTEN: {ticker} qty={qty}")

    # ================================================================
    #  MARKET REGIME DETECTION
    # ================================================================
    def detect_market_regime(self):
        """SPY-based regime detection: BULL / BEAR / NEUTRAL."""
        try:
            if not self._is_market_open():
                self._log_market_closed_skip("detect_market_regime")
                return

            spy = self._compute_spy_indicators()
            if not spy:
                self.logger.warning("Cannot compute SPY indicators — keeping regime")
                return

            ema_20, ema_50 = spy["ema_20"], spy["ema_50"]
            ema_20_prev, ema_50_prev = spy["ema_20_prev"], spy["ema_50_prev"]
            spy_price = spy["price"]
            rsi_val = spy["rsi"]

            previous_regime = self.market_regime

            # Structural signals
            price_above_50 = spy_price > ema_50
            price_below_50 = spy_price < ema_50
            ema_structure_bull = ema_20 > ema_50
            ema_structure_bear = ema_20 < ema_50

            # Momentum signals
            ema_20_rising = ema_20 > ema_20_prev
            momentum_bear = (not ema_20_rising) and spy_price < ema_20

            # Decision (with hysteresis)
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

            rsi_str = f" | RSI: {rsi_val:.1f}" if rsi_val else ""
            self.logger.info(
                f"Regime: {self.market_regime} | SPY: {spy_price:.2f} "
                f"| EMA20: {ema_20:.2f} | EMA50: {ema_50:.2f} "
                f"| Rising: {ema_20_rising}{rsi_str}"
            )

            if previous_regime != self.market_regime:
                self.logger.warning(
                    f"REGIME CHANGE: {previous_regime} → {self.market_regime}"
                )
                self._send_alert_email(
                    f"Regime Change: {previous_regime} → {self.market_regime}",
                    f"SPY: {spy_price:.2f} | EMA20: {ema_20:.2f} | EMA50: {ema_50:.2f}",
                )

        except Exception as e:
            self.logger.error(f"Regime detection error: {e}")

    # ================================================================
    #  MARKET HOURS CHECK
    # ================================================================
    def _is_market_open(self) -> bool:
        """Check if US equity market is open (ET time-based)."""
        try:
            import pytz

            et = pytz.timezone("US/Eastern")
            now_et = datetime.now(et)
            current_time = now_et.time()
            is_weekday = now_et.weekday() < 5
            return is_weekday and dt_time(9, 30) <= current_time <= dt_time(16, 0)
        except Exception:
            return True  # Safe default

    def _roll_schedule_stats_day(self):
        """Reset daily schedule counters when ET day rolls over."""
        try:
            today_et = datetime.now(pytz.timezone("US/Eastern")).date()
            if today_et != self._schedule_stats_date:
                self._schedule_stats_date = today_et
                self._schedule_trigger_count = 0
                self._schedule_market_closed_skips = 0
                self._last_closed_log_key.clear()
        except Exception:
            pass

    def _log_market_closed_skip(self, job_name: str):
        """Log when scheduled jobs are skipped because market is closed."""
        try:
            self._roll_schedule_stats_day()
            et = pytz.timezone("US/Eastern")
            now_et = datetime.now(et)
            current_key = f"{now_et.strftime('%Y-%m-%d %H:%M')}|{job_name}"
            if self._last_closed_log_key.get(job_name) == current_key:
                return
            self._last_closed_log_key[job_name] = current_key
            self._schedule_market_closed_skips += 1
            self.logger.info(
                f"Market closed - skipping {job_name} at "
                f"{now_et.strftime('%Y-%m-%d %H:%M:%S %Z')}"
            )
        except Exception:
            self.logger.info(f"Market closed - skipping {job_name}")

    # ================================================================
    #  POSITION SIZING
    # ================================================================
    def _calculate_position_size(self, ticker: str, price: float) -> int:
        try:
            available_cash = self.cash
            safe_available = available_cash * 0.75
            target_value = min(safe_available * 0.85, 6500)
            if target_value < 4000:
                return 0
            qty = int(target_value / price)
            if qty <= 0:
                return 0

            # IB fee estimate
            fee = self._estimate_fee(qty, price)
            total_cost = qty * price + fee
            if total_cost > target_value:
                qty = int((target_value - fee) / price)
                fee = self._estimate_fee(qty, price)
                total_cost = qty * price + fee

            return qty if qty > 0 and total_cost >= 4000 else 0
        except Exception as e:
            self.logger.error(f"Position sizing error: {e}")
            return 0

    @staticmethod
    def _estimate_fee(qty: int, price: float) -> float:
        if qty <= 0 or price <= 0:
            return 0.0
        trade_value = qty * price
        fee = max(0.0035 * qty, 0.35)
        fee = min(fee, trade_value * 0.01)
        return fee

    # ================================================================
    #  ORDER EXECUTION
    # ================================================================
    def _place_market_buy(self, ticker: str, qty: int) -> bool:
        contract = self._contracts.get(ticker)
        if not contract:
            self.logger.error(f"No contract for {ticker}")
            return False
        order = MarketOrder("BUY", qty)
        order.tif = "DAY"
        trade = self.ib.placeOrder(contract, order)
        self.logger.info(f"ORDER PLACED: BUY {qty} {ticker}")

        # Wait up to 30s for fill
        for _ in range(30):
            self.ib.sleep(1)
            if trade.isDone():
                break
        if trade.orderStatus.status == "Filled":
            fill_price = trade.orderStatus.avgFillPrice
            self.logger.info(f"FILLED: BUY {qty} {ticker} @ ${fill_price:.2f}")
            return True
        else:
            self.logger.warning(f"Order status: {trade.orderStatus.status}")
            return trade.orderStatus.status in ("Filled", "Submitted", "PreSubmitted")

    def _place_market_sell(self, ticker: str, qty: int, reason: str = "") -> bool:
        contract = self._contracts.get(ticker)
        if not contract:
            self.logger.error(f"No contract for {ticker}")
            return False
        order = MarketOrder("SELL", qty)
        order.tif = "DAY"
        trade = self.ib.placeOrder(contract, order)
        self.logger.info(f"ORDER PLACED: SELL {qty} {ticker} ({reason})")

        for _ in range(30):
            self.ib.sleep(1)
            if trade.isDone():
                break
        if trade.orderStatus.status == "Filled":
            fill_price = trade.orderStatus.avgFillPrice
            self.logger.info(f"FILLED: SELL {qty} {ticker} @ ${fill_price:.2f} ({reason})")
            return True
        else:
            self.logger.warning(f"Sell order status: {trade.orderStatus.status}")
            return False

    # ================================================================
    #  SIGNAL EVALUATION (entry + exit)
    # ================================================================
    def evaluate_signals(self):
        """Main signal evaluation — called on schedule."""
        try:
            if not self._is_market_open():
                self._log_market_closed_skip("evaluate_signals")
                return
            if not self._trading_enabled:
                self.logger.warning("Trading disabled (circuit breaker)")
                return
            if not self.ib.isConnected():
                self.logger.warning("Not connected to IB — skipping evaluation")
                return

            # Risk checks
            current_equity = self.net_liquidation
            if current_equity <= 0:
                return

            daily_loss = self._starting_cash - current_equity
            if daily_loss > self.config.risk.max_daily_loss:
                self.logger.critical(f"DAILY LOSS LIMIT: ${daily_loss:,.2f}")
                self._trading_enabled = False
                self._send_alert_email(
                    "CIRCUIT BREAKER: Daily Loss Limit",
                    f"Loss: ${daily_loss:,.2f} exceeds ${self.config.risk.max_daily_loss}",
                )
                return

            if current_equity > self.peak_equity:
                self.peak_equity = current_equity

            drawdown = (
                (self.peak_equity - current_equity) / self.peak_equity
                if self.peak_equity > 0
                else 0
            )
            if drawdown > self.config.risk.max_drawdown_pct:
                self.logger.critical(f"DRAWDOWN LIMIT: {drawdown:.1%}")
                self._trading_enabled = False
                self._send_alert_email(
                    "CIRCUIT BREAKER: Max Drawdown",
                    f"Drawdown: {drawdown:.1%} > {self.config.risk.max_drawdown_pct:.1%}",
                )
                return

            self._evaluate_exits_and_entries(current_equity)

        except Exception as e:
            self.logger.error(f"Signal evaluation error: {e}")

    def _evaluate_exits_and_entries(self, current_equity: float):
        """Core exit + entry logic."""
        now = datetime.now()
        positions = self._get_positions()

        # Separate algo-managed vs manual positions
        algo_positions = {
            t: p for t, p in positions.items() if t in self._algo_managed_positions
        }
        all_invested_tickers = set(positions.keys()) - {"SPY"}
        algo_invested_tickers = set(algo_positions.keys())

        # ── EXIT LOGIC ───────────────────────────────────────────
        for ticker in list(algo_invested_tickers):
            pos = positions.get(ticker)
            if not pos:
                continue
            try:
                current_price = pos["market_price"]
                if current_price <= 0:
                    current_price = self._get_price(ticker)
                if current_price <= 0:
                    continue

                avg_entry = pos["avg_cost"]
                qty = int(pos["qty"])
                pnl_pct = (current_price - avg_entry) / avg_entry if avg_entry > 0 else 0
                pnl_dollar = qty * (current_price - avg_entry)
                held_time = now - self.entry_time.get(ticker, now)

                self.highest_price[ticker] = max(
                    self.highest_price.get(ticker, current_price), current_price
                )

                should_exit = False
                reason = ""

                self.logger.debug(f"CHECK {ticker}: pnl={pnl_pct:.4f} held={held_time}")

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

                # Gap-down protection
                if pnl_pct <= -0.05:
                    self.logger.critical(
                        f"GAP DOWN: {ticker} at {pnl_pct:.2%}"
                    )
                    should_exit, reason = True, "gap_protection"

                if should_exit:
                    sold = self._place_market_sell(ticker, qty, reason)
                    if sold:
                        trade_record = (
                            f"{now.strftime('%Y-%m-%d %H:%M')} SELL {ticker} "
                            f"- Qty: {qty} @ ${current_price:.2f} | P&L: ${pnl_dollar:.2f}"
                        )
                        self.trade_history.append(trade_record)
                        if pnl_dollar > 0:
                            self.winning_trades.append(trade_record)
                        else:
                            self.losing_trades.append(trade_record)
                        self._update_symbol_performance(ticker, pnl_dollar)
                        self._algo_managed_positions.discard(ticker)
                        self._entry_strategy_reason.pop(ticker, None)
                        self.logger.info(
                            f"EXIT {ticker}: {reason}, P&L ${pnl_dollar:.0f}"
                        )

            except Exception as e:
                self.logger.error(f"Exit error for {ticker}: {e}")

        # ── ENTRY LOGIC ──────────────────────────────────────────
        if self.market_regime == "BEAR":
            self.logger.debug("SKIP ENTRY: BEAR regime")
            return

        portfolio_return = (
            (current_equity - self._starting_cash) / self._starting_cash
            if self._starting_cash > 0
            else 0
        )

        # Dynamic max positions by regime
        max_positions = self.config.trading.max_positions
        if self.market_regime == "BULL" and portfolio_return > 0.02:
            max_allowed = max_positions
        elif self.market_regime == "BULL":
            max_allowed = min(3, max_positions)
        elif self.market_regime == "NEUTRAL":
            max_allowed = min(2, max_positions)
        else:
            max_allowed = 1

        algo_count = len(
            [t for t in self._algo_managed_positions if t in positions]
        )
        if algo_count >= max_allowed:
            self.logger.debug(
                f"SKIP ENTRY: {algo_count}/{max_allowed} algo positions "
                f"({self.market_regime})"
            )
            return

        available_cash = self.cash
        if available_cash < 4000:
            self.logger.debug(f"SKIP ENTRY: cash ${available_cash:.0f} < $4,000")
            return

        # Score candidates
        candidates = []
        for ticker in self._active_universe:
            if ticker == "SPY":
                continue
            if ticker in positions:
                continue

            indicators = self._compute_symbol_indicators(ticker)
            if not indicators:
                continue

            price = indicators["price"]
            if price <= 0:
                continue

            score, reason = self._compute_portfolio_signal(ticker, indicators)
            if score >= self._min_portfolio_signal_score:
                candidates.append((ticker, score, price, reason))

            # Rate-limit IB API requests (pacing)
            self.ib.sleep(0.5)

        if not candidates:
            self.logger.debug("No entry candidates found")
            return

        candidates.sort(key=lambda x: x[1], reverse=True)
        best_ticker, best_score, current_price, reason = candidates[0]

        # Re-fetch live price
        live_price = self._get_price(best_ticker)
        if live_price > 0:
            current_price = live_price

        qty = self._calculate_position_size(best_ticker, current_price)
        if qty <= 0:
            return

        bought = self._place_market_buy(best_ticker, qty)
        if bought:
            self._algo_managed_positions.add(best_ticker)
            self.entry_time[best_ticker] = now
            self.entry_price[best_ticker] = current_price
            self.highest_price[best_ticker] = current_price
            self._entry_strategy_reason[best_ticker] = reason

            trade_record = (
                f"{now.strftime('%Y-%m-%d %H:%M')} BUY {best_ticker} "
                f"- Qty: {qty} @ ${current_price:.2f} | score={best_score:.2f} | {reason}"
            )
            self.trade_history.append(trade_record)
            self.logger.info(
                f"BUY {best_ticker}: qty={qty}, ${current_price:.2f}, score={best_score:.2f}, {reason}"
            )

    def _update_symbol_performance(self, ticker: str, pnl: float):
        perf = self.symbol_performance[ticker]
        perf["trades"] += 1
        perf["total_pnl"] += pnl
        if pnl > 0:
            perf["wins"] += 1
            perf["consecutive_losses"] = 0
        else:
            perf["consecutive_losses"] += 1
        perf["win_rate"] = (
            perf["wins"] / perf["trades"] * 100 if perf["trades"] > 0 else 0
        )

    # ================================================================
    #  DAILY RISK SUMMARY
    # ================================================================
    def daily_risk_summary(self):
        if not self._is_market_open():
            self._log_market_closed_skip("daily_risk_summary")
            return
        try:
            equity = self.net_liquidation
            pnl = equity - self._starting_cash
            ret = pnl / self._starting_cash if self._starting_cash > 0 else 0
            self.logger.info(f"Daily: ${equity:,.0f} ({ret:.2%})")

            # Reset daily trading state at end of day
            self._trading_enabled = True
        except Exception as e:
            self.logger.error(f"Daily summary error: {e}")

    # ================================================================
    #  EMAIL (portfolio summaries + alerts)
    # ================================================================
    def send_portfolio_summary_email(self):
        if not self._is_market_open():
            self._log_market_closed_skip("send_portfolio_summary_email")
            return
        try:
            et = pytz.timezone("US/Eastern")
            now_et = datetime.now(et)
            self._roll_schedule_stats_day()
            equity = self.net_liquidation
            cash_val = self.cash
            total_return = (
                (equity - self._starting_cash) / self._starting_cash
                if self._starting_cash > 0
                else 0
            )
            daily_pnl = equity - self._starting_cash
            positions = self._get_positions()

            positions_summary = []
            for ticker, pos in positions.items():
                if ticker == "SPY":
                    continue
                price = pos["market_price"]
                avg = pos["avg_cost"]
                qty = int(pos["qty"])
                if price <= 0:
                    live_price = self._get_price(ticker)
                    if live_price > 0:
                        price = live_price
                    else:
                        # Prevent false -100% P&L when live quote is temporarily unavailable.
                        price = avg
                pnl = qty * (price - avg)
                pnl_pct = (price - avg) / avg if avg > 0 else 0
                pos_type = "ALGO" if ticker in self._algo_managed_positions else "MANUAL"
                entry_dt = self.entry_time.get(ticker)
                if entry_dt is None:
                    held_str = "N/A"
                else:
                    held_delta = now_et - entry_dt
                    if held_delta.total_seconds() < 0:
                        held_delta = timedelta(0)
                    held_str = str(held_delta).split('.')[0]
                positions_summary.append(
                    f"  {ticker:<6} | Qty:{qty:<5} | Entry:${avg:<8.2f} | "
                    f"Now:${price:<8.2f} | P&L:${pnl:<8.2f} ({pnl_pct:>6.2%}) | "
                    f"{pos_type} | {held_str}"
                )

            body = (
                f"{'=' * 70}\n"
                f"PORTFOLIO SUMMARY\n"
                f"{'=' * 70}\n\n"
                f"Equity:        ${equity:,.2f}\n"
                f"Total Return:  {total_return:.2%}\n"
                f"Daily P&L:     ${daily_pnl:,.2f}\n"
                f"Cash:          ${cash_val:,.2f}\n"
                f"Regime:        {self.market_regime}\n"
                f"Algo Positions: {len(self._algo_managed_positions)}/{self.config.trading.max_positions}\n\n"
                f"POSITIONS\n{'-' * 70}\n"
            )
            if positions_summary:
                body += "\n".join(positions_summary) + "\n"
            else:
                body += "  No open positions\n"

            body += f"\nRECENT TRADES\n{'-' * 70}\n"
            recent = list(self.trade_history)[-10:]
            if recent:
                body += "\n".join(f"  {t}" for t in recent) + "\n"
            else:
                body += "  No recent trades\n"

            if now_et.hour == 16 and now_et.minute <= 5:
                body += (
                    f"\nEOD SCHEDULE SUMMARY\n{'-' * 70}\n"
                    f"  Scheduled triggers today: {self._schedule_trigger_count}\n"
                    f"  Market-closed skips today: {self._schedule_market_closed_skips}\n"
                )

            body += f"\n{'=' * 70}\n"
            body += f"Generated (US/Eastern): {now_et.strftime('%Y-%m-%d %H:%M:%S')}\n"

            self._send_email(f"Portfolio Summary - {now_et.strftime('%Y-%m-%d %H:%M')} ET", body)

        except Exception as e:
            self.logger.error(f"Portfolio email error: {e}")

    def send_weekly_summary_email(self):
        try:
            now_et = datetime.now(pytz.timezone("US/Eastern"))
            equity = self.net_liquidation
            cash_val = self.cash
            total_return = (
                (equity - self._starting_cash) / self._starting_cash
                if self._starting_cash > 0
                else 0
            )
            drawdown = (
                (self.peak_equity - equity) / self.peak_equity
                if self.peak_equity > 0
                else 0
            )

            wins = len(self.winning_trades)
            losses = len(self.losing_trades)
            total = wins + losses
            win_rate = (wins / total * 100) if total > 0 else 0

            positions = self._get_positions()
            pos_lines = []
            total_pos_value = 0
            for ticker, pos in positions.items():
                if ticker == "SPY":
                    continue
                price = pos["market_price"]
                qty = int(pos["qty"])
                value = qty * price
                total_pos_value += value
                pnl = qty * (price - pos["avg_cost"])
                pos_lines.append(
                    f"  {ticker:<6} | Qty:{qty:<5} | "
                    f"Value:${value:,.0f} | P&L:${pnl:,.0f}"
                )

            body = (
                f"{'=' * 80}\n"
                f"WEEKLY PORTFOLIO ANALYSIS\n"
                f"{'=' * 80}\n\n"
                f"PERFORMANCE\n{'-' * 80}\n"
                f"Equity:          ${equity:,.2f}\n"
                f"Starting:        ${self._starting_cash:,.2f}\n"
                f"Return:          {total_return:.2%}\n"
                f"Cash:            ${cash_val:,.2f}\n"
                f"Peak:            ${self.peak_equity:,.2f}\n"
                f"Drawdown:        {drawdown:.2%}\n\n"
                f"TRADING STATS\n{'-' * 80}\n"
                f"Total Trades:    {total}\n"
                f"Wins:            {wins}\n"
                f"Losses:          {losses}\n"
                f"Win Rate:        {win_rate:.1f}%\n"
                f"Regime:          {self.market_regime}\n\n"
                f"HOLDINGS\n{'-' * 80}\n"
            )
            if pos_lines:
                body += "\n".join(pos_lines) + "\n"
                body += f"\nTotal Position Value: ${total_pos_value:,.0f}\n"
            else:
                body += "  No holdings\n"

            body += f"\nRECENT TRADES (last 15)\n{'-' * 80}\n"
            for t in list(self.trade_history)[-15:]:
                body += f"  {t}\n"

            body += f"\n{'=' * 80}\n"
            body += f"Weekly Report (US/Eastern): {now_et.strftime('%Y-%m-%d %H:%M:%S')}\n"

            self._send_email(
                f"Weekly Portfolio Summary - {now_et.strftime('%Y-%m-%d')} ET",
                body,
            )
        except Exception as e:
            self.logger.error(f"Weekly email error: {e}")

    def _send_email(self, subject: str, body: str):
        """Send email via SMTP."""
        try:
            cfg = self.config.email
            if not all([cfg.smtp_server, cfg.sender_email, cfg.sender_password]):
                self.logger.debug("Email not configured — skipping")
                return

            msg = MIMEMultipart()
            msg["From"] = cfg.sender_email
            msg["To"] = cfg.recipient_email
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain"))

            server = smtplib.SMTP(cfg.smtp_server, cfg.smtp_port)
            server.starttls()
            server.login(cfg.sender_email, cfg.sender_password)
            server.sendmail(cfg.sender_email, cfg.recipient_email, msg.as_string())
            server.quit()
            self.logger.info(f"Email sent: {subject}")
        except Exception as e:
            self.logger.error(f"Email send error: {e}")

    def _send_alert_email(self, subject: str, body: str):
        self._send_email(f"[ALERT] {subject}", body)

    # ================================================================
    #  SCHEDULER SETUP
    # ================================================================
    def _setup_scheduler(self):
        """Configure APScheduler jobs matching the original QC schedule (ET)."""
        mf = "mon-fri"
        et = pytz.timezone("US/Eastern")
        jobs = [
            # Dynamic universe refresh
            (9, 20, self.refresh_dynamic_universe_if_due, "universe_refresh"),
            # Regime detection
            (9,  25, self.detect_market_regime,         "regime_morning"),
            (12,  0, self.detect_market_regime,         "regime_midday"),
            (15,  0, self.detect_market_regime,         "regime_afternoon"),
            # Signal evaluation
            (9,  30, self.evaluate_signals,             "signals_morning"),
            (12, 30, self.evaluate_signals,             "signals_midday"),
            (15, 30, self.evaluate_signals,             "signals_afternoon"),
            (15, 55, self.flatten_intraday_positions,   "flatten_eod"),
            # Portfolio emails
            (9,  35, self.send_portfolio_summary_email, "email_morning"),
            (12, 30, self.send_portfolio_summary_email, "email_midday"),
            (15, 30, self.send_portfolio_summary_email, "email_afternoon"),
            (16,  0, self.send_portfolio_summary_email, "email_close"),
            # Daily risk summary
            (16,  0, self.daily_risk_summary,           "daily_risk"),
        ]

        for hour, minute, func, job_id in jobs:
            self.scheduler.add_job(
                self._safe_run(func),
                CronTrigger(hour=hour, minute=minute, day_of_week=mf, timezone=et),
                id=job_id,
                replace_existing=True,
            )

        # Weekly summary — Friday 16:00
        self.scheduler.add_job(
            self._safe_run(self.send_weekly_summary_email),
            CronTrigger(hour=16, minute=0, day_of_week="fri", timezone=et),
            id="weekly_summary",
            replace_existing=True,
        )

        self.logger.info(
            f"Scheduled {len(jobs) + 1} jobs (US/Eastern, Mon-Fri)"
        )

    def _safe_run(self, func):
        """Wrap scheduled functions with error handling."""
        def wrapper():
            try:
                self._roll_schedule_stats_day()
                self._schedule_trigger_count += 1
                self.logger.info(f"Scheduled job triggered: {func.__name__}")
                if not self.ib.isConnected():
                    self.logger.warning(
                        f"IB not connected — skipping {func.__name__}"
                    )
                    self._reconnect()
                    return
                func()
            except Exception as e:
                self.logger.error(f"Job {func.__name__} error: {e}")
        wrapper.__name__ = func.__name__
        return wrapper

    # ================================================================
    #  MAIN ENTRY POINT
    # ================================================================
    def start(self):
        """Connect, subscribe, schedule, run."""
        # Handle SIGTERM/SIGINT for clean shutdown
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        self._connect()
        self._refresh_dynamic_universe(force=True)
        self._subscribe_market_data()

        # Start live feeds (non-blocking daemon threads)
        self._feeds.start()

        # Run initial regime detection
        self.detect_market_regime()

        self._setup_scheduler()
        self.scheduler.start()

        self.logger.info("=" * 60)
        self.logger.info("TRADING BOT STARTED")
        self.logger.info(f"Mode: {self.config.general.mode}")
        self.logger.info(f"Core Symbols: {self.config.universe.core_symbols}")
        self.logger.info(f"Dynamic Symbols: {sorted(self._dynamic_symbols)}")
        self.logger.info(f"Active Universe: {self._active_universe}")
        self.logger.info(f"Regime: {self.market_regime}")
        self.logger.info(f"Equity: ${self.net_liquidation:,.2f}")
        self.logger.info("=" * 60)

        self._send_alert_email(
            "Trading Bot Started",
            f"Mode: {self.config.general.mode}\n"
            f"Equity: ${self.net_liquidation:,.2f}\n"
            f"Regime: {self.market_regime}\n"
            f"Core Symbols: {', '.join(self.config.universe.core_symbols)}\n"
            f"Dynamic Symbols: {', '.join(sorted(self._dynamic_symbols))}\n"
            f"Active Universe: {', '.join(self._active_universe)}",
        )

        # Main event loop — process IB events, keep alive
        try:
            while self._running:
                self.ib.sleep(30)
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            self.shutdown()

    def shutdown(self):
        """Graceful shutdown."""
        self.logger.info("Shutting down...")
        self._running = False
        try:
            self._feeds.stop()
        except Exception:
            pass
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:
            pass
        try:
            if self.ib.isConnected():
                self.ib.disconnect()
        except Exception:
            pass
        self.logger.info("Shutdown complete.")

    def _signal_handler(self, signum, frame):
        self.logger.info(f"Received signal {signum}")
        self._running = False


# ====================================================================
#  CLI
# ====================================================================
if __name__ == "__main__":
    bot = TradingBot()
    bot.start()
