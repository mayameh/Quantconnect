#!/usr/bin/env python3
"""Backtest harness for the Alpaca-native Three-Sleeve Hybrid strategy."""
from __future__ import annotations

import argparse
from io import StringIO
import sys
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bot_config import BotConfig
from strategy_core import adx, band_index, closes, ema, evaluate_signal, normalize_bars, pct_return


@dataclass
class SimPosition:
    qty: float
    avg_cost: float


@dataclass
class SimPortfolio:
    cash: float
    positions: dict[str, SimPosition] = field(default_factory=dict)
    prices: dict[str, float] = field(default_factory=dict)

    def update_price(self, symbol: str, price: float):
        if price > 0:
            self.prices[symbol] = price

    def equity(self) -> float:
        total = self.cash
        for symbol, pos in self.positions.items():
            total += pos.qty * self.prices.get(symbol, pos.avg_cost)
        return float(total)

    def market_value(self, symbol: str) -> float:
        pos = self.positions.get(symbol)
        if not pos:
            return 0.0
        return pos.qty * self.prices.get(symbol, pos.avg_cost)

    def order(self, symbol: str, qty: float, price: float):
        if abs(qty) <= 0 or price <= 0:
            return
        pos = self.positions.get(symbol)
        if qty > 0:
            cost = qty * price
            if cost > self.cash:
                qty = self.cash / price
                cost = qty * price
            if qty <= 0:
                return
            if pos:
                new_qty = pos.qty + qty
                pos.avg_cost = (pos.qty * pos.avg_cost + qty * price) / new_qty
                pos.qty = new_qty
            else:
                self.positions[symbol] = SimPosition(qty=qty, avg_cost=price)
            self.cash -= cost
        else:
            if not pos:
                return
            sell_qty = min(abs(qty), pos.qty)
            self.cash += sell_qty * price
            pos.qty -= sell_qty
            if pos.qty <= 1e-8:
                self.positions.pop(symbol, None)


class ThreeSleeveHybridBacktest:
    def __init__(
        self,
        start: str,
        end: str,
        capital: float,
        use_live_hedges: bool = False,
        data_provider: str = "alpaca",
    ):
        self.config = BotConfig()
        self.start = pd.Timestamp(start, tz=self.config.runtime.timezone)
        self.end = pd.Timestamp(end, tz=self.config.runtime.timezone)
        self.portfolio = SimPortfolio(cash=float(capital))
        self.use_live_hedges = use_live_hedges
        self.data_provider = data_provider.strip().lower()
        self.alpaca_data_client = None
        if self.data_provider == "alpaca":
            acfg = self.config.alpaca
            if not acfg.api_key or not acfg.api_secret:
                raise RuntimeError(
                    "Alpaca data provider requires APCA_API_KEY_ID and APCA_API_SECRET_KEY"
                )
            self.alpaca_data_client = StockHistoricalDataClient(acfg.api_key, acfg.api_secret)

        self.data: dict[str, pd.DataFrame] = {}
        self.trades: list[dict] = []
        self.equity_curve: list[dict] = []
        self.s1_spy_weight = 0.0
        self.s1_gld_weight = 0.0
        self.s2_budget = 0.0
        self.s3_bull_market = False
        self.sleeves_active = False
        self.s3_allow = True
        self.s3_was_risk_off = False
        self.s3_risk_off_date = None
        self.s3_max_stress = 0.0
        self.signal_counts = {"bull": 0, "non_bull": 0, "missing": 0}
        self._last_s2_rebalance_month: tuple[int, int] | None = None
        self._last_s3_rebalance_month: tuple[int, int] | None = None

    def _symbol_map(self) -> dict[str, str]:
        inst = self.config.instruments
        return {
            "SPY_SIGNAL": inst.spy_signal,
            "GLD_SIGNAL": inst.gld_signal,
            "HYG": inst.hyg_signal,
            "LQD": inst.lqd_signal,
            "RSP": inst.rsp_signal,
            "IEF": inst.ief_signal,
            "SHY": inst.shy_signal,
            "VIX": "^VIX",
            "VIX3M": "^VIX3M",
            "SPY_HEDGE": inst.live_spy_hedge if self.use_live_hedges else "SPY",
            "GLD_HEDGE": inst.live_gld_hedge if self.use_live_hedges else "GLD",
        }

    def _all_download_symbols(self) -> list[str]:
        mapped = set(self._symbol_map().values())
        mapped.update(self.config.universe.s2_candidates)
        mapped.update(self.config.universe.s3_candidates)
        return sorted(mapped)

    def _load_yfinance_symbol(self, symbol: str, dl_start, dl_end) -> pd.DataFrame:
        import yfinance as yf

        yf_symbol = symbol.replace(".", "-") if symbol == "BRK.B" else symbol
        raw = yf.download(
            yf_symbol,
            start=dl_start,
            end=dl_end,
            interval="1d",
            auto_adjust=False,
            progress=False,
        )
        return normalize_bars(raw, self.config.runtime.timezone)

    def _load_alpaca_symbol(self, symbol: str, dl_start, dl_end) -> pd.DataFrame:
        if self.alpaca_data_client is None:
            return pd.DataFrame()
        req = StockBarsRequest(
            symbol_or_symbols=[symbol],
            timeframe=TimeFrame.Day,
            start=pd.Timestamp(dl_start, tz=self.config.runtime.timezone).to_pydatetime(),
            end=pd.Timestamp(dl_end, tz=self.config.runtime.timezone).to_pydatetime(),
        )
        bars = self.alpaca_data_client.get_stock_bars(req).df
        if bars.empty:
            return pd.DataFrame()
        if isinstance(bars.index, pd.MultiIndex):
            if symbol not in bars.index.get_level_values("symbol"):
                return pd.DataFrame()
            bars = bars.xs(symbol, level="symbol")
        return normalize_bars(bars, self.config.runtime.timezone)

    def _load_cboe_symbol(self, symbol: str, dl_start, dl_end) -> pd.DataFrame:
        name = "VIX3M" if "VIX3M" in symbol.upper() else "VIX"
        urls = {
            "VIX": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv",
            "VIX3M": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX3M_History.csv",
        }
        response = requests.get(urls[name], timeout=20)
        response.raise_for_status()
        raw = pd.read_csv(StringIO(response.text))
        date_col = "DATE" if "DATE" in raw.columns else raw.columns[0]
        close_col = "CLOSE" if "CLOSE" in raw.columns else raw.columns[-1]
        out = pd.DataFrame(
            {"close": pd.to_numeric(raw[close_col], errors="coerce").to_numpy()},
            index=pd.to_datetime(raw[date_col]),
        )
        out["open"] = out["high"] = out["low"] = out["close"]
        out["volume"] = 0
        out.index = out.index.tz_localize(self.config.runtime.timezone)
        start_ts = pd.Timestamp(dl_start, tz=self.config.runtime.timezone)
        end_ts = pd.Timestamp(dl_end, tz=self.config.runtime.timezone)
        return out[(out.index >= start_ts) & (out.index <= end_ts)].dropna().sort_index()

    def load_data(self):
        symbols = self._all_download_symbols()
        dl_start = (self.start - timedelta(days=4600)).date()
        dl_end = (self.end + timedelta(days=5)).date()
        print(f"Loading {len(symbols)} symbols via {self.data_provider}...")
        for symbol in symbols:
            try:
                if symbol.startswith("^VIX"):
                    clean = self._load_cboe_symbol(symbol, dl_start, dl_end)
                elif self.data_provider == "alpaca":
                    clean = self._load_alpaca_symbol(symbol, dl_start, dl_end)
                elif self.data_provider == "yfinance":
                    clean = self._load_yfinance_symbol(symbol, dl_start, dl_end)
                else:
                    raise ValueError(f"Unknown data provider: {self.data_provider}")
                if clean.empty:
                    print(f"  x {symbol}")
                    continue
                self.data[symbol] = clean
                print(f"  ok {symbol}: {len(clean)} bars")
            except Exception as exc:
                print(f"  x {symbol}: {exc}")

    def _bars(self, symbol: str, ts, n: int | None = None) -> pd.DataFrame:
        df = self.data.get(symbol, pd.DataFrame())
        if df.empty:
            return pd.DataFrame()
        view = df[df.index <= ts]
        return view.tail(n) if n else view

    def _price(self, symbol: str, ts) -> float:
        df = self._bars(symbol, ts, 1)
        if df.empty:
            return 0.0
        return float(df["close"].iloc[-1])

    def _update_prices(self, ts):
        for symbol in self.data:
            price = self._price(symbol, ts)
            if price > 0:
                self.portfolio.update_price(symbol, price)

    def _target_weight(self, symbol: str, target_weight: float, ts, reason: str):
        equity = self.portfolio.equity()
        price = self._price(symbol, ts)
        if equity <= 0 or price <= 0:
            return
        current = self.portfolio.market_value(symbol)
        delta = target_weight * equity - current
        if abs(delta) < self.config.risk.min_trade_notional:
            return
        qty = delta / price
        before_qty = self.portfolio.positions.get(symbol, SimPosition(0, 0)).qty
        self.portfolio.order(symbol, qty, price)
        after_qty = self.portfolio.positions.get(symbol, SimPosition(0, 0)).qty
        filled = after_qty - before_qty
        if abs(filled) > 1e-8:
            self.trades.append({
                "date": ts.date(),
                "symbol": symbol,
                "action": "BUY" if filled > 0 else "SELL",
                "qty": abs(filled),
                "price": price,
                "reason": reason,
            })

    def _liquidate(self, symbols: list[str], ts, reason: str):
        for symbol in symbols:
            pos = self.portfolio.positions.get(symbol)
            price = self._price(symbol, ts)
            if pos and price > 0:
                qty = pos.qty
                self.portfolio.order(symbol, -qty, price)
                self.trades.append({
                    "date": ts.date(),
                    "symbol": symbol,
                    "action": "SELL",
                    "qty": qty,
                    "price": price,
                    "reason": reason,
                })

    def _arrays(self, ts, n: int = 320) -> dict[str, np.ndarray]:
        m = self._symbol_map()
        return {
            "spy": closes(self._bars(m["SPY_SIGNAL"], ts, n)),
            "vix": closes(self._bars(m["VIX"], ts, n)),
        }

    def _s1_hedges(self) -> tuple[str, str]:
        m = self._symbol_map()
        return m["SPY_HEDGE"], m["GLD_HEDGE"]

    def _safe_set_macro(self, ts):
        spy_hedge, gld_hedge = self._s1_hedges()
        self._target_weight(spy_hedge, self.s1_spy_weight, ts, "S1")
        self._target_weight(gld_hedge, self.s1_gld_weight, ts, "S1")

    def _sleeve_value(self, symbols: list[str]) -> float:
        wanted = set(symbols)
        return sum(
            self.portfolio.market_value(symbol)
            for symbol in wanted
            if symbol in self.portfolio.positions
        )

    def _liquidate_sleeve2(self, ts):
        self._liquidate(list(self.config.universe.s2_candidates), ts, "S2_liq")

    def _liquidate_sleeve3(self, ts, transition: bool = False):
        symbols = list(self.config.universe.s3_candidates)
        if not transition:
            s2 = set(self.config.universe.s2_candidates)
            symbols = [s for s in symbols if s not in s2]
        self._liquidate(symbols, ts, "S3_liq")

    def check_signal(self, ts):
        arrays = self._arrays(ts)
        state = evaluate_signal(arrays["spy"], arrays["vix"], ml_on=False)
        if not state:
            self.signal_counts["missing"] += 1
            return
        self.signal_counts["bull" if state.bull else "non_bull"] += 1
        prev_bull = self.s3_bull_market
        self.s3_bull_market = state.bull
        self.sleeves_active = state.sleeves_active
        self.s1_spy_weight = state.s1_spy_weight
        self.s1_gld_weight = state.s1_gld_weight
        self.s2_budget = state.s2_budget
        if state.bull:
            self._liquidate_sleeve2(ts)
            self._safe_set_macro(ts)
            if not prev_bull:
                self._liquidate_sleeve3(ts, transition=True)
                self.rebalance_sleeve3(ts)
            else:
                s3_actual = self._sleeve_value(self.config.universe.s3_candidates)
                target = self.portfolio.equity() * self.config.sleeves.s3_bull_budget
                if target > 0 and s3_actual < target * 0.9375:
                    self.rebalance_sleeve3(ts)
        else:
            if prev_bull:
                self._liquidate_sleeve3(ts, transition=True)
            else:
                self._liquidate_sleeve3(ts)
            self._safe_set_macro(ts)
            if not state.sleeves_active:
                self._liquidate_sleeve2(ts)
            elif self.s2_budget > 0 and self._sleeve_value(self.config.universe.s2_candidates) <= 0:
                self.rebalance_sleeve2(ts)

    def rebalance_sleeve2(self, ts):
        if self.s3_bull_market or not self.sleeves_active or self.s2_budget <= 0:
            self._liquidate_sleeve2(ts)
            return
        scored = []
        for symbol in self.config.universe.s2_candidates:
            ret = pct_return(closes(self._bars(symbol, ts)), self.config.s2.momentum_lookback)
            if ret >= self.config.s2.momentum_min_return:
                scored.append((symbol, ret))
        top = sorted(scored, key=lambda item: item[1], reverse=True)[: self.config.s2.max_positions]
        if not top:
            self._liquidate_sleeve2(ts)
            return
        per_pos = min(self.s2_budget / len(top), self.config.s2.max_position_weight * self.s2_budget)
        keep = {symbol for symbol, _ in top}
        for symbol, _ in top:
            self._target_weight(symbol, per_pos, ts, "S2_rebalance")
        self._liquidate([s for s in self.config.universe.s2_candidates if s not in keep], ts, "S2_stale")

    def _s3_band_stress(self, ts) -> float:
        idxs = []
        cfg = self.config.s3
        for symbol in self.config.universe.s3_candidates:
            df = self._bars(symbol, ts, cfg.band_len + 10)
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
        if len(idxs) < 20:
            return 0.0
        return sum(idx in cfg.bottom_levels for idx in idxs) / len(idxs)

    def rebalance_sleeve3(self, ts):
        if not self.s3_bull_market:
            self._liquidate_sleeve3(ts, transition=True)
            return
        stress = self._s3_band_stress(ts)
        self.s3_max_stress = max(self.s3_max_stress, stress)
        if stress >= 0.40:
            self.s3_allow = False
            self.s3_was_risk_off = True
            self.s3_risk_off_date = ts
        elif self.s3_was_risk_off:
            denom = max(self.s3_max_stress, 0.10)
            improvement = (self.s3_max_stress - stress) / denom
            days_off = (ts - self.s3_risk_off_date).days if self.s3_risk_off_date is not None else 0
            self.s3_allow = improvement >= 0.60 or stress < 0.15 or days_off > 180
            if self.s3_allow:
                self.s3_was_risk_off = False
                self.s3_max_stress = 0.0
                self.s3_risk_off_date = None
        else:
            self.s3_allow = True
        if not self.s3_allow:
            self._liquidate_sleeve3(ts)
            return

        cfg = self.config.s3
        scored = []
        for symbol in self.config.universe.s3_candidates:
            df = self._bars(symbol, ts, max(cfg.lookbacks) + 50)
            if len(df) < max(cfg.lookbacks) + 1:
                continue
            close = df["close"].astype(float)
            trend = adx(df, cfg.adx_period).iloc[-1]
            if not np.isfinite(trend) or trend > cfg.adx_limit:
                continue
            ma_len = cfg.band_len if len(close) >= cfg.band_len else 50
            if float(close.iloc[-1]) <= float(ema(close, ma_len).iloc[-1]):
                continue
            momentum = np.mean([pct_return(close, lb) for lb in cfg.lookbacks])
            if momentum > 0:
                scored.append((symbol, momentum * float(trend)))
        top = sorted(scored, key=lambda item: item[1], reverse=True)[: cfg.stock_count]
        if not top:
            self._liquidate_sleeve3(ts)
            return
        raw_total = sum(score for _, score in top)
        capped = {symbol: min(cfg.max_position_weight, score / raw_total) for symbol, score in top if raw_total > 0}
        scale = self.config.sleeves.s3_bull_budget / sum(capped.values())
        targets = {symbol: weight * scale for symbol, weight in capped.items()}
        for symbol, weight in targets.items():
            self._target_weight(symbol, weight, ts, "S3_rebalance")
        self._liquidate([s for s in self.config.universe.s3_candidates if s not in targets], ts, "S3_stale")

    def process_day(self, ts):
        self._update_prices(ts)
        self.check_signal(ts)
        month_key = (ts.year, ts.month)
        if (
            ts.day <= 7
            and self._last_s2_rebalance_month != month_key
            and not self.s3_bull_market
        ):
            self.rebalance_sleeve2(ts)
            self._last_s2_rebalance_month = month_key
        next_day = ts + timedelta(days=1)
        if (
            next_day.month != ts.month
            and self._last_s3_rebalance_month != month_key
            and self.s3_bull_market
        ):
            self.rebalance_sleeve3(ts)
            self._last_s3_rebalance_month = month_key
        self.equity_curve.append({
            "date": ts.date(),
            "equity": self.portfolio.equity(),
            "cash": self.portfolio.cash,
            "mode": "S3+S1" if self.s3_bull_market else "S1+S2",
        })

    def run(self):
        self.load_data()
        spy = self.data.get(self._symbol_map()["SPY_SIGNAL"], pd.DataFrame())
        if spy.empty:
            raise RuntimeError("SPY data missing; cannot run backtest")
        dates = spy[(spy.index >= self.start) & (spy.index <= self.end)].index
        for ts in dates:
            self.process_day(ts)

    def report(self):
        eq = pd.DataFrame(self.equity_curve)
        if eq.empty:
            print("No equity curve.")
            return {}
        start_eq = float(eq["equity"].iloc[0])
        end_eq = float(eq["equity"].iloc[-1])
        ret = end_eq / start_eq - 1 if start_eq > 0 else 0.0
        dd = eq["equity"] / eq["equity"].cummax() - 1
        daily = eq["equity"].pct_change().dropna()
        sharpe = daily.mean() / daily.std() * np.sqrt(252) if len(daily) > 2 and daily.std() > 0 else 0.0
        trades = pd.DataFrame(self.trades)
        print("\n" + "=" * 72)
        print("THREE-SLEEVE HYBRID BACKTEST")
        print("=" * 72)
        print(f"Period:   {self.start.date()} to {self.end.date()}")
        print(f"Start:    ${start_eq:,.2f}")
        print(f"End:      ${end_eq:,.2f}")
        print(f"Return:   {ret:+.2%}")
        print(f"Max DD:   {dd.min():.2%}")
        print(f"Sharpe:   {sharpe:.2f}")
        print(f"Trades:   {len(trades)}")
        print(
            "Signals:  "
            f"bull={self.signal_counts['bull']} "
            f"non_bull={self.signal_counts['non_bull']} "
            f"missing={self.signal_counts['missing']}"
        )
        if not trades.empty:
            print("\nRecent trades:")
            print(trades.tail(20).to_string(index=False))
        return {
            "start_equity": start_eq,
            "end_equity": end_eq,
            "return_pct": ret * 100,
            "max_drawdown_pct": float(dd.min() * 100),
            "sharpe": float(sharpe),
            "trades": int(len(trades)),
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest Three-Sleeve Hybrid Alpaca port")
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default="2021-01-01")
    parser.add_argument("--capital", type=float, default=100_000)
    parser.add_argument("--live-hedges", action="store_true", help="Use BRK.B/NEM S1 hedges instead of SPY/GLD")
    parser.add_argument(
        "--data-provider",
        default="alpaca",
        choices=["alpaca", "yfinance"],
        help="Historical stock-bar provider. VIX/VIX3M still load from CBOE CSV.",
    )
    args = parser.parse_args()

    harness = ThreeSleeveHybridBacktest(
        args.start,
        args.end,
        args.capital,
        use_live_hedges=args.live_hedges,
        data_provider=args.data_provider,
    )
    harness.run()
    harness.report()
