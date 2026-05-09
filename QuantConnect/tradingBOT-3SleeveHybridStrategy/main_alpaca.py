#!/usr/bin/env python3
"""Alpaca deployment runner for the Three-Sleeve Hybrid strategy."""
from __future__ import annotations

import logging
import logging.handlers
import os
import signal
import smtplib
import sys
import time
from collections import deque
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import numpy as np
import pandas as pd
import pytz
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestBarRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce, OrderStatus
from alpaca.trading.requests import MarketOrderRequest

from bot_config import BotConfig
from strategy_core import (
    LABEL_HORIZON,
    MIN_SPY_BARS,
    MIN_TRAIN_ROWS,
    SAFETY_BUFFER,
    TRAIN_VAL_GAP,
    adx,
    band_index,
    closes,
    ema,
    evaluate_signal,
    get_features,
    normalize_bars,
    pct_return,
)

try:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import StandardScaler
except Exception:  # pragma: no cover - deployment may choose to run without sklearn.
    RandomForestClassifier = None
    StandardScaler = None


class ThreeSleeveHybridAlpaca:
    def __init__(self):
        self.config = BotConfig()
        self.et = pytz.timezone(self.config.runtime.timezone)
        self._setup_logging()

        acfg = self.config.alpaca
        self.trading_client = TradingClient(acfg.api_key, acfg.api_secret, paper=acfg.paper)
        self.data_client = StockHistoricalDataClient(acfg.api_key, acfg.api_secret)
        self.scheduler = BackgroundScheduler(timezone=self.et)

        self.model = (
            RandomForestClassifier(n_estimators=200, max_depth=6, min_samples_leaf=20, random_state=42)
            if RandomForestClassifier else None
        )
        self.scaler = StandardScaler() if StandardScaler else None
        self.trained = False

        self.s1_spy_weight = 0.0
        self.s1_gld_weight = 0.0
        self.s2_budget = 0.0
        self.s3_bull_market = False
        self.sleeves_active = False
        self.s3_allow = True
        self.s3_was_risk_off = False
        self.s3_risk_off_date: datetime | None = None
        self.s3_max_stress = 0.0

        self._starting_equity = self.config.runtime.starting_capital_fallback
        self._peak_equity = self._starting_equity
        self._prior_close_equity = self._starting_equity
        self._running = True
        self._daily_equity: list[dict] = []
        self.trade_history = deque(maxlen=200)
        self._email_config_warned = False

    # ------------------------------------------------------------------
    # Setup and account helpers
    # ------------------------------------------------------------------
    def _setup_logging(self):
        log_dir = Path(self.config.runtime.log_dir).expanduser()
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            log_dir = Path(__file__).resolve().parent / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger("ThreeSleeveHybridAlpaca")
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        self.logger.addHandler(stream)
        file_handler = logging.handlers.RotatingFileHandler(log_dir / "three_sleeve_hybrid.log", maxBytes=8_000_000, backupCount=5)
        file_handler.setFormatter(fmt)
        self.logger.addHandler(file_handler)

    @staticmethod
    def _safe_float(value, default: float = 0.0) -> float:
        try:
            return default if value is None else float(value)
        except (TypeError, ValueError):
            return default

    @property
    def equity(self) -> float:
        try:
            account = self.trading_client.get_account()
            return self._safe_float(getattr(account, "portfolio_value", None)) or self._safe_float(getattr(account, "equity", None))
        except Exception:
            return 0.0

    @property
    def cash(self) -> float:
        try:
            return self._safe_float(self.trading_client.get_account().cash)
        except Exception:
            return 0.0

    def _is_market_open(self) -> bool:
        try:
            return bool(self.trading_client.get_clock().is_open)
        except Exception as exc:
            self.logger.warning(f"market clock unavailable, assuming closed: {exc}")
            return False

    def _positions(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        try:
            for pos in self.trading_client.get_all_positions():
                qty = self._safe_float(pos.qty)
                if qty == 0:
                    continue
                out[pos.symbol.upper()] = {
                    "qty": qty,
                    "avg_cost": self._safe_float(pos.avg_entry_price),
                    "market_price": self._safe_float(pos.current_price),
                    "market_value": self._safe_float(pos.market_value),
                }
        except Exception as exc:
            self.logger.error(f"positions error: {exc}")
        return out

    def _latest_price(self, symbol: str) -> float:
        try:
            bars = self.data_client.get_stock_latest_bar(StockLatestBarRequest(symbol_or_symbols=[symbol]))
            bar = bars.get(symbol)
            if bar and bar.close:
                return float(bar.close)
        except Exception as exc:
            self.logger.debug(f"latest price error {symbol}: {exc}")
        pos = self._positions().get(symbol.upper())
        return self._safe_float((pos or {}).get("market_price"))

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------
    def _bars(self, symbol: str, days: int = 420) -> pd.DataFrame:
        try:
            req = StockBarsRequest(
                symbol_or_symbols=[symbol],
                timeframe=TimeFrame.Day,
                start=datetime.now(self.et) - timedelta(days=days),
                end=datetime.now(self.et),
            )
            bars = self.data_client.get_stock_bars(req).df
            if bars.empty:
                return pd.DataFrame()
            if isinstance(bars.index, pd.MultiIndex):
                if symbol in bars.index.get_level_values("symbol"):
                    bars = bars.xs(symbol, level="symbol")
                else:
                    return pd.DataFrame()
            return normalize_bars(bars, self.config.runtime.timezone)
        except Exception as exc:
            self.logger.debug(f"bars error {symbol}: {exc}")
            return pd.DataFrame()

    def _cboe_history(self, symbol: str, days: int = 4200) -> pd.DataFrame:
        urls = {
            "VIX": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv",
            "VIX3M": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX3M_History.csv",
        }
        try:
            response = requests.get(urls[symbol], timeout=15)
            response.raise_for_status()
            from io import StringIO

            df = pd.read_csv(StringIO(response.text))
            date_col = "DATE" if "DATE" in df.columns else df.columns[0]
            close_col = "CLOSE" if "CLOSE" in df.columns else df.columns[-1]
            out = pd.DataFrame(
                {"close": pd.to_numeric(df[close_col], errors="coerce").to_numpy()},
                index=pd.to_datetime(df[date_col]),
            )
            out["open"] = out["high"] = out["low"] = out["close"]
            out["volume"] = 0
            out.index = out.index.tz_localize(self.et)
            return out.dropna().sort_index().tail(days)
        except Exception as exc:
            self.logger.error(f"CBOE history error {symbol}: {exc}")
            return pd.DataFrame()

    def _signal_arrays(self, days: int = 420) -> dict[str, np.ndarray]:
        inst = self.config.instruments
        data = {
            "spy": closes(self._bars(inst.spy_signal, days), n=days),
            "gld": closes(self._bars(inst.gld_signal, days), n=days),
            "hyg": closes(self._bars(inst.hyg_signal, days), n=days),
            "lqd": closes(self._bars(inst.lqd_signal, days), n=days),
            "rsp": closes(self._bars(inst.rsp_signal, days), n=days),
            "ief": closes(self._bars(inst.ief_signal, days), n=days),
            "shy": closes(self._bars(inst.shy_signal, days), n=days),
            "vix": closes(self._cboe_history(inst.vix_symbol, days), n=days),
            "vix3m": closes(self._cboe_history(inst.vix3m_symbol, days), n=days),
        }
        return data

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def _submit_market(self, symbol: str, qty: float) -> bool:
        if abs(qty) <= 0:
            return False
        if not self._is_market_open():
            self.logger.warning(f"market closed; skipped order {symbol} qty={qty:.6f}")
            return False
        side = OrderSide.BUY if qty > 0 else OrderSide.SELL
        try:
            order = self.trading_client.submit_order(
                MarketOrderRequest(
                    symbol=symbol,
                    qty=round(abs(qty), self.config.risk.fractional_qty_decimals),
                    side=side,
                    time_in_force=TimeInForce.DAY,
                )
            )
            self.logger.info(f"ORDER {side.value.upper()} {abs(qty):.6f} {symbol}")
            for _ in range(30):
                time.sleep(1)
                final = self.trading_client.get_order_by_id(str(order.id))
                if final.status in {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED}:
                    if final.status == OrderStatus.FILLED:
                        fill = self._safe_float(getattr(final, "filled_avg_price", 0))
                        msg = (
                            f"{datetime.now(self.et):%Y-%m-%d %H:%M} "
                            f"{side.value.upper()} {symbol} qty={abs(qty):.6f}"
                            + (f" @ ${fill:.2f}" if fill > 0 else "")
                        )
                        self.trade_history.append(msg)
                        return True
                    self.logger.warning(f"order {symbol} ended with status={final.status}")
                    return False
            return False
        except Exception as exc:
            self.logger.error(f"order error {symbol} qty={qty}: {exc}")
            return False

    def _target_weight(self, symbol: str, target_weight: float):
        equity = self.equity
        price = self._latest_price(symbol)
        if equity <= 0 or price <= 0:
            return
        current_value = self._positions().get(symbol.upper(), {}).get("market_value", 0.0)
        delta_value = target_weight * equity - self._safe_float(current_value)
        if abs(delta_value) < self.config.risk.min_trade_notional:
            return
        qty = delta_value / price
        self._submit_market(symbol, qty)

    def _liquidate_symbols(self, symbols: list[str], reason: str):
        positions = self._positions()
        for symbol in symbols:
            pos = positions.get(symbol.upper())
            if not pos:
                continue
            qty = self._safe_float(pos.get("qty"))
            if qty != 0:
                self.logger.info(f"{reason}: closing {symbol} qty={qty:.6f}")
                self._submit_market(symbol, -qty)

    def _s1_symbols(self) -> list[str]:
        return [self.config.instruments.live_spy_hedge, self.config.instruments.live_gld_hedge]

    def _sleeve_symbol_sets(self) -> dict[str, set[str]]:
        return {
            "S1": {symbol.upper() for symbol in self._s1_symbols()},
            "S2": {symbol.upper() for symbol in self.config.universe.s2_candidates},
            "S3": {symbol.upper() for symbol in self.config.universe.s3_candidates},
        }

    def _sleeve_market_value(self, symbols: list[str]) -> float:
        wanted = set(symbol.upper() for symbol in symbols)
        return sum(
            self._safe_float(pos.get("market_value"))
            for symbol, pos in self._positions().items()
            if symbol.upper() in wanted
        )

    def _sleeve_exposures(self) -> dict[str, float]:
        equity = max(self.equity, 1e-9)
        positions = self._positions()
        sets = self._sleeve_symbol_sets()
        values = {"S1": 0.0, "S2": 0.0, "S3": 0.0, "OTHER": 0.0}
        for symbol, pos in positions.items():
            value = self._safe_float(pos.get("market_value"))
            if symbol in sets["S1"]:
                values["S1"] += value
            elif symbol in sets["S2"]:
                values["S2"] += value
            elif symbol in sets["S3"]:
                values["S3"] += value
            else:
                values["OTHER"] += value
        return {key: value / equity for key, value in values.items()}

    def _safe_set_macro(self):
        self._target_weight(self.config.instruments.live_spy_hedge, self.s1_spy_weight)
        self._target_weight(self.config.instruments.live_gld_hedge, self.s1_gld_weight)

    # ------------------------------------------------------------------
    # S1 ML and signal
    # ------------------------------------------------------------------
    def train_model(self):
        if not self.model or not self.scaler:
            self.logger.warning("[S1] sklearn unavailable; ML overlay disabled")
            self.trained = False
            return
        arrays = self._signal_arrays(days=4200)
        spy_c = arrays["spy"]
        vix_c = arrays["vix"]
        if len(spy_c) < MIN_SPY_BARS + MIN_TRAIN_ROWS or len(vix_c) < MIN_SPY_BARS + MIN_TRAIN_ROWS:
            self.logger.warning("[S1] insufficient history for ML training")
            return
        lc = min(len(spy_c), len(vix_c)) - LABEL_HORIZON - SAFETY_BUFFER
        te = lc - TRAIN_VAL_GAP
        idx = list(range(MIN_SPY_BARS, lc))
        future_returns = [spy_c[i + LABEL_HORIZON] / spy_c[i] - 1 for i in idx]
        median_return = float(np.median(future_returns))
        x_rows, y_rows = [], []
        for offset, i in enumerate(idx):
            features = get_features(
                vix_c[:i],
                spy_c[:i],
                vix3m_c=arrays["vix3m"][:i] if len(arrays["vix3m"]) >= i else None,
                hyg_c=arrays["hyg"][:i] if len(arrays["hyg"]) >= i else None,
                lqd_c=arrays["lqd"][:i] if len(arrays["lqd"]) >= i else None,
                rsp_c=arrays["rsp"][:i] if len(arrays["rsp"]) >= i else None,
                ief_c=arrays["ief"][:i] if len(arrays["ief"]) >= i else None,
                shy_c=arrays["shy"][:i] if len(arrays["shy"]) >= i else None,
            )
            if features:
                x_rows.append(features)
                y_rows.append(1 if future_returns[offset] > median_return else 0)
        if len(x_rows) < MIN_TRAIN_ROWS:
            self.logger.warning(f"[S1] too few ML rows: {len(x_rows)}")
            return
        split = max(0, te - MIN_SPY_BARS)
        x_arr = np.asarray(x_rows)
        y_arr = np.asarray(y_rows)
        self.scaler.fit(x_arr[:split])
        self.model.fit(self.scaler.transform(x_arr[:split]), y_arr[:split])
        self.trained = True
        self.logger.info(f"[S1] ML trained rows={len(x_rows)} train={split}")

    def _ml_signal(self, arrays: dict[str, np.ndarray]) -> bool:
        if not self.trained or not self.model or not self.scaler:
            return False
        features = get_features(
            arrays["vix"], arrays["spy"], arrays["vix3m"], arrays["hyg"],
            arrays["lqd"], arrays["rsp"], arrays["ief"], arrays["shy"],
        )
        if not features:
            return False
        try:
            probs = self.model.predict_proba(self.scaler.transform([features]))[0]
            return bool((probs[1] if len(probs) == 2 else 0.5) > self.config.sleeves.ml_threshold)
        except Exception as exc:
            self.logger.warning(f"[S1] ML inference failed: {exc}")
            return False

    def check_signal(self):
        arrays = self._signal_arrays(days=320)
        ml_on = self._ml_signal(arrays)
        state = evaluate_signal(arrays["spy"], arrays["vix"], ml_on)
        if not state:
            self.logger.warning("[S1] missing signal history")
            return

        prev_bull = self.s3_bull_market
        self.s3_bull_market = state.bull
        self.sleeves_active = state.sleeves_active
        self.s1_spy_weight = state.s1_spy_weight
        self.s1_gld_weight = state.s1_gld_weight
        self.s2_budget = state.s2_budget
        self.logger.info(
            f"[GATE] bull={state.bull} regime={state.regime_name} "
            f"SPY={state.spy_price:.2f} 50MA={state.sma50:.2f} 200MA={state.sma200:.2f} "
            f"20d={state.ret20:+.2%} VIX={state.vix:.1f} VIX80={state.vix80:.1f} ml={ml_on}"
        )

        if state.bull:
            self._liquidate_sleeve2()
            self._safe_set_macro()
            if not prev_bull:
                self._liquidate_sleeve3(transition=True)
                self.rebalance_sleeve3()
            else:
                target = self.equity * self.config.sleeves.s3_bull_budget
                actual = self._sleeve_market_value(self.config.universe.s3_candidates)
                if target > 0 and actual < target * 0.9375:
                    self.rebalance_sleeve3()
        else:
            if prev_bull:
                self._liquidate_sleeve3(transition=True)
            else:
                self._liquidate_sleeve3()
            self._safe_set_macro()
            if not state.sleeves_active:
                self._liquidate_sleeve2()
            elif self.s2_budget > 0 and self._sleeve_market_value(self.config.universe.s2_candidates) <= 0:
                self.rebalance_sleeve2()

    # ------------------------------------------------------------------
    # S2/S3
    # ------------------------------------------------------------------
    def _liquidate_sleeve2(self):
        self._liquidate_symbols(self.config.universe.s2_candidates, "[S2] LIQ")

    def _liquidate_sleeve3(self, transition: bool = False):
        symbols = list(self.config.universe.s3_candidates)
        if not transition:
            symbols = [s for s in symbols if s not in set(self.config.universe.s2_candidates)]
        self._liquidate_symbols(symbols, "[S3] LIQ")

    def rebalance_sleeve2(self):
        if self.s3_bull_market or not self.sleeves_active or self.s2_budget <= 0:
            self._liquidate_sleeve2()
            return
        scored = []
        for symbol in self.config.universe.s2_candidates:
            df = self._bars(symbol, days=self.config.s2.momentum_lookback + 40)
            ret = pct_return(closes(df), self.config.s2.momentum_lookback)
            if ret >= self.config.s2.momentum_min_return:
                scored.append((symbol, ret))
            time.sleep(0.15)
        top = sorted(scored, key=lambda item: item[1], reverse=True)[: self.config.s2.max_positions]
        if not top:
            self._liquidate_sleeve2()
            return
        per_pos = min(self.s2_budget / len(top), self.config.s2.max_position_weight * self.s2_budget)
        keep = {symbol for symbol, _ in top}
        for symbol, ret in top:
            self.logger.info(f"[S2] target {symbol} weight={per_pos:.2%} roc63={ret:+.2%}")
            self._target_weight(symbol, per_pos)
        self._liquidate_symbols([s for s in self.config.universe.s2_candidates if s not in keep], "[S2] stale")

    def _s3_band_stress(self) -> float:
        idxs = []
        cfg = self.config.s3
        for symbol in self.config.universe.s3_candidates:
            df = self._bars(symbol, days=cfg.band_len + 10)
            if len(df) < cfg.band_len:
                continue
            close = df["close"].astype(float)
            mid = float(ema(close, cfg.band_len).iloc[-1])
            dev = float(close.tail(cfg.band_len).std(ddof=0))
            if dev <= 0:
                continue
            bands = [
                mid - dev * 1.618, mid - dev * 1.382, mid - dev, mid - dev * 0.809,
                mid - dev * 0.5, mid - dev * 0.382, mid, mid + dev * 0.382,
                mid + dev * 0.5, mid + dev * 0.809, mid + dev, mid + dev * 1.382,
                mid + dev * 1.618,
            ]
            idxs.append(band_index(float(close.iloc[-1]), bands))
            time.sleep(0.05)
        if len(idxs) < 20:
            return 0.0
        return sum(idx in cfg.bottom_levels for idx in idxs) / len(idxs)

    def rebalance_sleeve3(self):
        if not self.s3_bull_market:
            self._liquidate_sleeve3(transition=True)
            return
        stress = self._s3_band_stress()
        self.s3_max_stress = max(self.s3_max_stress, stress)
        now = datetime.now(self.et)
        if stress >= 0.40:
            self.s3_was_risk_off = True
            self.s3_risk_off_date = self.s3_risk_off_date or now
            self.s3_allow = False
            self.logger.warning(f"[S3] risk-off stress={stress:.1%}")
        elif self.s3_was_risk_off:
            denom = max(self.s3_max_stress, 0.10)
            improvement = (self.s3_max_stress - stress) / denom
            days_off = (now - self.s3_risk_off_date).days if self.s3_risk_off_date else 0
            self.s3_allow = improvement >= 0.60 or stress < 0.15 or days_off > 180
            if self.s3_allow:
                self.s3_was_risk_off = False
                self.s3_max_stress = 0.0
                self.s3_risk_off_date = None
        else:
            self.s3_allow = True
        if not self.s3_allow:
            self._liquidate_sleeve3()
            return

        scored = []
        cfg = self.config.s3
        for symbol in self.config.universe.s3_candidates:
            df = self._bars(symbol, days=max(cfg.lookbacks) + 50)
            if len(df) < max(cfg.lookbacks) + 1:
                continue
            close = df["close"].astype(float)
            trend = adx(df, cfg.adx_period).iloc[-1]
            if not np.isfinite(trend) or trend > cfg.adx_limit:
                continue
            ma = float(ema(close, cfg.band_len if len(close) >= cfg.band_len else 50).iloc[-1])
            if float(close.iloc[-1]) <= ma:
                continue
            momentum = np.mean([pct_return(close, lb) for lb in cfg.lookbacks])
            if momentum > 0:
                scored.append((symbol, momentum * float(trend)))
            time.sleep(0.15)
        top = sorted(scored, key=lambda item: item[1], reverse=True)[: cfg.stock_count]
        if not top:
            self._liquidate_sleeve3()
            return
        raw_total = sum(score for _, score in top)
        weights = {
            symbol: min(cfg.max_position_weight, score / raw_total) for symbol, score in top if raw_total > 0
        }
        scale = self.config.sleeves.s3_bull_budget / sum(weights.values())
        targets = {symbol: weight * scale for symbol, weight in weights.items()}
        for symbol, weight in sorted(targets.items(), key=lambda item: item[1], reverse=True):
            self.logger.info(f"[S3] target {symbol} weight={weight:.2%}")
            self._target_weight(symbol, weight)
        self._liquidate_symbols([s for s in self.config.universe.s3_candidates if s not in targets], "[S3] stale")

    def daily_snapshot(self):
        equity = self.equity
        self._peak_equity = max(self._peak_equity, equity)
        self._daily_equity.append({"time": datetime.now(self.et), "equity": equity, "cash": self.cash})
        exposures = self._sleeve_exposures()
        self.logger.info(
            f"[SNAP] equity=${equity:,.2f} cash=${self.cash:,.2f} "
            f"mode={'S3+S1' if self.s3_bull_market else 'S1+S2'} "
            f"S1={exposures['S1']:.1%} S2={exposures['S2']:.1%} S3={exposures['S3']:.1%}"
        )
        self.send_portfolio_summary_email()
        self._prior_close_equity = equity

    # ------------------------------------------------------------------
    # Email
    # ------------------------------------------------------------------
    def _position_lines(self) -> list[str]:
        positions = self._positions()
        sets = self._sleeve_symbol_sets()
        lines = []
        for symbol, pos in sorted(positions.items()):
            qty = self._safe_float(pos.get("qty"))
            avg = self._safe_float(pos.get("avg_cost"))
            price = self._safe_float(pos.get("market_price"))
            if price <= 0:
                price = self._latest_price(symbol) or avg
            value = self._safe_float(pos.get("market_value")) or qty * price
            pnl = qty * (price - avg)
            pnl_pct = (price / avg - 1.0) if avg > 0 else 0.0
            if symbol in sets["S1"]:
                sleeve = "S1"
            elif symbol in sets["S2"]:
                sleeve = "S2"
            elif symbol in sets["S3"]:
                sleeve = "S3"
            else:
                sleeve = "MANUAL"
            lines.append(
                f"  {symbol:<7} | {sleeve:<6} | Qty:{qty:>10.4f} | "
                f"Avg:${avg:>8.2f} | Now:${price:>8.2f} | "
                f"Value:${value:>10,.2f} | P&L:${pnl:>9,.2f} ({pnl_pct:>7.2%})"
            )
        return lines

    def _email_header_metrics(self) -> dict[str, float | str]:
        equity = self.equity
        total_return = (equity - self._starting_equity) / self._starting_equity if self._starting_equity > 0 else 0.0
        daily_pnl = equity - self._prior_close_equity
        daily_ret = daily_pnl / self._prior_close_equity if self._prior_close_equity > 0 else 0.0
        drawdown = (self._peak_equity - equity) / self._peak_equity if self._peak_equity > 0 else 0.0
        return {
            "equity": equity,
            "cash": self.cash,
            "total_return": total_return,
            "daily_pnl": daily_pnl,
            "daily_ret": daily_ret,
            "drawdown": drawdown,
            "mode": "S3+S1" if self.s3_bull_market else "S1+S2",
        }

    def send_portfolio_summary_email(self):
        try:
            now_et = datetime.now(self.et)
            metrics = self._email_header_metrics()
            exposures = self._sleeve_exposures()
            body = (
                f"{'=' * 76}\nTHREE-SLEEVE HYBRID PORTFOLIO SUMMARY\n{'=' * 76}\n\n"
                f"Equity:        ${metrics['equity']:,.2f}\n"
                f"Cash:          ${metrics['cash']:,.2f}\n"
                f"Total Return:  {metrics['total_return']:.2%}\n"
                f"Daily P&L:     ${metrics['daily_pnl']:,.2f} ({metrics['daily_ret']:.2%})\n"
                f"Drawdown:      {metrics['drawdown']:.2%}\n"
                f"Mode:          {metrics['mode']}\n"
                f"Sleeves:       S1={exposures['S1']:.1%} S2={exposures['S2']:.1%} "
                f"S3={exposures['S3']:.1%} Other={exposures['OTHER']:.1%}\n"
                f"Targets:       S1={self.s1_spy_weight + self.s1_gld_weight:.1%} "
                f"S2={self.s2_budget:.1%} S3={self.config.sleeves.s3_bull_budget if self.s3_bull_market else 0.0:.1%}\n\n"
                f"POSITIONS\n{'-' * 76}\n"
            )
            pos_lines = self._position_lines()
            body += ("\n".join(pos_lines) + "\n") if pos_lines else "  No open positions\n"
            body += f"\nRECENT ORDERS\n{'-' * 76}\n"
            recent = list(self.trade_history)[-12:]
            body += ("\n".join(f"  {line}" for line in recent) + "\n") if recent else "  No recent algo orders\n"
            body += f"\n{'=' * 76}\nGenerated (US/Eastern): {now_et:%Y-%m-%d %H:%M:%S}\n"
            self._send_email(f"Portfolio Summary - {now_et:%Y-%m-%d %H:%M} ET", body)
        except Exception as exc:
            self.logger.error(f"portfolio email error: {exc}")

    def send_weekly_summary_email(self):
        try:
            now_et = datetime.now(self.et)
            metrics = self._email_header_metrics()
            exposures = self._sleeve_exposures()
            week_rows = [
                row for row in self._daily_equity
                if now_et - row["time"] <= timedelta(days=7)
            ]
            week_start_equity = week_rows[0]["equity"] if week_rows else self._starting_equity
            weekly_return = (
                (metrics["equity"] - week_start_equity) / week_start_equity
                if week_start_equity > 0 else 0.0
            )
            body = (
                f"{'=' * 80}\nTHREE-SLEEVE HYBRID WEEKLY SUMMARY\n{'=' * 80}\n\n"
                f"Equity:        ${metrics['equity']:,.2f}\n"
                f"Weekly Return: {weekly_return:.2%}\n"
                f"Total Return:  {metrics['total_return']:.2%}\n"
                f"Drawdown:      {metrics['drawdown']:.2%}\n"
                f"Mode:          {metrics['mode']}\n"
                f"Sleeves:       S1={exposures['S1']:.1%} S2={exposures['S2']:.1%} "
                f"S3={exposures['S3']:.1%} Other={exposures['OTHER']:.1%}\n"
                f"Open Positions:{len(self._positions())}\n\n"
                f"RECENT ORDERS (last 20)\n{'-' * 80}\n"
            )
            recent = list(self.trade_history)[-20:]
            body += ("\n".join(f"  {line}" for line in recent) + "\n") if recent else "  No recent algo orders\n"
            body += f"\n{'=' * 80}\nWeekly Report (US/Eastern): {now_et:%Y-%m-%d %H:%M:%S}\n"
            self._send_email(f"Weekly Portfolio Summary - {now_et:%Y-%m-%d} ET", body)
        except Exception as exc:
            self.logger.error(f"weekly email error: {exc}")

    def _send_email(self, subject: str, body: str):
        cfg = self.config.email
        if not cfg.enabled:
            return
        if not all([cfg.smtp_server, cfg.sender_email, cfg.sender_password, cfg.recipient_email]):
            if not self._email_config_warned:
                self.logger.warning("email disabled: BOT_EMAIL_USER/BOT_EMAIL_PASS/BOT_EMAIL_TO not fully configured")
                self._email_config_warned = True
            return
        try:
            msg = MIMEMultipart()
            msg["From"] = cfg.sender_email
            msg["To"] = cfg.recipient_email
            tag = "[THREE-SLEEVE ALPACA]"
            clean_subject = subject.strip()
            msg["Subject"] = clean_subject if clean_subject.startswith(tag) else f"{tag} {clean_subject}"
            msg["X-Bot-Name"] = "THREE-SLEEVE ALPACA"
            msg["X-Bot-System"] = "tradingBOT-3SleeveHybridStrategy"
            msg.attach(MIMEText(body, "plain"))
            with smtplib.SMTP(cfg.smtp_server, cfg.smtp_port) as server:
                server.starttls()
                server.login(cfg.sender_email, cfg.sender_password)
                server.sendmail(cfg.sender_email, cfg.recipient_email, msg.as_string())
            self.logger.info(f"email sent: {subject}")
        except Exception as exc:
            self.logger.error(f"email send error: {exc}")

    # ------------------------------------------------------------------
    # Scheduler
    # ------------------------------------------------------------------
    def _safe_job(self, func):
        def wrapper():
            try:
                self.logger.info(f"job start: {func.__name__}")
                func()
            except Exception as exc:
                self.logger.exception(f"job error {func.__name__}: {exc}")
        return wrapper

    def setup_scheduler(self):
        mf = "mon-fri"
        self.scheduler.add_job(self._safe_job(self.train_model), CronTrigger(day=1, hour=13, minute=30, timezone=self.et), id="train_model", replace_existing=True)
        self.scheduler.add_job(self._safe_job(self.check_signal), CronTrigger(day_of_week=mf, hour=14, minute=0, timezone=self.et), id="check_signal", replace_existing=True)
        self.scheduler.add_job(self._safe_job(self.rebalance_sleeve2), CronTrigger(day=1, hour=14, minute=30, timezone=self.et), id="rebalance_s2", replace_existing=True)
        self.scheduler.add_job(self._safe_job(self.rebalance_sleeve3), CronTrigger(day="last", hour=15, minute=30, timezone=self.et), id="rebalance_s3", replace_existing=True)
        self.scheduler.add_job(self._safe_job(self.daily_snapshot), CronTrigger(day_of_week=mf, hour=15, minute=59, timezone=self.et), id="snapshot", replace_existing=True)
        self.scheduler.add_job(self._safe_job(self.send_weekly_summary_email), CronTrigger(day_of_week="fri", hour=16, minute=10, timezone=self.et), id="weekly_email", replace_existing=True)

    def _signal_handler(self, signum, frame):
        self.logger.info(f"signal {signum}; shutting down")
        self._running = False
        self.scheduler.shutdown(wait=False)
        sys.exit(0)

    def start(self):
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
        account = self.trading_client.get_account()
        self._starting_equity = self._safe_float(getattr(account, "portfolio_value", None)) or self._safe_float(getattr(account, "equity", None))
        self._peak_equity = self._starting_equity
        self._prior_close_equity = self._starting_equity
        self.logger.info(
            f"Connected to Alpaca {'paper' if self.config.alpaca.paper else 'live'} "
            f"equity=${self._starting_equity:,.2f} cash=${self.cash:,.2f}"
        )
        self.setup_scheduler()
        self.scheduler.start()
        if self._is_market_open():
            self.check_signal()
        else:
            self.logger.info("market closed at startup; waiting for scheduled jobs before trading")
        while self._running:
            time.sleep(5)


if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parent)
    ThreeSleeveHybridAlpaca().start()
