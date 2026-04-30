"""
Mock IB interface for backtesting.
Provides stub objects and state tracking for historical data replay.
"""
from datetime import datetime
from collections import namedtuple, defaultdict
import pandas as pd


# Stub contract and ticker data
Contract = namedtuple("Contract", ["symbol"])
Position = namedtuple("Position", ["contract", "position", "avgCost"])
OrderStatus = namedtuple("OrderStatus", ["status", "avgFillPrice"])
Trade = namedtuple("Trade", ["orderStatus"])


class MockTicker:
    """Mock ticker with OHLCV + streaming price."""
    def __init__(self, symbol):
        self.symbol = symbol
        self.close = 0.0
        self.bid = 0.0
        self.ask = 0.0
        self.last = 0.0
        self.volume = 0
        self.time = datetime.now()

    def marketPrice(self):
        """Return mid of bid/ask or last close."""
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2.0
        return self.last


class MockPortfolio:
    """Mock portfolio tracking positions and cash."""
    def __init__(self, starting_cash):
        self.cash = starting_cash
        self.positions: dict[str, dict] = {}  # ticker -> {qty, avg_cost}
        self._total_value = starting_cash

    def total_portfolio_value(self):
        """Current equity: cash + position values."""
        return self._total_value

    def __getitem__(self, symbol):
        """Return position-like object."""
        if symbol not in self.positions:
            return type('obj', (), {'invested': False, 'quantity': 0, 'average_price': 0})()
        pos = self.positions[symbol]
        return type('obj', (), {
            'invested': pos['qty'] != 0,
            'quantity': pos['qty'],
            'average_price': pos['avg_cost']
        })()

    def keys(self):
        return self.positions.keys()


class MockAccountValues:
    """Mock account values dict-like."""
    def __init__(self, starting_cash):
        self._values = {
            "NetLiquidation": starting_cash,
            "TotalCashValue": starting_cash,
        }

    def update_liquidation(self, value):
        self._values["NetLiquidation"] = value
        self._values["TotalCashValue"] = value

    def __getitem__(self, key):
        return self._values.get(key, 0.0)


class MockIB:
    """Mock IB Gateway for backtesting."""
    def __init__(self, starting_cash, current_time):
        self.starting_cash = starting_cash
        self.current_time = current_time
        self.tickers: dict[str, MockTicker] = {}
        self.portfolio = MockPortfolio(starting_cash)
        self.account_values = MockAccountValues(starting_cash)
        self.historical_data: dict[str, pd.DataFrame] = {}  # symbol -> OHLCV DataFrame
        self.order_id = 1
        self.filled_orders = []
        self._connected = True
        self.errorEvent = None

    def connect(self, *args, **kwargs):
        """Stub connect."""
        self._connected = True

    def isConnected(self):
        return self._connected

    def disconnect(self):
        self._connected = False

    def qualifyContracts(self, contract):
        """Stub qualify."""
        if contract.symbol not in self.tickers:
            self.tickers[contract.symbol] = MockTicker(contract.symbol)

    def reqMktData(self, contract, *args, **kwargs):
        """Stub market data subscription."""
        pass

    def ticker(self, contract):
        """Return current ticker snapshot."""
        if contract.symbol not in self.tickers:
            self.tickers[contract.symbol] = MockTicker(contract.symbol)
        return self.tickers[contract.symbol]

    def accountValues(self):
        """Return account values as list of objects."""
        values = []
        for tag, val in self.account_values._values.items():
            values.append(type('av', (), {'tag': tag, 'value': str(val), 'currency': 'USD'})())
        return values

    def positions(self):
        """Return list of Position namedtuples."""
        result = []
        for symbol, pos in self.portfolio.positions.items():
            if pos['qty'] != 0:
                contract = Contract(symbol=symbol)
                result.append(Position(contract=contract, position=pos['qty'], avgCost=pos['avg_cost']))
        return result

    def reqHistoricalData(self, contract, endDateTime="", durationStr="", barSizeSetting="", whatToShow="", useRTH=True):
        """Return historical bars (pre-loaded from backtest harness)."""
        return self.historical_data.get(contract.symbol, [])

    def placeOrder(self, contract, order):
        """Simulate order placement and fill."""
        symbol = contract.symbol
        qty = order.totalQuantity
        action = order.action  # "BUY" or "SELL"
        order_id = self.order_id
        self.order_id += 1

        # Get current price from ticker.
        ticker = self.ticker(contract)
        fill_price = ticker.marketPrice()
        if fill_price <= 0:
            fill_price = 100.0  # Fallback for testing

        # Simulate fill.
        if action == "BUY":
            if symbol not in self.portfolio.positions:
                self.portfolio.positions[symbol] = {'qty': 0, 'avg_cost': 0}
            pos = self.portfolio.positions[symbol]
            total_qty = pos['qty'] + qty
            pos['avg_cost'] = ((pos['qty'] * pos['avg_cost']) + (qty * fill_price)) / total_qty if total_qty > 0 else 0
            pos['qty'] = total_qty
            cost = qty * fill_price
            self.portfolio.cash -= cost
        elif action == "SELL":
            if symbol in self.portfolio.positions:
                pos = self.portfolio.positions[symbol]
                pos['qty'] -= qty
                proceeds = qty * fill_price
                self.portfolio.cash += proceeds

        self.filled_orders.append({
            'order_id': order_id,
            'symbol': symbol,
            'action': action,
            'qty': qty,
            'fill_price': fill_price,
            'time': self.current_time,
        })

        # Return mock trade with status.
        trade_obj = Trade(orderStatus=OrderStatus(status="Filled", avgFillPrice=fill_price))
        return trade_obj

    def sleep(self, seconds):
        """Stub sleep (no-op in backtest)."""
        pass

    def update_current_time(self, new_time):
        """Called by backtest harness to advance time."""
        self.current_time = new_time

    def update_ticker_price(self, symbol, close, bid, ask, volume):
        """Called by backtest harness to update streaming prices."""
        if symbol not in self.tickers:
            self.tickers[symbol] = MockTicker(symbol)
        ticker = self.tickers[symbol]
        ticker.close = close
        ticker.bid = bid
        ticker.ask = ask
        ticker.last = close
        ticker.volume = volume
        ticker.time = self.current_time

    def load_historical_data(self, symbol, df):
        """Load OHLCV DataFrame for a symbol."""
        self.historical_data[symbol] = df
