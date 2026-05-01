#!/usr/bin/env python3
"""
Trading Bot — Alpaca native version
Connects to Alpaca Markets REST API (paper or live).
Architecture: Scheduler → Bot → Alpaca REST API → Markets
"""

import logging
import logging.handlers
import signal
import sys
import os
import smtplib
import time
import requests
from datetime import datetime, timedelta, time as dt_time
from collections import defaultdict, deque
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import pandas as pd
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    GetOrdersRequest,
    ClosePositionRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderStatus as AlpacaOrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import (
    StockBarsRequest,
    StockLatestBarRequest,
    StockLatestQuoteRequest,
)
from alpaca.data.timeframe import TimeFrame

from bot_config import BOT_Config
from live_feeds import FeedManager


class AlpacaTradingBot:
    """
    Production trading bot using the Alpaca Markets API (alpaca-py SDK).
    Paper-trading by default; set ALPACA_PAPER=false for live.
    """

    def __init__(self):
        self.config = BOT_Config()
        self._setup_logging()
        self.logger.info("Initializing Alpaca Trading Bot...")

        # ── Alpaca clients ──────────────────────────────────────────────
        cfg = self.config.alpaca
        self.trading_client = TradingClient(
            cfg.api_key, cfg.api_secret, paper=cfg.paper
        )
        self.data_client = StockHistoricalDataClient(cfg.api_key, cfg.api_secret)

        self.scheduler = BackgroundScheduler(timezone=pytz.timezone("US/Eastern"))

        # ── State ─────────────────────────────────────────────────────
        self._starting_cash: float = self.config.general.starting_capital
        self.peak_equity: float = self._starting_cash
        self.market_regime: str = "NEUTRAL"
        self.trading_style: str = str(
            getattr(self.config.general, "trading_style", "INTRADAY")
        ).strip().upper()
        if self.trading_style not in {"INTRADAY", "SWING"}:
            self.trading_style = "INTRADAY"
        self._style_settings = self._build_style_settings()

        self._dynamic_symbols: set[str] = set()
        self._active_universe: list[str] = list(self.config.universe.core_symbols)
        self._last_universe_refresh: datetime | None = None
        self._revenue_growth_cache: dict[str, tuple[datetime, float]] = {}

        self._algo_managed_positions: set[str] = set()
        self.entry_time: dict[str, datetime] = {}
        self.entry_price: dict[str, float] = {}
        self.highest_price: dict[str, float] = {}
        self._entry_strategy_reason: dict[str, str] = {}
        self._entry_timestamps: deque = deque(maxlen=200)
        self._last_entry_time: dict[str, datetime] = {}
        self._last_exit_time: dict[str, datetime] = {}

        self._strategy_weights: dict[str, float] = {
            "momentum":       0.30,
            "mean_reversion": 0.20,
            "orb":            0.15,
            "vwap_twap":      0.20,
            "market_making":  0.05,
            "stat_arb":       0.00,
            "sentiment":      0.10,
        }

        # ── Live feeds ─────────────────────────────────────────────────
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
        self._last_closed_log_key: dict[str, str] = {}
        et_now = datetime.now(pytz.timezone("US/Eastern"))
        self._schedule_stats_date = et_now.date()
        self._schedule_trigger_count = 0
        self._schedule_market_closed_skips = 0
        self.symbol_performance: dict = defaultdict(
            lambda: {"trades": 0, "wins": 0, "total_pnl": 0.0,
                     "consecutive_losses": 0, "win_rate": 0.0}
        )

        self._trading_enabled: bool = True
        self._running: bool = True

    def _build_style_settings(self) -> dict:
        cfg = self.config.trading
        risk = self.config.risk
        if self.trading_style == "SWING":
            return {
                "signal_source": "daily",
                "flatten_eod": bool(getattr(cfg, "swing_flatten_positions", False)),
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
            "signal_source": "intraday",
            "flatten_eod": bool(getattr(cfg, "intraday_flatten_positions", True)),
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

    def _is_intraday_style(self) -> bool:
        return self.trading_style == "INTRADAY"

    def _is_swing_style(self) -> bool:
        return self.trading_style == "SWING"

    # ================================================================
    #  LOGGING
    # ================================================================
    def _setup_logging(self):
        preferred_log_dir = self.config.monitoring.log_dir
        try:
            os.makedirs(preferred_log_dir, exist_ok=True)
            log_dir = preferred_log_dir
        except PermissionError:
            log_dir = os.path.join(os.getcwd(), "logs")
            os.makedirs(log_dir, exist_ok=True)

        self.logger = logging.getLogger("AlpacaBot")
        self.logger.setLevel(logging.DEBUG)

        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )

        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(fmt)
        self.logger.addHandler(ch)

        fh = logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, "alpacabot.log"),
            maxBytes=10_000_000,
            backupCount=5,
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        self.logger.addHandler(fh)

    # ================================================================
    #  ALPACA ACCOUNT HELPERS
    # ================================================================
    def _get_account(self):
        """Return Alpaca account object (cached at most 5 s to avoid throttling)."""
        return self.trading_client.get_account()

    @property
    def cash(self) -> float:
        try:
            return float(self._get_account().cash)
        except Exception:
            return 0.0

    @property
    def net_liquidation(self) -> float:
        try:
            return float(self._get_account().portfolio_value)
        except Exception:
            return 0.0

    def _get_positions(self) -> dict:
        """Return {ticker: {qty, avg_cost, market_price, side}}."""
        result = {}
        try:
            for pos in self.trading_client.get_all_positions():
                qty = float(pos.qty)
                if qty == 0:
                    continue
                ticker = pos.symbol
                result[ticker] = {
                    "qty": abs(qty),
                    "avg_cost": float(pos.avg_entry_price),
                    "market_price": float(pos.current_price or 0),
                    "side": "LONG" if qty > 0 else "SHORT",
                }
        except Exception as exc:
            self.logger.error(f"_get_positions error: {exc}")
        return result

    def _get_price(self, ticker: str) -> float:
        """Get latest price via Alpaca latest-bar endpoint."""
        try:
            req = StockLatestBarRequest(symbol_or_symbols=[ticker])
            bars = self.data_client.get_stock_latest_bar(req)
            bar = bars.get(ticker)
            if bar and bar.close and bar.close > 0:
                return float(bar.close)
        except Exception as exc:
            self.logger.debug(f"_get_price {ticker}: {exc}")
        return 0.0

    # ================================================================
    #  DYNAMIC UNIVERSE SELECTION
    # ================================================================
    @staticmethod
    def _safe_float(value, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _build_active_universe(self):
        merged = list(self.config.universe.core_symbols) + sorted(self._dynamic_symbols)
        seen: set = set()
        self._active_universe = [t for t in merged if not (t in seen or seen.add(t))]
        self._feeds.update_symbols(self._active_universe)

    def _get_daily_bars_df(self, ticker: str, days: int = 130) -> pd.DataFrame:
        """Fetch daily bars via Alpaca SDK, return clean DataFrame."""
        try:
            req = StockBarsRequest(
                symbol_or_symbols=[ticker],
                timeframe=TimeFrame.Day,
                start=datetime.now() - timedelta(days=days),
                end=datetime.now(),
            )
            bar_set = self.data_client.get_stock_bars(req)
            df = bar_set.df
            if isinstance(df.index, pd.MultiIndex):
                # (symbol, timestamp) multi-index — drop symbol level
                df = df.xs(ticker, level="symbol") if ticker in df.index.get_level_values("symbol") else pd.DataFrame()
            df = df.copy()
            df.columns = [str(c).lower() for c in df.columns]
            if df.index.tz is not None:
                df.index = df.index.tz_convert("US/Eastern")
            return df
        except Exception as exc:
            self.logger.debug(f"_get_daily_bars_df {ticker}: {exc}")
            return pd.DataFrame()

    def _get_hourly_bars_df(self, ticker: str, days: int = 12) -> pd.DataFrame:
        """Fetch hourly bars via Alpaca SDK, return clean DataFrame."""
        try:
            req = StockBarsRequest(
                symbol_or_symbols=[ticker],
                timeframe=TimeFrame.Hour,
                start=datetime.now() - timedelta(days=days),
                end=datetime.now(),
            )
            bar_set = self.data_client.get_stock_bars(req)
            df = bar_set.df
            if isinstance(df.index, pd.MultiIndex):
                df = df.xs(ticker, level="symbol") if ticker in df.index.get_level_values("symbol") else pd.DataFrame()
            df = df.copy()
            df.columns = [str(c).lower() for c in df.columns]
            if df.index.tz is not None:
                df.index = df.index.tz_convert("US/Eastern")
            return df
        except Exception as exc:
            self.logger.debug(f"_get_hourly_bars_df {ticker}: {exc}")
            return pd.DataFrame()

    def _get_minute_bars_df(self, ticker: str, days: int = 2) -> pd.DataFrame:
        """Fetch minute bars for session VWAP and opening-range calculations."""
        try:
            req = StockBarsRequest(
                symbol_or_symbols=[ticker],
                timeframe=TimeFrame.Minute,
                start=datetime.now() - timedelta(days=days),
                end=datetime.now(),
            )
            bar_set = self.data_client.get_stock_bars(req)
            df = bar_set.df
            if isinstance(df.index, pd.MultiIndex):
                df = df.xs(ticker, level="symbol") if ticker in df.index.get_level_values("symbol") else pd.DataFrame()
            df = df.copy()
            df.columns = [str(c).lower() for c in df.columns]
            if df.index.tz is not None:
                df.index = df.index.tz_convert("US/Eastern")
            return df
        except Exception as exc:
            self.logger.debug(f"_get_minute_bars_df {ticker}: {exc}")
            return pd.DataFrame()

    def _latest_session_slice(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or not hasattr(df.index, "date"):
            return pd.DataFrame()
        session_date = df.index[-1].date()
        return df[df.index.map(lambda x: x.date() == session_date)].copy()

    def _compute_session_vwap(self, df: pd.DataFrame) -> float:
        session = self._latest_session_slice(df)
        if session.empty:
            return 0.0
        vol = session["volume"].replace(0, pd.NA)
        session_vwap = ((session["close"] * vol).cumsum() / vol.cumsum()).iloc[-1]
        if pd.isna(session_vwap):
            return float(session["close"].iloc[-1])
        return float(session_vwap)

    def _extract_revenue_growth_from_payload(self, payload) -> float | None:
        if payload is None:
            return None
        if isinstance(payload, dict):
            for key in ["revenueGrowth", "revenue_growth", "revenue_growth_yoy",
                        "revenue_growth_1y", "revenue_growth_ttm"]:
                if key in payload:
                    return self._safe_float(payload.get(key), 0.0)
            for nested_key in ["fundamentals", "financials", "data", "results", "result"]:
                if nested_key in payload:
                    growth = self._extract_revenue_growth_from_payload(payload[nested_key])
                    if growth is not None:
                        return growth
        if isinstance(payload, list) and len(payload) >= 2:
            latest = payload[0] if isinstance(payload[0], dict) else {}
            prior  = payload[1] if isinstance(payload[1], dict) else {}
            latest_rev = self._safe_float(latest.get("revenue"), 0.0)
            prior_rev  = self._safe_float(prior.get("revenue"), 0.0)
            if latest_rev > 0 and prior_rev > 0:
                return (latest_rev / prior_rev) - 1.0
        return None

    def _get_revenue_growth_external(self, ticker: str) -> float | None:
        cfg = self.config.universe
        if not getattr(cfg, "fundamentals_external_enabled", False):
            return None
        api_key    = str(getattr(cfg, "fundamentals_api_key", "")).strip()
        api_secret = str(getattr(cfg, "fundamentals_api_secret", "")).strip()
        if not api_key or not api_secret:
            return None

        now_et = datetime.now(pytz.timezone("US/Eastern"))
        cache_hours = max(1, int(getattr(cfg, "fundamentals_cache_hours", 24)))
        cached = self._revenue_growth_cache.get(ticker)
        if cached:
            cached_time, cached_value = cached
            if now_et - cached_time < timedelta(hours=cache_hours):
                return cached_value

        try:
            timeout_sec   = max(2, int(getattr(cfg, "fundamentals_timeout_seconds", 8)))
            base_url      = str(getattr(cfg, "fundamentals_base_url", "https://data.alpaca.markets")).rstrip("/")
            path_template = str(getattr(cfg, "fundamentals_path_template", "/v1beta1/fundamentals/{symbol}"))
            url = f"{base_url}{path_template.format(symbol=ticker)}"
            headers = {
                "APCA-API-KEY-ID": api_key,
                "APCA-API-SECRET-KEY": api_secret,
                "accept": "application/json",
            }
            response = requests.get(url, headers=headers, timeout=timeout_sec)
            response.raise_for_status()
            growth = self._extract_revenue_growth_from_payload(response.json())
            if growth is None:
                return None
            self._revenue_growth_cache[ticker] = (now_et, growth)
            return growth
        except Exception as exc:
            self.logger.debug(f"External fundamentals fetch failed for {ticker}: {exc}")
            return None

    def _score_dynamic_candidate(self, ticker: str) -> tuple[float, float, float] | None:
        cfg = self.config.universe
        lookback  = int(getattr(cfg, "momentum_lookback_days", 22))
        vol_window = int(getattr(cfg, "avg_volume_window_days", 20))
        min_required = max(lookback + 1, vol_window)

        df = self._get_daily_bars_df(ticker, days=130)
        if df.empty or len(df) < min_required + 1:
            return None

        close_series = df["close"].dropna()
        if len(close_series) < min_required + 1:
            return None

        current_price  = self._safe_float(close_series.iloc[-1])
        lookback_price = self._safe_float(close_series.iloc[-(lookback + 1)])
        if current_price <= 0 or lookback_price <= 0:
            return None

        min_price = self._safe_float(getattr(cfg, "min_price", 0.0), 0.0)
        if current_price < min_price:
            return None

        if "volume" not in df.columns:
            return None

        recent = df.tail(vol_window).copy()
        recent["dollar_vol"] = recent["close"] * recent["volume"]
        avg_dollar_vol = self._safe_float(recent["dollar_vol"].mean())
        min_dollar_vol = self._safe_float(getattr(cfg, "min_avg_dollar_volume", 0.0), 0.0)
        if avg_dollar_vol < min_dollar_vol:
            return None

        momentum       = (current_price / lookback_price) - 1.0
        revenue_growth = self._get_revenue_growth_external(ticker)
        if revenue_growth is None:
            if getattr(cfg, "fundamentals_fallback_to_static", True):
                rev_map        = getattr(cfg, "revenue_growth_1y", {})
                revenue_growth = self._safe_float(rev_map.get(ticker, 0.0), 0.0)
            else:
                revenue_growth = 0.0

        w_mom = self._safe_float(getattr(cfg, "momentum_weight", 0.6), 0.6)
        w_rev = self._safe_float(getattr(cfg, "revenue_growth_weight", 0.4), 0.4)
        score = (w_mom * momentum) + (w_rev * revenue_growth)
        return score, momentum, revenue_growth

    def refresh_dynamic_universe_if_due(self):
        try:
            self._refresh_dynamic_universe(force=False)
        except Exception as exc:
            self.logger.error(f"Universe refresh job error: {exc}")

    def _refresh_dynamic_universe(self, force: bool = False):
        cfg       = self.config.universe
        if not getattr(cfg, "dynamic_enabled", False):
            self._dynamic_symbols = set()
            self._build_active_universe()
            return

        et      = pytz.timezone("US/Eastern")
        now_et  = datetime.now(et)
        refresh_days = max(1, int(getattr(cfg, "refresh_days", 14)))

        if not force and self._last_universe_refresh is not None:
            if (now_et.date() - self._last_universe_refresh.date()).days < refresh_days:
                return

        core      = set(self.config.universe.core_symbols)
        excludes  = set(getattr(cfg, "exclude_symbols", []))
        candidates = [
            t for t in getattr(cfg, "candidate_symbols", [])
            if t not in core and t not in excludes
        ]

        if not candidates:
            self._dynamic_symbols = set()
            self._build_active_universe()
            return

        scored: list[tuple[str, float, float, float]] = []
        pause_sec = self._safe_float(getattr(cfg, "request_pause_seconds", 0.25), 0.25)
        for ticker in candidates:
            try:
                result = self._score_dynamic_candidate(ticker)
                if result is None:
                    continue
                score, momentum, revenue_growth = result
                scored.append((ticker, score, momentum, revenue_growth))
            except Exception as exc:
                self.logger.debug(f"Universe candidate error {ticker}: {exc}")
            if pause_sec > 0:
                time.sleep(pause_sec)

        scored.sort(key=lambda x: x[1], reverse=True)
        top_n    = max(0, int(getattr(cfg, "top_n_dynamic", 10)))
        selected = [t for t, _, _, _ in scored[:top_n]]
        self._dynamic_symbols        = set(selected)
        self._build_active_universe()
        self._last_universe_refresh  = now_et

        if selected:
            top_msg = ", ".join(
                [f"{t}(score={s:.3f}, mom={m:.1%}, rev={r:.1%})" for t, s, m, r in scored[:top_n]]
            )
            self.logger.info(f"Universe refreshed ({refresh_days}d cadence): {top_msg}")
        else:
            self.logger.warning("Universe refreshed but no dynamic symbols passed filters")

    # ================================================================
    #  INDICATORS  (computed from Alpaca bars via pandas)
    # ================================================================
    @staticmethod
    def _ema(series: pd.Series, span: int) -> pd.Series:
        return series.ewm(span=span, adjust=False).mean()

    @staticmethod
    def _rsi(series: pd.Series, length: int = 14) -> pd.Series:
        delta     = series.diff()
        gain      = delta.clip(lower=0)
        loss      = -delta.clip(upper=0)
        avg_gain  = gain.ewm(alpha=1 / length, adjust=False).mean()
        avg_loss  = loss.ewm(alpha=1 / length, adjust=False).mean()
        rs        = avg_gain / avg_loss.replace(0, pd.NA)
        return 100 - (100 / (1 + rs))

    def _compute_spy_indicators(self) -> dict | None:
        try:
            df = self._get_daily_bars_df("SPY", days=110)
            if df.empty or len(df) < 55:
                self.logger.warning("Insufficient SPY daily bars for indicators")
                return None

            df["ema_20"] = self._ema(df["close"], 20)
            df["ema_50"] = self._ema(df["close"], 50)
            df["rsi"]    = self._rsi(df["close"], 14)

            latest = df.iloc[-1]
            prev   = df.iloc[-2]

            return {
                "price":        float(latest["close"]),
                "ema_20":       float(latest["ema_20"]),
                "ema_50":       float(latest["ema_50"]),
                "ema_20_prev":  float(prev["ema_20"]),
                "ema_50_prev":  float(prev["ema_50"]),
                "rsi":          float(latest["rsi"]) if pd.notna(latest["rsi"]) else None,
            }
        except Exception as exc:
            self.logger.error(f"SPY indicator error: {exc}")
            return None

    def _compute_intraday_indicators(self, ticker: str) -> dict | None:
        hourly_df = self._get_hourly_bars_df(ticker, days=12)
        if hourly_df.empty or len(hourly_df) < 52:
            self.logger.debug(f"{ticker}: insufficient hourly bars ({len(hourly_df)})")
            return None

        ema_fast = self._ema(hourly_df["close"], 12)
        ema_slow = self._ema(hourly_df["close"], 26)
        hourly_df["macd"] = ema_fast - ema_slow
        hourly_df["macd_signal"] = self._ema(hourly_df["macd"], 9)
        hourly_df["rsi"] = self._rsi(hourly_df["close"], 14)
        hourly_df["ema_50"] = self._ema(hourly_df["close"], 50)

        h, l, c = hourly_df["high"], hourly_df["low"], hourly_df["close"]
        tr = pd.concat([
            h - l,
            (h - c.shift()).abs(),
            (l - c.shift()).abs(),
        ], axis=1).max(axis=1)
        hourly_df["atr_14"] = tr.ewm(span=14, adjust=False).mean()
        hourly_df["bb_mid"] = hourly_df["close"].rolling(20).mean()
        hourly_df["bb_std"] = hourly_df["close"].rolling(20).std(ddof=0)
        hourly_df["zscore"] = (
            (hourly_df["close"] - hourly_df["bb_mid"]) /
            hourly_df["bb_std"].replace(0, pd.NA)
        )
        hourly_df["vol_sma_20"] = hourly_df["volume"].rolling(20).mean()

        latest = hourly_df.iloc[-1]
        vol_sma = float(latest.get("vol_sma_20", 0) or 0)
        volume_ratio = (float(latest["volume"]) / vol_sma) if vol_sma > 0 else 1.0
        zscore_val = latest.get("zscore", 0)
        if pd.isna(zscore_val):
            zscore_val = 0.0

        minute_df = self._get_minute_bars_df(ticker, days=2)
        session_vwap = float(latest["close"])
        orb_high = float(latest["high"])
        orb_low = float(latest["low"])
        if not minute_df.empty:
            session_df = self._latest_session_slice(minute_df)
            if not session_df.empty:
                session_vwap = self._compute_session_vwap(minute_df)
                orb_window = max(1, int(getattr(self.config.trading, "intraday_orb_minutes", 30)))
                opening_range = session_df.head(orb_window)
                if not opening_range.empty:
                    orb_high = float(opening_range["high"].max())
                    orb_low = float(opening_range["low"].min())

        return {
            "price": float(latest["close"]),
            "macd": float(latest.get("macd", 0)),
            "macd_signal": float(latest.get("macd_signal", 0)),
            "rsi": float(latest.get("rsi", 50)),
            "ema_50": float(latest.get("ema_50", 0)),
            "atr_14": float(latest.get("atr_14", 0)),
            "vwap": float(session_vwap),
            "zscore": float(zscore_val),
            "volume_ratio": volume_ratio,
            "opening_range_high": orb_high,
            "opening_range_low": orb_low,
        }

    def _compute_swing_indicators(self, ticker: str) -> dict | None:
        df = self._get_daily_bars_df(ticker, days=130)
        if df.empty or len(df) < 55:
            self.logger.debug(f"{ticker}: insufficient daily bars ({len(df)})")
            return None

        ema_fast = self._ema(df["close"], 12)
        ema_slow = self._ema(df["close"], 26)
        df["macd"] = ema_fast - ema_slow
        df["macd_signal"] = self._ema(df["macd"], 9)
        df["rsi"] = self._rsi(df["close"], 14)
        df["ema_50"] = self._ema(df["close"], 50)

        h, l, c = df["high"], df["low"], df["close"]
        tr = pd.concat([
            h - l,
            (h - c.shift()).abs(),
            (l - c.shift()).abs(),
        ], axis=1).max(axis=1)
        df["atr_14"] = tr.ewm(span=14, adjust=False).mean()
        df["bb_mid"] = df["close"].rolling(20).mean()
        df["bb_std"] = df["close"].rolling(20).std(ddof=0)
        df["zscore"] = (df["close"] - df["bb_mid"]) / df["bb_std"].replace(0, pd.NA)
        df["vol_sma_20"] = df["volume"].rolling(20).mean()
        df["vwap_20"] = (
            (df["close"] * df["volume"]).rolling(20).sum() /
            df["volume"].rolling(20).sum().replace(0, pd.NA)
        )
        df["breakout_20"] = df["high"].rolling(20).max()

        latest = df.iloc[-1]
        vol_sma = float(latest.get("vol_sma_20", 0) or 0)
        volume_ratio = (float(latest["volume"]) / vol_sma) if vol_sma > 0 else 1.0
        zscore_val = latest.get("zscore", 0)
        if pd.isna(zscore_val):
            zscore_val = 0.0
        vwap_val = latest.get("vwap_20", latest["close"])
        if pd.isna(vwap_val):
            vwap_val = latest["close"]
        orb_high = latest.get("breakout_20", latest["high"])
        if pd.isna(orb_high):
            orb_high = latest["high"]

        return {
            "price": float(latest["close"]),
            "macd": float(latest.get("macd", 0)),
            "macd_signal": float(latest.get("macd_signal", 0)),
            "rsi": float(latest.get("rsi", 50)),
            "ema_50": float(latest.get("ema_50", 0)),
            "atr_14": float(latest.get("atr_14", 0)),
            "vwap": float(vwap_val),
            "zscore": float(zscore_val),
            "volume_ratio": volume_ratio,
            "opening_range_high": float(orb_high),
            "opening_range_low": float(latest.get("low", 0)),
        }

    def _compute_symbol_indicators(self, ticker: str) -> dict | None:
        try:
            if self._is_swing_style():
                return self._compute_swing_indicators(ticker)
            return self._compute_intraday_indicators(ticker)
        except Exception as exc:
            self.logger.error(f"{ticker} indicator error: {exc}")
            return None

    def _trade_count_this_week(self, now: datetime) -> int:
        week_start = (now - timedelta(days=now.weekday())).date()
        return sum(1 for ts in self._entry_timestamps if ts.date() >= week_start)

    def _symbol_on_cooldown(self, ticker: str, now: datetime) -> bool:
        cfg = self.config.trading
        min_gap = int(getattr(cfg, "min_days_between_trades", 0))
        symbol_cooldown = int(getattr(cfg, "symbol_cooldown_days", 0))
        last_exit = self._last_exit_time.get(ticker)
        if not last_exit:
            return False
        elapsed_days = (now.date() - last_exit.date()).days
        return elapsed_days < min_gap or elapsed_days < symbol_cooldown

    def _compute_portfolio_signal(self, ticker: str, ind: dict) -> tuple[float, str]:
        price       = ind["price"]
        macd        = ind["macd"]
        macd_sig    = ind["macd_signal"]
        rsi_val     = ind["rsi"]
        ema_val     = ind["ema_50"]
        vwap        = ind["vwap"]
        zscore      = ind["zscore"]
        volume_ratio = ind["volume_ratio"]
        orb_high    = ind["opening_range_high"]

        sentiment_raw = self._feeds.get_sentiment(ticker)
        ob_imbalance  = self._feeds.get_ob_imbalance(ticker)

        component_scores: dict[str, float] = {
            "momentum": 0.0, "mean_reversion": 0.0, "orb": 0.0,
            "vwap_twap": 0.0, "market_making": 0.0, "stat_arb": 0.0, "sentiment": 0.0,
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

        if sentiment_raw > 0.10:
            component_scores["sentiment"] = min(1.0, (sentiment_raw - 0.10) / 0.90)

        if ob_imbalance > 0.10:
            component_scores["market_making"] = min(1.0, (ob_imbalance - 0.10) / 0.90)

        weighted = 0.0
        active: list[str] = []
        for name, score in component_scores.items():
            weight = self._strategy_weights.get(name, 0.0)
            if score > 0 and weight > 0:
                active.append(f"{name}:{score:.2f}")
            weighted += weight * score

        return weighted, ", ".join(active) if active else "none"

    # ================================================================
    #  MARKET REGIME DETECTION
    # ================================================================
    def detect_market_regime(self):
        try:
            if not self._is_market_open():
                self._log_market_closed_skip("detect_market_regime")
                return

            spy = self._compute_spy_indicators()
            if not spy:
                self.logger.warning("Cannot compute SPY indicators — keeping regime")
                return

            ema_20, ema_50 = spy["ema_20"], spy["ema_50"]
            ema_20_prev    = spy["ema_20_prev"]
            spy_price      = spy["price"]
            rsi_val        = spy["rsi"]

            previous_regime    = self.market_regime
            price_above_50     = spy_price > ema_50
            price_below_50     = spy_price < ema_50
            ema_structure_bull = ema_20 > ema_50
            ema_structure_bear = ema_20 < ema_50
            ema_20_rising      = ema_20 > ema_20_prev
            momentum_bear      = (not ema_20_rising) and spy_price < ema_20

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
                self.logger.warning(f"REGIME CHANGE: {previous_regime} → {self.market_regime}")
                self._send_alert_email(
                    f"Regime Change: {previous_regime} → {self.market_regime}",
                    f"SPY: {spy_price:.2f} | EMA20: {ema_20:.2f} | EMA50: {ema_50:.2f}",
                )

        except Exception as exc:
            self.logger.error(f"Regime detection error: {exc}")

    # ================================================================
    #  MARKET HOURS CHECK
    # ================================================================
    def _is_market_open(self) -> bool:
        try:
            et      = pytz.timezone("US/Eastern")
            now_et  = datetime.now(et)
            return (now_et.weekday() < 5
                    and dt_time(9, 30) <= now_et.time() <= dt_time(16, 0))
        except Exception:
            return True

    def _roll_schedule_stats_day(self):
        try:
            today_et = datetime.now(pytz.timezone("US/Eastern")).date()
            if today_et != self._schedule_stats_date:
                self._schedule_stats_date          = today_et
                self._schedule_trigger_count       = 0
                self._schedule_market_closed_skips = 0
                self._last_closed_log_key.clear()
        except Exception:
            pass

    def _log_market_closed_skip(self, job_name: str):
        try:
            self._roll_schedule_stats_day()
            et      = pytz.timezone("US/Eastern")
            now_et  = datetime.now(et)
            key     = f"{now_et.strftime('%Y-%m-%d %H:%M')}|{job_name}"
            if self._last_closed_log_key.get(job_name) == key:
                return
            self._last_closed_log_key[job_name] = key
            self._schedule_market_closed_skips += 1
            self.logger.info(f"Market closed - skipping {job_name}")
        except Exception:
            self.logger.info(f"Market closed - skipping {job_name}")

    # ================================================================
    #  POSITION SIZING
    # ================================================================
    def _calculate_position_size(self, ticker: str, price: float) -> int:
        try:
            available_cash = self.cash
            safe_available = available_cash * 0.75
            target_value   = min(safe_available * 0.85, 6500)
            if target_value < 4000:
                return 0
            qty = int(target_value / price)
            if qty <= 0:
                return 0
            # Alpaca is commission-free; keep fee estimate for minimum-size check
            fee        = self._estimate_fee(qty, price)
            total_cost = qty * price + fee
            if total_cost > target_value:
                qty        = int((target_value - fee) / price)
                total_cost = qty * price + self._estimate_fee(qty, price)
            return qty if qty > 0 and total_cost >= 4000 else 0
        except Exception as exc:
            self.logger.error(f"Position sizing error: {exc}")
            return 0

    @staticmethod
    def _estimate_fee(qty: int, price: float) -> float:
        """Alpaca is commission-free; this is a negligible SEC fee floor."""
        if qty <= 0 or price <= 0:
            return 0.0
        trade_value = qty * price
        return min(0.000008 * trade_value, 0.35)

    # ================================================================
    #  ORDER EXECUTION
    # ================================================================
    def _wait_for_fill(self, order_id: str, timeout: int = 30) -> object:
        """Poll order status until filled or timeout."""
        for _ in range(timeout):
            time.sleep(1)
            try:
                order = self.trading_client.get_order_by_id(order_id)
                if order.status in (
                    AlpacaOrderStatus.FILLED,
                    AlpacaOrderStatus.CANCELED,
                    AlpacaOrderStatus.EXPIRED,
                    AlpacaOrderStatus.REJECTED,
                ):
                    return order
            except Exception:
                pass
        return self.trading_client.get_order_by_id(order_id)

    def _place_market_buy(self, ticker: str, qty: int) -> bool:
        try:
            req = MarketOrderRequest(
                symbol=ticker,
                qty=qty,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
            )
            order = self.trading_client.submit_order(req)
            self.logger.info(f"ORDER PLACED: BUY {qty} {ticker}")
            final = self._wait_for_fill(str(order.id))
            if final.status == AlpacaOrderStatus.FILLED:
                fill_price = float(final.filled_avg_price or 0)
                self.logger.info(f"FILLED: BUY {qty} {ticker} @ ${fill_price:.2f}")
                return True
            self.logger.warning(f"BUY order status: {final.status}")
            return final.status in (AlpacaOrderStatus.FILLED,)
        except Exception as exc:
            self.logger.error(f"Buy order error {ticker}: {exc}")
            return False

    def _place_market_sell(self, ticker: str, qty: int, reason: str = "") -> bool:
        try:
            req = MarketOrderRequest(
                symbol=ticker,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            )
            order = self.trading_client.submit_order(req)
            self.logger.info(f"ORDER PLACED: SELL {qty} {ticker} ({reason})")
            final = self._wait_for_fill(str(order.id))
            if final.status == AlpacaOrderStatus.FILLED:
                fill_price = float(final.filled_avg_price or 0)
                self.logger.info(f"FILLED: SELL {qty} {ticker} @ ${fill_price:.2f} ({reason})")
                return True
            self.logger.warning(f"SELL order status: {final.status}")
            return False
        except Exception as exc:
            self.logger.error(f"Sell order error {ticker}: {exc}")
            return False

    # ================================================================
    #  SIGNAL EVALUATION (entry + exit)
    # ================================================================
    def flatten_intraday_positions(self):
        if not self._style_settings["flatten_eod"]:
            return
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
                self._last_exit_time[ticker] = datetime.now()
                self.logger.info(f"EOD FLATTEN: {ticker} qty={qty}")

    def evaluate_signals(self):
        try:
            if not self._is_market_open():
                self._log_market_closed_skip("evaluate_signals")
                return
            if not self._trading_enabled:
                self.logger.warning("Trading disabled (circuit breaker)")
                return

            current_equity = self.net_liquidation
            if current_equity <= 0:
                return

            daily_loss = self._starting_cash - current_equity
            if daily_loss > self._style_settings["max_daily_loss"]:
                self.logger.critical(f"DAILY LOSS LIMIT: ${daily_loss:,.2f}")
                self._trading_enabled = False
                self._send_alert_email(
                    "CIRCUIT BREAKER: Daily Loss Limit",
                    f"Loss: ${daily_loss:,.2f} exceeds ${self._style_settings['max_daily_loss']}",
                )
                return

            if current_equity > self.peak_equity:
                self.peak_equity = current_equity

            drawdown = ((self.peak_equity - current_equity) / self.peak_equity
                        if self.peak_equity > 0 else 0)
            if drawdown > self.config.risk.max_drawdown_pct:
                self.logger.critical(f"DRAWDOWN LIMIT: {drawdown:.1%}")
                self._trading_enabled = False
                self._send_alert_email(
                    "CIRCUIT BREAKER: Max Drawdown",
                    f"Drawdown: {drawdown:.1%} > {self.config.risk.max_drawdown_pct:.1%}",
                )
                return

            self._evaluate_exits_and_entries(current_equity)

        except Exception as exc:
            self.logger.error(f"Signal evaluation error: {exc}")

    def _evaluate_exits_and_entries(self, current_equity: float):
        now       = datetime.now()
        positions = self._get_positions()

        algo_positions = {
            t: p for t, p in positions.items() if t in self._algo_managed_positions
        }

        # ── EXIT LOGIC ─────────────────────────────────────────────
        for ticker in list(algo_positions):
            pos = positions.get(ticker)
            if not pos:
                continue
            try:
                current_price = pos["market_price"]
                if current_price <= 0:
                    current_price = self._get_price(ticker)
                if current_price <= 0:
                    continue

                avg_entry  = pos["avg_cost"]
                qty        = int(pos["qty"])
                pnl_pct    = (current_price - avg_entry) / avg_entry if avg_entry > 0 else 0
                pnl_dollar = qty * (current_price - avg_entry)
                held_time  = now - self.entry_time.get(ticker, now)

                self.highest_price[ticker] = max(
                    self.highest_price.get(ticker, current_price), current_price
                )

                should_exit, reason = False, ""
                style = self._style_settings

                if pnl_pct <= -style["stop_loss_pct"]:
                    should_exit, reason = True, "stop_loss"
                elif held_time >= timedelta(hours=style["min_hold_hours"]) and pnl_pct >= style["take_profit_pct"]:
                    should_exit, reason = True, "take_profit"
                elif (held_time >= timedelta(hours=style["profit_lock_hours"])
                      and pnl_pct >= style["profit_lock_min_gain_pct"]):
                    should_exit, reason = True, "profit_lock"
                elif held_time >= timedelta(days=style["max_hold_days"]):
                    should_exit, reason = True, "time_exit"

                trail_activation = avg_entry * (1 + style["trailing_activation_pct"])
                trailing_stop = self.highest_price.get(ticker, current_price) * (1 - style["trailing_stop_pct"])
                if self.highest_price.get(ticker, current_price) >= trail_activation and current_price <= trailing_stop:
                    should_exit, reason = True, "trailing_stop"

                if pnl_pct <= -0.05:
                    self.logger.critical(f"GAP DOWN: {ticker} at {pnl_pct:.2%}")
                    should_exit, reason = True, "gap_protection"

                if should_exit:
                    sold = self._place_market_sell(ticker, qty, reason)
                    if sold:
                        record = (
                            f"{now.strftime('%Y-%m-%d %H:%M')} SELL {ticker} "
                            f"- Qty: {qty} @ ${current_price:.2f} | P&L: ${pnl_dollar:.2f}"
                        )
                        self.trade_history.append(record)
                        if pnl_dollar > 0:
                            self.winning_trades.append(record)
                        else:
                            self.losing_trades.append(record)
                        self._update_symbol_performance(ticker, pnl_dollar)
                        self._algo_managed_positions.discard(ticker)
                        self._entry_strategy_reason.pop(ticker, None)
                        self._last_exit_time[ticker] = now
                        self.logger.info(f"EXIT {ticker}: {reason}, P&L ${pnl_dollar:.0f}")

            except Exception as exc:
                self.logger.error(f"Exit error for {ticker}: {exc}")

        # ── ENTRY LOGIC ────────────────────────────────────────────
        if self.market_regime == "BEAR":
            return

        portfolio_return = (
            (current_equity - self._starting_cash) / self._starting_cash
            if self._starting_cash > 0 else 0
        )

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

        if self._is_swing_style() and self._trade_count_this_week(now) >= self.config.trading.max_weekly_trades:
            self.logger.info("Weekly trade cap reached - skipping new swing entries")
            return

        candidates: list[tuple[str, float, float, str]] = []
        for ticker in self._active_universe:
            if ticker == "SPY" or ticker in positions:
                continue
            if self._is_swing_style() and self._symbol_on_cooldown(ticker, now):
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

            time.sleep(0.3)   # Alpaca rate-limit pacing

        if not candidates:
            return

        candidates.sort(key=lambda x: x[1], reverse=True)
        best_ticker, best_score, current_price, reason = candidates[0]

        live_price = self._get_price(best_ticker)
        if live_price > 0:
            current_price = live_price

        qty = self._calculate_position_size(best_ticker, current_price)
        if qty <= 0:
            return

        bought = self._place_market_buy(best_ticker, qty)
        if bought:
            self._algo_managed_positions.add(best_ticker)
            self.entry_time[best_ticker]              = now
            self.entry_price[best_ticker]             = current_price
            self.highest_price[best_ticker]           = current_price
            self._entry_strategy_reason[best_ticker]  = reason
            self._entry_timestamps.append(now)
            self._last_entry_time[best_ticker] = now

            record = (
                f"{now.strftime('%Y-%m-%d %H:%M')} BUY {best_ticker} "
                f"- Qty: {qty} @ ${current_price:.2f} | score={best_score:.2f} | {reason}"
            )
            self.trade_history.append(record)
            self.logger.info(
                f"BUY {best_ticker}: qty={qty}, ${current_price:.2f}, "
                f"score={best_score:.2f}, {reason}"
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
            pnl    = equity - self._starting_cash
            ret    = pnl / self._starting_cash if self._starting_cash > 0 else 0
            self.logger.info(f"Daily: ${equity:,.0f} ({ret:.2%})")
            self._trading_enabled = True     # Reset circuit breaker at EOD
        except Exception as exc:
            self.logger.error(f"Daily summary error: {exc}")

    # ================================================================
    #  EMAIL
    # ================================================================
    def send_portfolio_summary_email(self):
        if not self._is_market_open():
            self._log_market_closed_skip("send_portfolio_summary_email")
            return
        try:
            self._roll_schedule_stats_day()
            equity       = self.net_liquidation
            cash_val     = self.cash
            total_return = (equity - self._starting_cash) / self._starting_cash if self._starting_cash > 0 else 0
            daily_pnl    = equity - self._starting_cash
            positions    = self._get_positions()

            pos_lines = []
            for ticker, pos in positions.items():
                price = pos["market_price"]
                avg   = pos["avg_cost"]
                qty   = int(pos["qty"])
                pnl   = qty * (price - avg)
                pnl_pct = (price - avg) / avg if avg > 0 else 0
                held  = datetime.now() - self.entry_time.get(ticker, datetime.now())
                tag   = "ALGO" if ticker in self._algo_managed_positions else "MANUAL"
                pos_lines.append(
                    f"  {ticker:<6} | Qty:{qty:<5} | Entry:${avg:<8.2f} | "
                    f"Now:${price:<8.2f} | P&L:${pnl:<8.2f} ({pnl_pct:>6.2%}) | "
                    f"{tag} | {str(held).split('.')[0]}"
                )

            body = (
                f"{'=' * 70}\nPORTFOLIO SUMMARY\n{'=' * 70}\n\n"
                f"Equity:        ${equity:,.2f}\n"
                f"Total Return:  {total_return:.2%}\n"
                f"Daily P&L:     ${daily_pnl:,.2f}\n"
                f"Cash:          ${cash_val:,.2f}\n"
                f"Regime:        {self.market_regime}\n"
                f"Algo Positions: {len(self._algo_managed_positions)}/{self.config.trading.max_positions}\n\n"
                f"POSITIONS\n{'-' * 70}\n"
            )
            if pos_lines:
                body += "\n".join(pos_lines) + "\n"
            else:
                body += "  No open positions\n"

            body += f"\nRECENT TRADES\n{'-' * 70}\n"
            recent = list(self.trade_history)[-10:]
            body += ("\n".join(f"  {t}" for t in recent) + "\n") if recent else "  No recent trades\n"

            body += f"\n{'=' * 70}\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            self._send_email(f"Portfolio Summary - {datetime.now().strftime('%Y-%m-%d %H:%M')}", body)
        except Exception as exc:
            self.logger.error(f"Portfolio email error: {exc}")

    def send_weekly_summary_email(self):
        try:
            equity  = self.net_liquidation
            total_return = (equity - self._starting_cash) / self._starting_cash if self._starting_cash > 0 else 0
            drawdown = ((self.peak_equity - equity) / self.peak_equity if self.peak_equity > 0 else 0)
            wins  = len(self.winning_trades)
            losses = len(self.losing_trades)
            total  = wins + losses
            win_rate = (wins / total * 100) if total else 0

            body = (
                f"{'=' * 80}\nWEEKLY PORTFOLIO ANALYSIS\n{'=' * 80}\n\n"
                f"Equity:      ${equity:,.2f}\n"
                f"Return:      {total_return:.2%}\n"
                f"Drawdown:    {drawdown:.2%}\n"
                f"Win Rate:    {win_rate:.1f}% ({wins}/{total})\n"
                f"Regime:      {self.market_regime}\n\n"
                f"RECENT TRADES (last 15)\n{'-' * 80}\n"
            )
            for t in list(self.trade_history)[-15:]:
                body += f"  {t}\n"
            body += f"\n{'=' * 80}\nWeekly Report: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            self._send_email(f"Weekly Portfolio Summary - {datetime.now().strftime('%Y-%m-%d')}", body)
        except Exception as exc:
            self.logger.error(f"Weekly email error: {exc}")

    def _send_email(self, subject: str, body: str):
        try:
            cfg = self.config.email
            if not all([cfg.smtp_server, cfg.sender_email, cfg.sender_password]):
                return
            msg = MIMEMultipart()
            msg["From"]    = cfg.sender_email
            msg["To"]      = cfg.recipient_email
            subject_tag = "[ALPACA BOT]"
            normalized_subject = subject.strip()
            if not normalized_subject.startswith(subject_tag):
                normalized_subject = f"{subject_tag} {normalized_subject}"
            msg["Subject"] = normalized_subject
            msg["X-Bot-Name"] = "ALPACA BOT"
            msg["X-Bot-System"] = "EnhancedtradingBOTAlpaca"
            msg.attach(MIMEText(body, "plain"))
            server = smtplib.SMTP(cfg.smtp_server, cfg.smtp_port)
            server.starttls()
            server.login(cfg.sender_email, cfg.sender_password)
            server.sendmail(cfg.sender_email, cfg.recipient_email, msg.as_string())
            server.quit()
            self.logger.info(f"Email sent: {subject}")
        except Exception as exc:
            self.logger.error(f"Email send error: {exc}")

    def _send_alert_email(self, subject: str, body: str):
        self._send_email(f"[ALERT] {subject}", body)

    # ================================================================
    #  SCHEDULER SETUP
    # ================================================================
    def _setup_scheduler(self):
        mf   = "mon-fri"
        jobs = [
            (9,  20, self.refresh_dynamic_universe_if_due, "universe_refresh"),
            (9,  25, self.detect_market_regime,            "regime_morning"),
            (12,  0, self.detect_market_regime,            "regime_midday"),
            (15,  0, self.detect_market_regime,            "regime_afternoon"),
            (9,  35, self.send_portfolio_summary_email,    "email_morning"),
            (12, 30, self.send_portfolio_summary_email,    "email_midday"),
            (15, 30, self.send_portfolio_summary_email,    "email_afternoon"),
            (16,  0, self.send_portfolio_summary_email,    "email_close"),
            (16,  0, self.daily_risk_summary,              "daily_risk"),
        ]

        for hour, minute, func, job_id in jobs:
            self.scheduler.add_job(
                self._safe_run(func),
                CronTrigger(hour=hour, minute=minute, day_of_week=mf),
                id=job_id,
                replace_existing=True,
            )

        if self._is_intraday_style():
            self.scheduler.add_job(
                self._safe_run(self.evaluate_signals),
                CronTrigger(hour=9, minute="30,45", day_of_week=mf),
                id="signals_intraday_open",
                replace_existing=True,
            )
            self.scheduler.add_job(
                self._safe_run(self.evaluate_signals),
                CronTrigger(hour="10-15", minute="*/15", day_of_week=mf),
                id="signals_intraday_loop",
                replace_existing=True,
            )
            if self._style_settings["flatten_eod"]:
                self.scheduler.add_job(
                    self._safe_run(self.flatten_intraday_positions),
                    CronTrigger(hour=15, minute=55, day_of_week=mf),
                    id="flatten_eod",
                    replace_existing=True,
                )
        else:
            for hour, minute, job_id in [(10, 0, "signals_swing_open"), (15, 30, "signals_swing_close")]:
                self.scheduler.add_job(
                    self._safe_run(self.evaluate_signals),
                    CronTrigger(hour=hour, minute=minute, day_of_week=mf),
                    id=job_id,
                    replace_existing=True,
                )

        self.scheduler.add_job(
            self._safe_run(self.send_weekly_summary_email),
            CronTrigger(hour=16, minute=0, day_of_week="fri"),
            id="weekly_summary",
            replace_existing=True,
        )
        self.logger.info(f"Scheduled {len(jobs) + 1} jobs (US/Eastern, Mon-Fri)")

    def _safe_run(self, func):
        def wrapper():
            try:
                self._roll_schedule_stats_day()
                self._schedule_trigger_count += 1
                self.logger.info(f"Scheduled job triggered: {func.__name__}")
                func()
            except Exception as exc:
                self.logger.error(f"Job {func.__name__} error: {exc}")
        wrapper.__name__ = func.__name__
        return wrapper

    # ================================================================
    #  SIGNAL HANDLERS + MAIN ENTRY POINT
    # ================================================================
    def _signal_handler(self, signum, frame):
        self.logger.info(f"Signal {signum} received — shutting down.")
        self._running = False
        self._feeds.stop()
        self.scheduler.shutdown(wait=False)
        sys.exit(0)

    def start(self):
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT,  self._signal_handler)

        # Verify credentials by fetching account
        try:
            account = self._get_account()
            self._starting_cash = float(account.portfolio_value)
            self.peak_equity    = self._starting_cash
            mode_label = "PAPER" if self.config.alpaca.paper else "LIVE"
            self.logger.info(f"Connected to Alpaca [{mode_label}] — equity: ${self._starting_cash:,.2f}")
        except Exception as exc:
            self.logger.critical(f"Alpaca connection failed: {exc}")
            raise

        self._refresh_dynamic_universe(force=True)
        self._feeds.update_symbols(self._active_universe)
        self._feeds.start()
        self.detect_market_regime()
        self._setup_scheduler()
        self.scheduler.start()

        mode = self.config.general.mode
        self.logger.info("=" * 60)
        self.logger.info("ALPACA TRADING BOT STARTED")
        self.logger.info(f"Mode:          {mode}")
        self.logger.info(f"Trading style: {self.trading_style}")
        self.logger.info(f"Paper trading: {self.config.alpaca.paper}")
        self.logger.info(f"Core symbols:  {self.config.universe.core_symbols}")
        self.logger.info(f"Active universe: {self._active_universe}")
        self.logger.info(f"Regime:        {self.market_regime}")
        self.logger.info(f"Equity:        ${self.net_liquidation:,.2f}")
        self.logger.info("=" * 60)

        self._send_alert_email(
            "Alpaca Trading Bot Started",
            f"Mode: {mode} (paper={self.config.alpaca.paper})\n"
            f"Trading style: {self.trading_style}\n"
            f"Equity: ${self.net_liquidation:,.2f}\n"
            f"Regime: {self.market_regime}\n"
            f"Core Symbols: {', '.join(self.config.universe.core_symbols)}\n"
        )

        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            self._signal_handler(signal.SIGINT, None)


if __name__ == "__main__":
    bot = AlpacaTradingBot()
    bot.start()
