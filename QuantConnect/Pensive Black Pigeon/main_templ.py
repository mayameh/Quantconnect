from AlgorithmImports import *

class MultiIndicatorAlgorithm(QCAlgorithm):
    """Algorithm that trades Nividia with a multi–indicator rule‐set using
    MACD, Stochastic, ADX/Directional Indices, and CCI.
    It demonstrates the correct QuantConnect patterns for indicator
    creation, RollingWindow usage, scheduled trading, and logging.
    """

    def initialize(self) -> None:
        # 1. Basic configuration
        self.set_start_date(2023, 1, 1)
        self.set_end_date(2024, 1, 1)
        self.set_cash(100000)

        # 2. Universe
        equity = self.add_equity("NVDA", Resolution.DAILY)
        self._symbol = equity.symbol

        # 3. Indicators
        self._macd = self.macd(
            self._symbol, 12, 26, 9,
            MovingAverageType.EXPONENTIAL,
            Resolution.DAILY
        )
        self._stoch = self.sto(self._symbol, 14, 3, 3, Resolution.DAILY)
        self._adx   = self.adx(self._symbol, 14, Resolution.DAILY)
        self._cci   = self.cci(self._symbol, 20,
                               MovingAverageType.SIMPLE, Resolution.DAILY)

        # 4. RollingWindow containers (current bar + 1 bar ago)
        size = 2
        self._macd_window  = RollingWindow[float](size)
        self._signal_window = RollingWindow[float](size)
        self._sto_k_window = RollingWindow[float](size)
        self._sto_d_window = RollingWindow[float](size)
        self._adx_window   = RollingWindow[float](size)
        self._pdi_window   = RollingWindow[float](size)
        self._mdi_window   = RollingWindow[float](size)
        self._cci_window   = RollingWindow[float](size)

        # 5. Consolidator to push daily data into the windows
        consolidator = TradeBarConsolidator(timedelta(days=1))
        consolidator.data_consolidated += self._on_daily_bar
        self.subscription_manager.add_consolidator(self._symbol, consolidator)

        # 6. Scheduled trading logic
        self.schedule.on(
            self.date_rules.every_day(self._symbol),
            self.time_rules.after_market_open(self._symbol, 3),
            self.scan_and_trade
        )

        # 7. Warm-up period for indicators
        self.set_warm_up(timedelta(days=365))

    @staticmethod
    def _prev(window: RollingWindow[float], bars_back: int = 1):
        """Return the N-bars-ago value or None if unavailable."""
        return window[bars_back] if window.count > bars_back else None

    def _on_daily_bar(self, sender, bar: TradeBar) -> None:
        """Consolidator callback – updates all RollingWindows."""
        if self._macd.is_ready:
            self._macd_window.add(self._macd.current.value)
            self._signal_window.add(self._macd.signal.current.value)

        if self._stoch.is_ready:
            self._sto_k_window.add(self._stoch.stoch_k.current.value)
            self._sto_d_window.add(self._stoch.stoch_d.current.value)

        if self._adx.is_ready:
            self._adx_window.add(self._adx.current.value)
            self._pdi_window.add(
                self._adx.positive_directional_index.current.value
            )
            self._mdi_window.add(
                self._adx.negative_directional_index.current.value
            )

        if self._cci.is_ready:
            self._cci_window.add(self._cci.current.value)

    def scan_and_trade(self) -> None:
        """Evaluates signals and places trades."""
        if self.is_warming_up:
            return

        # Ensure every window needed is ready
        windows_ready = all([
            self._macd_window.is_ready, self._signal_window.is_ready,
            self._sto_k_window.is_ready, self._sto_d_window.is_ready,
            self._adx_window.is_ready,   self._pdi_window.is_ready,
            self._mdi_window.is_ready,   self._cci_window.is_ready
        ])
        if not windows_ready:
            return
        else:
            self.log("Window Ready. Executing logic....")

        
        macd_cur, macd_prev = self._macd_window[0], self._prev(self._macd_window)
        sig_cur,  sig_prev  = self._signal_window[0], self._prev(self._signal_window)
        sto_k     = self._sto_k_window[0]
        adx_val   = self._adx_window[0]
        pdi, mdi  = self._pdi_window[0], self._mdi_window[0]
        cci_val   = self._cci_window[0]

        # Entry conditions
        long_entry = (
            macd_prev is not None and sig_prev is not None and
            macd_prev < sig_prev and macd_cur > sig_cur and
            sto_k < 20 and pdi > mdi and cci_val < -100 and adx_val > 20
        )
        short_entry = (
            macd_prev is not None and sig_prev is not None and
            macd_prev > sig_prev and macd_cur < sig_cur and
            sto_k > 80 and pdi < mdi and cci_val > 100 and adx_val > 20
        )

        if long_entry:
           self.log(f"Buying..  & {self.symbol}")
        elif short_entry:
            self.log(f"Selling..  & {self.symbol}")
        
        holdings = self.portfolio[self._symbol]

        # Execute entries
        if long_entry and (not holdings.invested or holdings.quantity < 0):
            self.set_holdings(self._symbol, 1.0)
            self.debug(f"{self.time} LONG entry")
            return

        if short_entry and (not holdings.invested or holdings.quantity > 0):
            self.set_holdings(self._symbol, -1.0)
            self.debug(f"{self.time} SHORT entry")
            return

        # Exit rules – opposite MACD cross
        if holdings.invested:
            if holdings.quantity > 0 and macd_cur < sig_cur:
                self.liquidate(self._symbol)
                self.debug(f"{self.time} Exit LONG")
            elif holdings.quantity < 0 and macd_cur > sig_cur:
                self.liquidate(self._symbol)
                self.debug(f"{self.time} Exit SHORT")

    def on_data(self, data: Slice) -> None:
        # All trading decisions are handled in the scheduled scan_and_trade method.
        pass
