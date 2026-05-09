"""
Mock Alpaca client for backtesting.
Mimics the alpaca-py SDK interfaces used by main_alpaca.py:
  - TradingClient  (account, positions, orders)
  - StockHistoricalDataClient  (bars, latest bar)
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd


# ── Lightweight data-class stubs ─────────────────────────────────────────────

class _Account:
    def __init__(self, cash: float, portfolio_value: float, buying_power: float):
        self.cash            = str(cash)
        self.portfolio_value = str(portfolio_value)
        self.buying_power    = str(buying_power)


class _Position:
    def __init__(self, symbol: str, qty: float, avg_entry_price: float,
                 current_price: float, market_value: float, unrealized_pl: float):
        self.symbol           = symbol
        self.qty              = str(qty)
        self.avg_entry_price  = str(avg_entry_price)
        self.current_price    = str(current_price)
        self.market_value     = str(market_value)
        self.unrealized_pl    = str(unrealized_pl)


class _OrderStatus:
    FILLED   = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED  = "expired"
    ACCEPTED = "accepted"
    NEW      = "new"


class _Order:
    def __init__(self, order_id: str, symbol: str, qty: int, side: str,
                 status: str, filled_avg_price: float = 0.0):
        self.id               = order_id
        self.symbol           = symbol
        self.qty              = str(qty)
        self.side             = side
        self.status           = status
        self.filled_avg_price = str(filled_avg_price)


class _Bar:
    """Single OHLCV bar — mirrors alpaca-py Bar."""
    def __init__(self, open: float, high: float, low: float, close: float, volume: float):
        self.open   = open
        self.high   = high
        self.low    = low
        self.close  = close
        self.volume = volume


class _BarSet:
    """Wraps a multi-symbol bar dict and provides a .df property."""
    def __init__(self, data: Dict[str, pd.DataFrame]):
        self._data = data

    @property
    def df(self) -> pd.DataFrame:
        if not self._data:
            return pd.DataFrame()
        frames = []
        for symbol, df in self._data.items():
            tmp = df.copy()
            tmp["symbol"] = symbol
            frames.append(tmp)
        combined = pd.concat(frames)
        combined = combined.reset_index().set_index(["symbol", "timestamp"])
        return combined

    def get(self, symbol: str) -> Optional[pd.DataFrame]:
        return self._data.get(symbol)


# ── Portfolio tracker ─────────────────────────────────────────────────────────

class _MockPortfolio:
    def __init__(self, starting_cash: float):
        self.cash: float = starting_cash
        self.positions: Dict[str, Dict[str, Any]] = {}   # symbol → {qty, avg_cost}
        self._total_value: float = starting_cash

    def total_value(self) -> float:
        return self._total_value


# ── Mock TradingClient ────────────────────────────────────────────────────────

class MockTradingClient:
    """
    Drops-in for alpaca.trading.client.TradingClient during backtesting.

    Used by backtest_harness.py — all methods return stub objects that mirror
    the shape expected by main_alpaca.py.
    """

    def __init__(self, starting_cash: float = 11_000.0):
        self.portfolio     = _MockPortfolio(starting_cash)
        self._prices: Dict[str, float] = {}   # latest close for each symbol
        self._orders: Dict[str, _Order] = {}
        self._order_seq    = 0
        self._starting_cash = starting_cash

    # ── prices (updated by harness) ───────────────────────────────────────────

    def update_price(self, symbol: str, price: float) -> None:
        self._prices[symbol] = price
        # Mark-to-market any open position
        pos = self.portfolio.positions.get(symbol)
        if pos and pos["qty"] > 0:
            self._recalculate_equity()

    def _recalculate_equity(self) -> None:
        total = self.portfolio.cash
        for sym, pos in self.portfolio.positions.items():
            qty = pos.get("qty", 0)
            px = self._prices.get(sym, pos.get("avg_cost", 0))
            total += qty * px
        self.portfolio._total_value = total

    # ── account ───────────────────────────────────────────────────────────────

    def get_account(self) -> _Account:
        self._recalculate_equity()
        equity       = self.portfolio._total_value
        buying_power = self.portfolio.cash * 2   # simplified; no margin modelling
        return _Account(
            cash            = round(self.portfolio.cash, 4),
            portfolio_value = round(equity, 4),
            buying_power    = round(buying_power, 4),
        )

    # ── positions ─────────────────────────────────────────────────────────────

    def get_all_positions(self) -> List[_Position]:
        result = []
        for sym, pos in self.portfolio.positions.items():
            qty = pos.get("qty", 0)
            if qty == 0:
                continue
            avg_cost      = pos.get("avg_cost", 0.0)
            current_price = self._prices.get(sym, avg_cost)
            market_value  = qty * current_price
            unrealized_pl = qty * (current_price - avg_cost)
            result.append(_Position(
                symbol          = sym,
                qty             = float(qty),
                avg_entry_price = avg_cost,
                current_price   = current_price,
                market_value    = market_value,
                unrealized_pl   = unrealized_pl,
            ))
        return result

    # ── orders ────────────────────────────────────────────────────────────────

    def submit_order(self, request) -> _Order:
        """Immediately fill at current price (backtest assumption)."""
        symbol = request.symbol
        qty    = float(str(request.qty))
        side   = str(request.side).lower()
        # Handle both enum and string
        if hasattr(side, "value"):
            side = side.value

        self._order_seq += 1
        order_id = f"mock-{self._order_seq:06d}"

        fill_price = self._prices.get(symbol, 0.0)
        if fill_price <= 0:
            fill_price = 100.0   # safe fallback

        # Simulate the fill
        if "buy" in side:
            pos = self.portfolio.positions.setdefault(symbol, {"qty": 0, "avg_cost": 0.0})
            prev_qty    = pos["qty"]
            if prev_qty < 0:
                # Buy-to-cover first; if crossing zero, remaining quantity becomes a new long.
                new_qty = prev_qty + qty
                if new_qty < 0:
                    pos["qty"] = new_qty
                elif new_qty == 0:
                    self.portfolio.positions.pop(symbol, None)
                else:
                    pos["qty"] = new_qty
                    pos["avg_cost"] = fill_price
            else:
                new_qty = prev_qty + qty
                if new_qty > 0:
                    pos["avg_cost"] = (prev_qty * pos["avg_cost"] + qty * fill_price) / new_qty
                pos["qty"] = new_qty
            self.portfolio.cash -= qty * fill_price
        elif "sell" in side:
            pos = self.portfolio.positions.setdefault(symbol, {"qty": 0, "avg_cost": 0.0})
            prev_qty = pos.get("qty", 0)
            if prev_qty > 0:
                new_qty = prev_qty - qty
                if new_qty > 0:
                    pos["qty"] = new_qty
                elif new_qty == 0:
                    self.portfolio.positions.pop(symbol, None)
                else:
                    # Sell through long -> remaining quantity opens a short.
                    pos["qty"] = new_qty
                    pos["avg_cost"] = fill_price
            else:
                # Extending / opening a short position.
                abs_prev = abs(prev_qty)
                new_qty = prev_qty - qty
                abs_new = abs(new_qty)
                if abs_new > 0:
                    pos["avg_cost"] = (
                        (abs_prev * pos.get("avg_cost", fill_price)) + (qty * fill_price)
                    ) / abs_new
                pos["qty"] = new_qty
            self.portfolio.cash += qty * fill_price

        self._recalculate_equity()

        order = _Order(
            order_id        = order_id,
            symbol          = symbol,
            qty             = qty,
            side            = side,
            status          = _OrderStatus.FILLED,
            filled_avg_price= fill_price,
        )
        self._orders[order_id] = order
        return order

    def get_order_by_id(self, order_id: str) -> _Order:
        return self._orders.get(order_id, _Order(order_id, "UNKNOWN", 0, "buy",
                                                  _OrderStatus.CANCELED))


# ── Mock StockHistoricalDataClient ────────────────────────────────────────────

class MockHistoricalDataClient:
    """
    Drops-in for alpaca.data.historical.StockHistoricalDataClient.

    The backtest harness pre-loads yfinance DataFrames via `load_data()`.
    All bar requests are answered from this in-memory store, sliced up to
    the harness's current simulation date.
    """

    def __init__(self):
        self._daily_data:  Dict[str, pd.DataFrame] = {}
        self._hourly_data: Dict[str, pd.DataFrame] = {}
        self._current_date: Optional[pd.Timestamp] = None

    def load_data(self, symbol: str, daily_df: pd.DataFrame,
                  hourly_df: Optional[pd.DataFrame] = None) -> None:
        """Called by harness to pre-populate data."""
        self._daily_data[symbol] = daily_df
        if hourly_df is not None:
            self._hourly_data[symbol] = hourly_df

    def set_current_date(self, ts: pd.Timestamp) -> None:
        self._current_date = ts

    def _slice(self, df: pd.DataFrame, start, end) -> pd.DataFrame:
        if df.empty:
            return df
        df = df.sort_index()
        mask = pd.Series(True, index=df.index)
        if start is not None:
            start_ts = pd.to_datetime(start)
            if start_ts.tzinfo is None:
                start_ts = start_ts.tz_localize("US/Eastern")
            mask &= df.index >= start_ts
        if end is not None:
            end_ts = pd.to_datetime(end)
            if end_ts.tzinfo is None:
                end_ts = end_ts.tz_localize("US/Eastern")
            if self._current_date is not None:
                end_ts = min(end_ts, self._current_date)
            mask &= df.index <= end_ts
        elif self._current_date is not None:
            mask &= df.index <= self._current_date
        return df[mask]

    def get_stock_bars(self, request) -> _BarSet:
        symbols = request.symbol_or_symbols
        if isinstance(symbols, str):
            symbols = [symbols]

        timeframe = request.timeframe
        start     = getattr(request, "start", None)
        end       = getattr(request, "end", None)

        # Detect daily vs hourly from TimeFrame
        is_daily = "day" in str(timeframe).lower() or "Day" in str(timeframe)
        store    = self._daily_data if is_daily else self._hourly_data

        result = {}
        for sym in symbols:
            df = store.get(sym, pd.DataFrame())
            if df.empty:
                continue
            sliced = self._slice(df, start, end)
            if sliced.empty:
                continue
            # Rename index to "timestamp" for BarSet.df compatibility
            out = sliced.copy()
            out.index.name = "timestamp"
            result[sym] = out

        return _BarSet(result)

    def get_stock_latest_bar(self, request) -> Dict[str, _Bar]:
        symbols = request.symbol_or_symbols
        if isinstance(symbols, str):
            symbols = [symbols]

        result = {}
        for sym in symbols:
            df = self._daily_data.get(sym, pd.DataFrame())
            if df.empty:
                continue
            sliced = self._slice(df, None, None)
            if sliced.empty:
                continue
            row = sliced.iloc[-1]
            result[sym] = _Bar(
                open   = float(row["open"]),
                high   = float(row["high"]),
                low    = float(row["low"]),
                close  = float(row["close"]),
                volume = float(row["volume"]),
            )
        return result

    def get_stock_latest_quote(self, request) -> Dict[str, Any]:
        """Stub — returns synthetic quote derived from latest bar."""
        symbols = request.symbol_or_symbols
        if isinstance(symbols, str):
            symbols = [symbols]
        result = {}
        for sym in symbols:
            bars = self.get_stock_latest_bar(
                type("R", (), {"symbol_or_symbols": [sym]})()
            )
            if sym in bars:
                px = bars[sym].close
                result[sym] = type("Q", (), {
                    "bid_price": px * 0.9999,
                    "ask_price": px * 1.0001,
                    "bid_size":  100,
                    "ask_size":  100,
                })()
        return result
