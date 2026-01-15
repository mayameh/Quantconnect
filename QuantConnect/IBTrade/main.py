# region imports
from AlgorithmImports import *
# endregion

class Ibtrade(QCAlgorithm):

    def initialize(self):
        # Locally Lean installs free sample data, to download more data please visit https://www.quantconnect.com/docs/v2/lean-cli/datasets/downloading-data
        self.set_start_date(2024, 1, 1)  # Set Start Date
        self.set_end_date(2024, 12, 24)  # Set End Date
        self.set_cash(100000)  # Set Strategy Cash
        apple = self.add_equity("AAPL", Resolution.DAILY)

        apple.set_data_normalization_mode(DataNormalizationMode.RAW)
        self.apple = apple.symbol
        self.set_benchmark("SPY")
        self.set_brokerage_model(BrokerageName.INTERACTIVE_BROKERS_BROKERAGE, AccountType.CASH)

        # Use SMA shortcut method
        self.sma_val = self.sma(self.apple, 30, Resolution.DAILY)
        closing_prices = self.history(self.apple, 30, Resolution.DAILY)["close"]
        

        # initialize helper variables
        self.entryPrice = 0
        self.period =  timedelta(31)
        self.nextEntryTime = self.time

    def on_data(self, data: Slice):
        """on_data event is the primary entry point for your algorithm. Each new data point will be pumped in here.
            Arguments:
                data: Slice object keyed by symbol containing the stock data
        """
        # Correct way: check that the symbol is in the data AND is not None
        if self.apple not in data or data[self.apple] is None:
            return  # No data for this time step

        price = data[self.apple].close

        if not self.portfolio.invested:
            if self.time >= self.nextEntryTime:
                self.set_holdings(self.apple, 1)
                # self.market_order(self.apple, int(self.portfolio.cash / price))
                self.log(f"Bought: {self.apple} at {str(price)}")
                self.entryPrice = price
            elif self.entryPrice * 1.1 < price or self.entryPrice * 0.9 > price:
                self.liquidate()
                self.log(f"Sold: {self.apple} at {str(price)}")
                self.nextEntryTime = self.time + self.period


