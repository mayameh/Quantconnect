from AlgorithmImports import *
from datetime import datetime, timedelta, time as dt_time  # Add 'time as dt_time'
from production_config import BOT_Config
from production_wrapper import RiskManager, OrderExecutor
from production_config import ProductionLogger, EmailAlerter, PerformanceTracker
from collections import defaultdict, deque
import json

'''
Algo enhanced to persist algo managed positions to be retrieved after restart
'''

class MayankAlgo_Production(QCAlgorithm):
    # ObjectStore keys for state persistence
    MANAGED_POSITIONS_KEY = "algo_managed_positions"
    ENTRY_TIMES_KEY = "entry_times"
    ENTRY_PRICES_KEY = "entry_prices"
    HIGHEST_PRICES_KEY = "highest_prices"

    def initialize(self) -> None:
        self.config = BOT_Config()
        self.logger = ProductionLogger(self.config)
        self.debug("Initializing Production Algorithm")
        
        self.email_alerter = EmailAlerter(self.config, self.logger)
        self.email_alerter.configure(
            smtp_server=self.config.email.smtp_server,
            smtp_port=self.config.email.smtp_port,
            email=self.config.email.sender_email,
            password=self.config.email.sender_password
        )
        
        self.perf_tracker = PerformanceTracker(self.logger)
        
        if self.config.general.mode == "LIVE":
            self.logger.critical("LIVE TRADING MODE ENABLED")
            self.set_brokerage_model(
                BrokerageName.INTERACTIVE_BROKERS_BROKERAGE, 
                AccountType.CASH
            )
        else:
            self.logger.info("PAPER TRADING MODE")
        
        self.set_start_date(2025, 4, 10)
        self.set_end_date(2025, 10, 28)
        self.set_cash(self.config.general.starting_capital)
        self._starting_cash = self.portfolio.cash
        
        self._core_symbols = []
        self._dynamic_symbols = set()
        self._all_symbols = set()
        
        # Load persisted state from ObjectStore (survives algo restarts)
        # Store as set of ticker strings for easier persistence
        if self.object_store.contains_key(self.MANAGED_POSITIONS_KEY):
            try:
                data = self.object_store.read(self.MANAGED_POSITIONS_KEY)
                saved_symbols = json.loads(data)
                self._algo_managed_positions = set(saved_symbols)
                self.logger.info(f"Restored {len(self._algo_managed_positions)} algo-managed positions from ObjectStore: {saved_symbols}")
            except Exception as e:
                self.logger.error(f"Failed to load managed positions: {e}")
                self._algo_managed_positions = set()
        else:
            self._algo_managed_positions = set()
        
        # For dictionaries, convert string keys back to symbols later after securities are added
        self._persisted_entry_times = {}
        self._persisted_entry_prices = {}
        self._persisted_highest_prices = {}
        
        if self.object_store.contains_key(self.ENTRY_TIMES_KEY):
            try:
                data = self.object_store.read(self.ENTRY_TIMES_KEY)
                self._persisted_entry_times = json.loads(data)
                self.logger.info(f"Restored entry times for {len(self._persisted_entry_times)} positions")
            except Exception as e:
                self.logger.error(f"Failed to load entry times: {e}")
        
        if self.object_store.contains_key(self.ENTRY_PRICES_KEY):
            try:
                data = self.object_store.read(self.ENTRY_PRICES_KEY)
                self._persisted_entry_prices = json.loads(data)
            except Exception as e:
                self.logger.error(f"Failed to load entry prices: {e}")
        
        if self.object_store.contains_key(self.HIGHEST_PRICES_KEY):
            try:
                data = self.object_store.read(self.HIGHEST_PRICES_KEY)
                self._persisted_highest_prices = json.loads(data)
            except Exception as e:
                self.logger.error(f"Failed to load highest prices: {e}")

        # Initialize empty dicts - will be populated from persisted data after symbols are added
        self.entry_time = {}
        self.entry_price = {}
        self.highest_price = {}        
        
        self._indicators = {}
        self._indicator_ready_time = {}
        # ✅ FIX: Start NEUTRAL to allow trading while regime indicators warm up
        self.market_regime = "NEUTRAL"
        self.weekly_trades_count = defaultdict(int)
        self.last_trade_date = None
        self._symbol_last_trade = {}
        self._last_eval_time = None
        self._last_universe_selection = None
        self.trade_history = deque(maxlen=50)
        self.winning_trades = deque(maxlen=30)
        self.losing_trades = deque(maxlen=20)
        self.symbol_performance = defaultdict(lambda: {'trades': 0, 'wins': 0, 'total_pnl': 0.0, 'consecutive_losses': 0, 'win_rate': 0.0})
        
        self.risk_manager = RiskManager(self.logger, self.config)
        self.order_executor = OrderExecutor(self.logger, self.config)
        self.peak_equity = self._starting_cash
        self.daily_start_time = self.time
        
        self.stop_loss_pct = self.config.trading.stop_loss_pct
        self.take_profit_pct = self.config.trading.take_profit_pct
        self.trailing_activation = self.config.trading.trailing_activation_pct
        self.max_positions = self.config.trading.max_positions
        
        for ticker in self.config.universe.core_symbols:
            try:
                eq = self.add_equity(ticker, Resolution.MINUTE, "USA")
                eq.set_leverage(1.0)
                self._core_symbols.append(eq.symbol)
                self._all_symbols.add(eq.symbol)
                self._create_indicators_for_symbol(eq.symbol)
                self.logger.info(f"Added CORE: {ticker}")
            except Exception as e:
                self.logger.error(f"Failed to add {ticker}: {e}")
        
        try:
            self.spy = self.add_equity("SPY", Resolution.MINUTE, "USA").symbol
            self.spy_ema_20 = self.ema(self.spy, 20, Resolution.DAILY)
            self.spy_ema_50 = self.ema(self.spy, 50, Resolution.DAILY)
            self.spy_rsi = self.rsi(self.spy, 14, MovingAverageType.EXPONENTIAL, Resolution.DAILY)
            self.logger.info("Added SPY regime tracker")
            # Warm SPY regime indicators so regime detection is ready immediately
            self._warm_up_spy_regime()
        except Exception as e:
            self.logger.error(f"Failed to add SPY: {e}")
        
        try:
            self.add_universe(lambda f: self._select_coarse(f), lambda f: self._select_fine(f))
            self.logger.info("Added dynamic universe")
        except Exception as e:
            self.logger.error(f"Failed to add universe: {e}")
        
        if self.live_mode:
            self.logger.critical("LIVE TRADING MODE - Market hours will be enforced")
        else:
            self.logger.info("BACKTESTING MODE - Market hours check disabled")
  
        # Use every_day with SPY symbol to ensure schedules only run on trading days
        self.schedule.on(self.date_rules.every_day(self.spy), self.time_rules.at(9, 25), self._detect_market_regime)

        self.schedule.on(self.date_rules.every_day(self.spy), self.time_rules.at(9, 30), self._evaluate_signals_safe)
        # Schedule portfolio summary emails
        self.schedule.on(self.date_rules.every_day(self.spy), self.time_rules.at(9, 35), self._send_portfolio_summary_email)

        # Re-check regime periodically during the day
        self.schedule.on(self.date_rules.every_day(self.spy), self.time_rules.at(12, 0), self._detect_market_regime)

        self.schedule.on(self.date_rules.every_day(self.spy), self.time_rules.at(12, 30), self._evaluate_signals_safe)
        # Schedule portfolio summary emails
        self.schedule.on(self.date_rules.every_day(self.spy), self.time_rules.at(12, 30), self._send_portfolio_summary_email)

        # Re-check regime periodically during the day
        self.schedule.on(self.date_rules.every_day(self.spy), self.time_rules.at(15, 0), self._detect_market_regime)

        self.schedule.on(self.date_rules.every_day(self.spy), self.time_rules.at(15, 30), self._evaluate_signals_safe)
        # Schedule portfolio summary emails
        self.schedule.on(self.date_rules.every_day(self.spy), self.time_rules.at(15, 30), self._send_portfolio_summary_email)

        self.schedule.on(self.date_rules.every_day(self.spy), self.time_rules.at(16, 0), self._daily_risk_summary)
        self.schedule.on(self.date_rules.every_day(self.spy), self.time_rules.at(16, 0), self._send_portfolio_summary_email)

        # Add weekly summary (optional)
        self.schedule.on(self.date_rules.every(DayOfWeek.FRIDAY), self.time_rules.at(16, 0), self._send_weekly_summary_email)

        # Option A: Use hour-level global warmup to avoid long daily delays
        self.set_warm_up(200, Resolution.HOUR)
        
        self.logger.info("=" * 60)
        self.logger.info("PRODUCTION ALGORITHM INITIALIZED")
        self.logger.info(f"Mode: {self.config.general.mode}")
        self.logger.info("=" * 60)
    
    def _is_market_open(self) -> bool:
        """Check if US market is currently open"""
        # try:
        #     # Check if any symbol's exchange is open
        #     for symbol in self._core_symbols:
        #         if self.securities[symbol].exchange.hours.is_open(self.time, False):
        #             return True
        #     return False
        # except Exception as e:
        #     self.logger.error(f"Market open check error: {e}")
        #     # Fallback: check time ranges (9:30 AM - 4:00 PM EST)
        #     current_time = self.time.time()
        #     market_open = dt_time(9, 30)
        #     market_close = dt_time(16, 0)
        #     is_weekday = self.time.weekday() < 5
        #     return is_weekday and market_open <= current_time <= market_close
        """FIXED: Check if US market is currently open - works in backtest AND live"""
        try:
            # ✅ Simple time-based check (works for both backtest and live)
            current_time = self.time.time()
            market_open = dt_time(9, 30)
            market_close = dt_time(16, 0)
            is_weekday = self.time.weekday() < 5  # Monday=0, Friday=4
            
            is_open = is_weekday and market_open <= current_time <= market_close
            
            # Only log in live mode to reduce backtest logs
            if not is_open and self.live_mode:
                self.logger.info(f"Market closed: weekday={is_weekday}, time={current_time}")
            
            return is_open
            
        except Exception as e:
            self.logger.error(f"Market check error: {e}")
            # SAFE DEFAULT: Assume market is open during error
            return True

    def _evaluate_signals_safe(self) -> None:
        try:
            # ✅ RECOMMENDED: Skip market check in backtesting
            if self.live_mode:
                # Only enforce market hours in live trading
                if not self._is_market_open():
                    self.debug("Market closed - skipping evaluation")
                    return

            if not self.risk_manager.can_trade():
                return
            
            current_equity = self.portfolio.total_portfolio_value
            daily_loss = self._starting_cash - current_equity
            
            if daily_loss > self.config.risk.max_daily_loss:
                self.logger.critical(f"DAILY LOSS LIMIT: ${daily_loss:,.2f}")
                self.risk_manager.emergency_stop("Daily loss limit exceeded")
                return
            
            if current_equity > self.peak_equity:
                self.peak_equity = current_equity
            
            drawdown = (self.peak_equity - current_equity) / self.peak_equity if self.peak_equity > 0 else 0
            if drawdown > self.config.risk.max_drawdown_pct:
                self.logger.critical(f"DRAWDOWN LIMIT: {drawdown:.1%}")
                self.risk_manager.emergency_stop(f"Drawdown: {drawdown:.1%}")
                return
            
            self._evaluate_signals()
        except Exception as e:
            self.logger.error(f"Signal evaluation error: {e}")
    
    def _daily_risk_summary(self) -> None:
        if not self._is_market_open():
            return  # Skip on weekends/holidays
        try:
            current_equity = self.portfolio.total_portfolio_value
            daily_pnl = current_equity - self._starting_cash
            daily_return = daily_pnl / self._starting_cash
            self.logger.info(f"Daily: ${current_equity:,.0f} ({daily_return:.2%})")
        except Exception as e:
            self.logger.error(f"Daily summary error: {e}")
    
    def _detect_market_regime(self) -> None:
        """
        Robust market regime detection.
        Regime = structural trend, not short-term momentum.
        """

        try:
            # Skip on weekends/holidays
            if not self._is_market_open():
                return
            # ─────────────────────────────────────────────
            # Guard: indicators must be ready
            # ─────────────────────────────────────────────
            if not (self.spy_ema_20.is_ready and self.spy_ema_50.is_ready):
                return

            spy_price = float(self.securities[self.spy].price)

            ema_20 = self.spy_ema_20.current.value
            ema_50 = self.spy_ema_50.current.value

            ema_20_prev = self.spy_ema_20.previous.value
            ema_50_prev = self.spy_ema_50.previous.value

            # RSI (optional confirmation only)
            rsi_ready = hasattr(self, "spy_rsi") and self.spy_rsi.is_ready
            rsi_val = self.spy_rsi.current.value if rsi_ready else None

            previous_regime = self.market_regime

            # ─────────────────────────────────────────────
            # Structural signals (regime)
            # ─────────────────────────────────────────────
            price_above_50 = spy_price > ema_50
            price_below_50 = spy_price < ema_50

            ema_structure_bull = ema_20 > ema_50
            ema_structure_bear = ema_20 < ema_50

            # ─────────────────────────────────────────────
            # Momentum signals (confirmation)
            # ─────────────────────────────────────────────
            ema_20_rising = ema_20 > ema_20_prev
            ema_50_rising = ema_50 > ema_50_prev

            momentum_bull = ema_20_rising and spy_price > ema_20
            momentum_bear = (not ema_20_rising) and spy_price < ema_20

            rsi_bull = rsi_ready and rsi_val > 55
            rsi_bear = rsi_ready and rsi_val < 45

            # ─────────────────────────────────────────────
            # REGIME DECISION LOGIC (with hysteresis)
            # ─────────────────────────────────────────────

            # ---- BULL REGIME ----
            if (
                price_above_50
                and ema_structure_bull
            ):
                self.market_regime = "BULL"

            # ---- BEAR REGIME ----
            elif (
                price_below_50
                and ema_structure_bear
                and momentum_bear
            ):
                self.market_regime = "BEAR"

            # ---- NEUTRAL (transition / chop) ----
            else:
                # Hysteresis: don't downgrade strong trends easily
                if previous_regime == "BULL" and price_above_50:
                    self.market_regime = "BULL"
                elif previous_regime == "BEAR" and price_below_50:
                    self.market_regime = "BEAR"
                else:
                    self.market_regime = "NEUTRAL"

            # ─────────────────────────────────────────────
            # LOGGING
            # ─────────────────────────────────────────────
            if previous_regime != self.market_regime:
                self.logger.info("=" * 70)
                self.logger.critical(
                    f"REGIME CHANGE: {previous_regime} → {self.market_regime}"
                )
                self.logger.info(
                    f"SPY: {spy_price:.2f} | EMA20: {ema_20:.2f} | EMA50: {ema_50:.2f}"
                )
                self.logger.info(
                    f"Struct: EMA20>EMA50={ema_structure_bull} | "
                    f"Momentum: EMA20 rising={ema_20_rising}"
                )
                if rsi_ready:
                    self.logger.info(f"RSI: {rsi_val:.1f}")
                self.logger.info("=" * 70)

            # Debug detail (safe from division-by-zero)
            self.debug("SPY Regime Diagnostics:")
            try:
                price_vs_50 = ((spy_price - ema_50) / ema_50 * 100) if abs(ema_50) > 1e-9 else None
            except Exception:
                price_vs_50 = None
            try:
                ema20_slope = ((ema_20 - ema_20_prev) / ema_20_prev * 100) if abs(ema_20_prev) > 1e-9 else None
            except Exception:
                ema20_slope = None
            try:
                ema20_vs_50 = ((ema_20 - ema_50) / ema_50 * 100) if abs(ema_50) > 1e-9 else None
            except Exception:
                ema20_vs_50 = None

            self.debug(f"  Price vs EMA50: {price_vs_50:+.2f}%" if price_vs_50 is not None else "  Price vs EMA50: n/a")
            self.debug(f"  EMA20 slope: {ema20_slope:+.2f}%" if ema20_slope is not None else "  EMA20 slope: n/a")
            self.debug(f"  EMA20 vs EMA50: {ema20_vs_50:+.2f}%" if ema20_vs_50 is not None else "  EMA20 vs EMA50: n/a")

        except Exception as e:
            self.logger.error(f"Regime detection error: {e}")
            self.logger.warning(f"Keeping existing regime: {self.market_regime}")

    def _select_coarse(self, coarse) -> list:
        if self._last_universe_selection is not None:
            days_since = (self.time.date() - self._last_universe_selection.date()).days
            if days_since < 14:
                return []
        # Filter: Only stocks (common equities), exclude ALL ETFs
        filtered = [x for x in coarse if x.has_fundamental_data 
                    and x.price >= 50.0 
                    and x.dollar_volume >= 100000000 
                    and x.volume > 0
                    and not x.symbol.value.startswith('SPY')  # Exclude SPY family
                    and not x.symbol.value.startswith('QQQ')  # Exclude QQQ family
                    and not x.symbol.value.startswith('IWM')  # Exclude IWM family
                    and not x.symbol.value.startswith('DIA')  # Exclude DIA family
                    and not x.symbol.value.startswith('VTI')  # Exclude Vanguard ETFs
                    and not x.symbol.value.startswith('VOO')  # Exclude Vanguard ETFs
                    and not x.symbol.value.startswith('XL')]  # Exclude sector ETFs
        sorted_by_volume = sorted(filtered, key=lambda x: x.dollar_volume, reverse=True)
        top_symbols = [x.symbol for x in sorted_by_volume[:30]]
        return list(set(self._core_symbols + top_symbols))
    
    def _select_fine(self, fine) -> list:
        # Filter: Only operating companies with real fundamentals (excludes ETFs, REITs, etc.)
        quality_stocks = [f for f in fine 
                         if f.asset_classification.morningstar_sector_code != MorningstarSectorCode.FINANCIAL_SERVICES 
                         and f.valuation_ratios.pe_ratio > 0 
                         and f.valuation_ratios.pe_ratio < 100
                         and f.asset_classification.morningstar_industry_group_code > 0]  # Has industry classification (stocks have this, ETFs don't)
        momentum_scores = []
        for stock in quality_stocks:
            try:
                if stock.symbol in self._core_symbols:
                    continue
                price_momentum = stock.valuation_ratios.price_change_1m or 0
                revenue_growth = stock.operation_ratios.revenue_growth.one_year or 0
                score = (price_momentum * 0.6 + revenue_growth * 0.4)
                momentum_scores.append((stock.symbol, score))
            except:
                continue
        momentum_scores.sort(key=lambda x: x[1], reverse=True)
        selected = [s for s, _ in momentum_scores[:10]]

        # Exclude ACN (Accenture) - cannot be traded
        selected = [s for s in selected if s.value != "ACN"]

        self._last_universe_selection = self.time
        return list(set(self._core_symbols + selected))
    
    def on_securities_changed(self, changes) -> None:
        for security in changes.added_securities:
            symbol = security.symbol
            # Skip SPY - it's only for regime tracking
            if symbol == self.spy:
                continue
            if symbol not in self._all_symbols:
                self._dynamic_symbols.add(symbol)
                self._all_symbols.add(symbol)
                self._create_indicators_for_symbol(symbol)
                # Ensure indicators for newly added symbols are warmed immediately
                self._warm_up_symbol_indicators(symbol, Resolution.HOUR)
        for security in changes.removed_securities:
            symbol = security.symbol
            if symbol not in self._core_symbols and not self.portfolio[symbol].invested:
                self._dynamic_symbols.discard(symbol)
                self._all_symbols.discard(symbol)
    
    def _create_indicators_for_symbol(self, symbol) -> None:
        try:
            # Option A: Revert to HOUR resolution for faster, timely signals
            self._indicators[symbol] = {
                'macd': self.macd(symbol, 12, 26, 9, MovingAverageType.EXPONENTIAL, Resolution.HOUR),
                'rsi': self.rsi(symbol, 14, MovingAverageType.EXPONENTIAL, Resolution.HOUR),
                'ema_50': self.ema(symbol, 50, Resolution.HOUR)
            }
            # Warm newly created indicators using history so they're ready immediately
            self._warm_up_symbol_indicators(symbol, Resolution.HOUR)
        except Exception as e:
            self.logger.error(f"Indicator error {symbol}: {e}")
    
    def _is_symbol_ready(self, symbol) -> bool:
        indicators = self._indicators.get(symbol)
        if not indicators:
            return False
        macd = indicators.get("macd")
        rsi = indicators.get("rsi")
        ema_50 = indicators.get("ema_50")
        return all([macd and macd.is_ready, rsi and rsi.is_ready, ema_50 and ema_50.is_ready])

    def _warm_up_symbol_indicators(self, symbol, resolution: Resolution = Resolution.HOUR) -> None:
        """Warm per-symbol indicators using historical data so they are ready immediately.

        Uses QuantConnect's WarmUpIndicator if available; falls back gracefully.
        """
        try:
            indicators = self._indicators.get(symbol)
            if not indicators:
                return

            for key in ["ema_50", "rsi", "macd"]:
                indicator = indicators.get(key)
                if not indicator:
                    continue
                try:
                    # Prefer pythonic snake_case if available, else use C#-style PascalCase
                    self.warm_up_indicator(symbol, indicator, resolution)
                except Exception as ie:
                    self.logger.warning(f"WarmUpIndicator failed for {symbol.value} ({key}): {ie}")
        except Exception as e:
            self.logger.error(f"Symbol warmup error {symbol.value}: {e}")

    def _warm_up_spy_regime(self) -> None:
        """Warm SPY regime indicators (EMA20/EMA50/RSI) using daily data."""
        try:
            for ind, name in [
                (getattr(self, "spy_ema_20", None), "EMA20"),
                (getattr(self, "spy_ema_50", None), "EMA50"),
                (getattr(self, "spy_rsi", None), "RSI"),
            ]:
                if not ind:
                    continue
                try:
                    self.warm_up_indicator(self.spy, ind, Resolution.DAILY)
                except Exception as ie:
                    self.logger.warning(f"SPY warmup failed for {name}: {ie}")
        except Exception as e:
            self.logger.error(f"SPY regime warmup error: {e}")
    
    def _calculate_position_size(self, symbol, current_price) -> int:
        try:
            available_cash = self.portfolio.cash
            safe_available = available_cash * 0.75
            target_value = min(safe_available * 0.85, 6500)
            if target_value < 4000:
                return 0
            # Account for estimated commissions (Interactive Brokers style)
            qty = int(target_value / current_price)
            if qty <= 0:
                return 0

            fee = self._estimate_order_fee(symbol, qty, current_price)
            total_cost = qty * current_price + fee

            # If fees push us over the target_value, scale down once using the fee-adjusted budget
            if total_cost > target_value:
                qty = int((target_value - fee) / current_price)
                fee = self._estimate_order_fee(symbol, qty, current_price)
                total_cost = qty * current_price + fee

            return qty if qty > 0 and total_cost >= 4000 else 0
        except Exception as e:
            self.logger.error(f"Position sizing error: {e}")
            return 0

    def _estimate_order_fee(self, symbol, quantity: int, price: float) -> float:
        """Rough Interactive Brokers-style fee model for sizing calculations.

        Uses per-share pricing with min and max caps to avoid oversizing when cash is tight.
        This is only for pre-trade sizing; actual fills will use the brokerage fee model.
        """
        try:
            if quantity <= 0 or price <= 0:
                return 0.0

            trade_value = abs(quantity) * price
            per_share_fee = 0.0035  # IB tiered typical US stock per-share fee
            min_fee = 0.35          # Minimum per order
            max_pct = 0.01          # Cap at 1% of trade value

            fee = per_share_fee * abs(quantity)
            fee = max(fee, min_fee)
            fee = min(fee, trade_value * max_pct)
            return float(fee)
        except Exception:
            return 0.0
    
    def _evaluate_signals(self) -> None:
        if self.is_warming_up:
            self.debug(f"WARMING UP: {self.portfolio.cash}")
            return
        
        # Get all invested positions (exclude SPY - it's only for regime tracking)
        all_invested = [s for s in self.portfolio.keys() if self.portfolio[s].invested and s != self.spy]
        
        # Get ONLY algorithm-managed positions for counting
        algo_invested = [s for s in all_invested if s in self._algo_managed_positions]
        
        # EXIT LOGIC - Only manage algorithmic positions
        for symbol in algo_invested:  # Changed from all_invested to algo_invested
            try:
                if not self.portfolio[symbol].invested:
                    continue
                current_price = float(self.securities[symbol].price)
                if current_price <= 0:
                    continue
                avg_entry = self.portfolio[symbol].average_price
                qty = abs(self.portfolio[symbol].quantity)
                pnl_pct = (current_price - avg_entry) / avg_entry
                pnl_dollar = qty * (current_price - avg_entry)
                held_time = self.time - self.entry_time.get(symbol, self.time)
                
                if symbol not in self.highest_price:
                    self.highest_price[symbol] = current_price
                else:
                    self.highest_price[symbol] = max(self.highest_price[symbol], current_price)
                
                should_exit = False
                reason = ""
                self.debug(f"CHECK {symbol.value}: pnl={pnl_pct:.4f}")
                if pnl_pct <= -0.03:
                    should_exit, reason = True, "stop_loss"
                elif pnl_pct >= 0.08:
                    self.debug(f"TAKE PROFIT TRIGGERED: {symbol.value}")
                    should_exit, reason = True, "take_profit"
                elif held_time >= timedelta(hours=30) and pnl_pct >= 0.018:
                    should_exit, reason = True, "profit_lock"
                elif held_time >= timedelta(days=14):
                    should_exit, reason = True, "time_exit"
            
                # ✅ ADD GAP DOWN PROTECTION
                if pnl_pct <= -0.05:  # If loss exceeds 5% (should never happen with 3% stop)
                    self.logger.critical(f"GAP DOWN DETECTED: {symbol.value} at {pnl_pct:.2%}")
                    should_exit, reason = True, "gap_protection"
            
                if should_exit:
                    # Always exit if it's algorithm-managed
                    self.liquidate(symbol, tag=reason)

                    pnl_dollar = qty * (current_price - avg_entry)
                    trade_exit = f"{self.time.strftime('%Y-%m-%d %H:%M')} SELL {symbol.value} - Qty: {qty} @ ${current_price:.2f} | P&L: ${pnl_dollar:.2f}"
                    self.trade_history.append(trade_exit)

                    self.debug(f"EXIT {symbol.value}: {reason}, P&L ${pnl_dollar:.0f}")
                    self._algo_managed_positions.discard(symbol.value)
                    
                    # Clean up position metadata
                    self.entry_time.pop(symbol, None)
                    self.entry_price.pop(symbol, None)
                    self.highest_price.pop(symbol, None)
                    
                    # Persist updated state
                    self._save_state_to_object_store()
            except Exception as e:
                self.logger.error(f"Exit error: {e}")
        
        # ENTRY LOGIC - Count only algorithm-managed positions
        current_equity = self.portfolio.total_portfolio_value
        portfolio_return = (current_equity - self._starting_cash) / self._starting_cash

        # Adjust max positions based on performance and regime
        if self.market_regime == "BEAR":
            self.debug(f"SKIP ENTRY: BEAR market regime")
            return
            
        # Dynamic position sizing based on regime and performance
        if self.market_regime == "BULL" and portfolio_return > 0.02:
            max_allowed = self.max_positions  # Full capacity when doing well in bull market
        elif self.market_regime == "BULL":
            max_allowed = min(3, self.max_positions)  # Reduced in bull but underperforming
        elif self.market_regime == "NEUTRAL":
            max_allowed = min(2, self.max_positions)  # Conservative in neutral
        else:
            max_allowed = 1  # Very conservative otherwise

        # COUNT ONLY ALGORITHM-MANAGED POSITIONS
        algo_position_count = len(algo_invested)
        
        if algo_position_count >= max_allowed:
            self.debug(f"SKIP ENTRY: Max algo positions for {self.market_regime} regime ({algo_position_count}/{max_allowed})")
            self.debug(f"Current positions - Total: {len(all_invested)}, Algo: {algo_position_count}, Manual: {len(all_invested) - algo_position_count}")
            return
            
        if self.portfolio.cash < 4000:
            self.debug(f"SKIP ENTRY: insufficient cash ${self.portfolio.cash:.0f}")
            return

        candidates = []
        self.debug(f"SCANNING {len(self._all_symbols)} symbols for entry")
        not_ready_symbols = []
        for symbol in self._all_symbols:
            try:
                # Skip SPY - it's only for regime tracking
                if symbol == self.spy:
                    continue
                if self.portfolio[symbol].invested:
                    continue

                # Track names of symbols whose indicators aren't ready
                if not self._is_symbol_ready(symbol):
                    not_ready_symbols.append(symbol.value)
                    continue
                indicators = self._indicators.get(symbol)
                if not indicators:
                    continue
                macd = indicators["macd"]
                rsi = indicators["rsi"]
                ema_50 = indicators["ema_50"]
                current_price = float(self.securities[symbol].price)
                if current_price <= 0:
                    continue
                macd_val = macd.current.value
                macd_sig = macd.signal.current.value
                rsi_val = rsi.current.value
                ema_val = ema_50.current.value
                if macd_val > macd_sig and 45 < rsi_val < 65 and current_price > ema_val:
                    score = (macd_val - macd_sig) * (rsi_val / 100)
                    candidates.append((symbol, score))
            except Exception as e:
                pass
        
        # Log which symbols weren't ready (sample up to 3 for brevity)
        if not_ready_symbols:
            sample = ", ".join(not_ready_symbols[:3])
            self.debug(f"Not ready: {sample} (total {len(not_ready_symbols)})")
        
        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            best_symbol, _ = candidates[0]
            current_price = float(self.securities[best_symbol].price)
            qty = self._calculate_position_size(best_symbol, current_price)
            if qty > 0:
                ticket = self.market_order(best_symbol, qty)
                
                trade_entry = f"{self.time.strftime('%Y-%m-%d %H:%M')} BUY {best_symbol.value} - Qty: {qty} @ ${current_price:.2f}"
                self.trade_history.append(trade_entry)
                
                if ticket:
                    self._algo_managed_positions.add(best_symbol)
                    self.entry_time[best_symbol] = self.time
                    self.entry_price[best_symbol] = current_price
                    self.highest_price[best_symbol] = current_price
                    self.debug(f"BUY {best_symbol.value}: qty={qty}, ${current_price:.2f}")

                    # Persist state to ObjectStore
                    self._save_state_to_object_store()
    
    # Add this debug info in _evaluate_signals where needed:
    def _log_position_status(self):
        """Log current position status for debugging"""
        stats = self._get_position_stats()
        self.debug(f"POSITIONS: Total={stats['total_positions']}, Algo={stats['algo_positions']}/{stats['max_algo_allowed']}, Manual={stats['manual_positions']}")
        if stats['algo_symbols']:
            self.debug(f"ALGO: {', '.join(stats['algo_symbols'])}")
        if stats['manual_symbols']:
            self.debug(f"MANUAL: {', '.join(stats['manual_symbols'])}")

    def _send_portfolio_summary_email(self) -> None:
        """Send comprehensive portfolio summary via email"""
        if self.is_warming_up:
            return  # Silent return during warmup
        if not self._is_market_open():
            return  # Skip on weekends/holidays
        
        try:
            # Portfolio Overview
            current_equity = self.portfolio.total_portfolio_value
            total_return = (current_equity - self._starting_cash) / self._starting_cash
            daily_pnl = current_equity - self._starting_cash
            
            # Positions Summary
            positions_summary = []
            for symbol, holding in self.portfolio.items():
                if holding.invested:
                    current_price = float(self.securities[symbol].price)
                    avg_entry = holding.average_price
                    qty = abs(holding.quantity)
                    pnl = qty * (current_price - avg_entry)
                    pnl_pct = (current_price - avg_entry) / avg_entry
                    time_held = self.time - self.entry_time.get(symbol, self.time)
                    
                    positions_summary.append({
                        'symbol': symbol.value,
                        'qty': qty,
                        'entry_price': avg_entry,
                        'current_price': current_price,
                        'pnl': pnl,
                        'pnl_pct': pnl_pct,
                        'time_held': str(time_held),
                        'value': qty * current_price
                    })
            
            # Recent Trades (from trade_history deque)
            recent_trades_summary = list(self.trade_history)[-10:]  # Last 10 trades
            
            # Build email body
            email_body = self._format_summary_email(
                current_equity=current_equity,
                total_return=total_return,
                daily_pnl=daily_pnl,
                cash_position=self.portfolio.cash,
                market_regime=self.market_regime,
                positions=positions_summary,
                recent_trades=recent_trades_summary,
                symbols_evaluated=len(self._all_symbols)
            )
            
            # Send email
            subject = f"Portfolio Summary - {self.time.strftime('%Y-%m-%d %H:%M')}"
#            self.email_alerter.send_email(subject, email_body)
            self.logger.info(f"Summary email sent at {self.time}")
            
        except Exception as e:
            self.logger.error(f"Email summary error: {e}")

    def _get_position_stats(self) -> dict:
        """Get detailed position statistics"""
        all_positions = [s for s in self.portfolio.keys() if self.portfolio[s].invested]
        algo_positions = [s for s in all_positions if s in self._algo_managed_positions]
        manual_positions = [s for s in all_positions if s not in self._algo_managed_positions]
        
        return {
            'total_positions': len(all_positions),
            'algo_positions': len(algo_positions),
            'manual_positions': len(manual_positions),
            'algo_symbols': [s.value for s in algo_positions],
            'manual_symbols': [s.value for s in manual_positions],
            'max_algo_allowed': self.max_positions
        }

    def _format_summary_email(self, current_equity, total_return, daily_pnl, cash_position, 
                            market_regime, positions, recent_trades, symbols_evaluated) -> str:
        """Format comprehensive summary email"""
        email = "=" * 70 + "\n"
        email += "PORTFOLIO SUMMARY\n"
        email += "=" * 70 + "\n\n"
        
        # Portfolio Overview
        email += "PORTFOLIO OVERVIEW\n"
        email += "-" * 70 + "\n"
        email += f"Current Equity:    ${current_equity:,.2f}\n"
        email += f"Total Return:      {total_return:.2%}\n"
        email += f"Daily P&L:         ${daily_pnl:,.2f}\n"
        email += f"Cash Position:     ${cash_position:,.2f}\n\n"
        
        # Market Regime
        email += "MARKET REGIME\n"
        email += "-" * 70 + "\n"
        email += f"Regime Status:     {self.market_regime}\n"
        email += f"Symbols Scanned:   {symbols_evaluated}\n"
        
        # Position counts
        algo_positions = len([s for s in self.portfolio.keys() if self.portfolio[s].invested and s in self._algo_managed_positions])
        manual_positions = len([s for s in self.portfolio.keys() if self.portfolio[s].invested and s not in self._algo_managed_positions])
        email += f"Algo Positions:    {algo_positions}/{self.max_positions}\n"
        email += f"Manual Positions:  {manual_positions}\n\n"
        
        # Current Positions - Enhanced to show type
        email += "OPEN POSITIONS\n"
        email += "-" * 70 + "\n"
        if positions:
            for pos in positions:
                position_type = "ALGO" if any(s.value == pos['symbol'] and s in self._algo_managed_positions for s in self.portfolio.keys()) else "MANUAL"
                email += f"{pos['symbol']:<8} | "
                email += f"Qty: {pos['qty']:<6} | "
                email += f"Entry: ${pos['entry_price']:<8.2f} | "
                email += f"Current: ${pos['current_price']:<8.2f} | "
                email += f"P&L: ${pos['pnl']:<10.2f} ({pos['pnl_pct']:>6.2%}) | "
                email += f"Type: {position_type:<6} | "
                email += f"Time: {pos['time_held']}\n"
        else:
            email += "No open positions\n"
        email += "\n"
        
        # Recent Trades
        email += "RECENT TRADES (Last 10)\n"
        email += "-" * 70 + "\n"
        if recent_trades:
            for trade in recent_trades:
                email += f"{trade}\n"
        else:
            email += "No recent trades\n"
        email += "\n"
        
        email += "=" * 70 + "\n"
        email += f"Report Generated: {self.time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        
        return email
    
    def _format_weekly_summary(self) -> str:
        """Format comprehensive weekly summary with performance analytics"""
        try:
            current_equity = self.portfolio.total_portfolio_value
            total_return = (current_equity - self._starting_cash) / self._starting_cash
            weekly_pnl = current_equity - self._starting_cash
            
            # Calculate weekly performance metrics
            winning_trade_count = len(self.winning_trades)
            losing_trade_count = len(self.losing_trades)
            total_trades = winning_trade_count + losing_trade_count
            win_rate = (winning_trade_count / total_trades * 100) if total_trades > 0 else 0
            
            # Symbol performance analysis
            top_performers = sorted(
                [(symbol, data) for symbol, data in self.symbol_performance.items() 
                if data['trades'] > 0],
                key=lambda x: x[1]['total_pnl'], 
                reverse=True
            )[:5]
            
            worst_performers = sorted(
                [(symbol, data) for symbol, data in self.symbol_performance.items() 
                if data['trades'] > 0],
                key=lambda x: x[1]['total_pnl']
            )[:5]
            
            # Build comprehensive weekly email
            email = "=" * 80 + "\n"
            email += "WEEKLY PORTFOLIO ANALYSIS\n"
            email += "=" * 80 + "\n\n"
            
            # Portfolio Performance
            email += "PORTFOLIO PERFORMANCE\n"
            email += "-" * 80 + "\n"
            email += f"Current Equity:        ${current_equity:,.2f}\n"
            email += f"Starting Capital:      ${self._starting_cash:,.2f}\n"
            email += f"Total Return:          {total_return:.2%}\n"
            email += f"Weekly P&L:            ${weekly_pnl:,.2f}\n"
            email += f"Cash Available:        ${self.portfolio.cash:,.2f}\n"
            email += f"Peak Equity:           ${self.peak_equity:,.2f}\n"
            
            # Calculate drawdown
            current_drawdown = (self.peak_equity - current_equity) / self.peak_equity if self.peak_equity > 0 else 0
            email += f"Current Drawdown:      {current_drawdown:.2%}\n\n"
            
            # Trading Statistics  
            email += "TRADING STATISTICS\n"
            email += "-" * 80 + "\n"
            email += f"Total Trades:          {total_trades}\n"
            email += f"Winning Trades:        {winning_trade_count}\n"
            email += f"Losing Trades:         {losing_trade_count}\n"
            email += f"Win Rate:              {win_rate:.1f}%\n"
            email += f"Market Regime:         {self.market_regime}\n"
            email += f"Symbols in Universe:   {len(self._all_symbols)}\n"
            email += f"Active Positions:      {len([s for s in self.portfolio.keys() if self.portfolio[s].invested])}\n\n"
            
            # Current Holdings Detail
            email += "CURRENT HOLDINGS\n"
            email += "-" * 80 + "\n"
            active_positions = []
            total_position_value = 0
            
            for symbol, holding in self.portfolio.items():
                if holding.invested:
                    current_price = float(self.securities[symbol].price)
                    avg_entry = holding.average_price
                    qty = abs(holding.quantity)
                    position_value = qty * current_price
                    pnl = qty * (current_price - avg_entry)
                    pnl_pct = (current_price - avg_entry) / avg_entry
                    time_held = self.time - self.entry_time.get(symbol, self.time)
                    
                    active_positions.append({
                        'symbol': symbol.value,
                        'qty': qty,
                        'entry_price': avg_entry,
                        'current_price': current_price,
                        'position_value': position_value,
                        'pnl': pnl,
                        'pnl_pct': pnl_pct,
                        'time_held': str(time_held).split('.')[0],  # Remove microseconds
                        'allocation': position_value / current_equity * 100
                    })
                    total_position_value += position_value
            
            if active_positions:
                email += f"{'Symbol':<8} | {'Qty':<6} | {'Entry':<10} | {'Current':<10} | {'Value':<12} | {'P&L':<12} | {'%':<8} | {'Alloc%':<7} | {'Time Held'}\n"
                email += "-" * 80 + "\n"
                
                for pos in sorted(active_positions, key=lambda x: x['pnl'], reverse=True):
                    email += f"{pos['symbol']:<8} | "
                    email += f"{pos['qty']:<6} | "
                    email += f"${pos['entry_price']:<9.2f} | "
                    email += f"${pos['current_price']:<9.2f} | "
                    email += f"${pos['position_value']:<11,.0f} | "
                    email += f"${pos['pnl']:<11,.0f} | "
                    email += f"{pos['pnl_pct']:<7.2%} | "
                    email += f"{pos['allocation']:<6.1f}% | "
                    email += f"{pos['time_held']}\n"
                    
                email += f"\nTotal Position Value: ${total_position_value:,.2f} ({total_position_value/current_equity*100:.1f}% of portfolio)\n\n"
            else:
                email += "No current holdings\n\n"
            
            # Top Performing Symbols
            email += "TOP PERFORMING SYMBOLS\n"
            email += "-" * 80 + "\n"
            if top_performers:
                email += f"{'Symbol':<8} | {'Trades':<6} | {'Win Rate':<8} | {'Total P&L':<12} | {'Avg P&L':<10}\n"
                email += "-" * 80 + "\n"
                for symbol, perf in top_performers:
                    avg_pnl = perf['total_pnl'] / perf['trades'] if perf['trades'] > 0 else 0
                    email += f"{symbol.value:<8} | {perf['trades']:<6} | {perf['win_rate']:<7.1f}% | ${perf['total_pnl']:<11,.0f} | ${avg_pnl:<9,.0f}\n"
            else:
                email += "No performance data available\n"
            email += "\n"
            
            # Worst Performing Symbols (if any)
            if worst_performers and len(worst_performers) > 0:
                email += "WORST PERFORMING SYMBOLS\n"
                email += "-" * 80 + "\n"
                email += f"{'Symbol':<8} | {'Trades':<6} | {'Win Rate':<8} | {'Total P&L':<12} | {'Consec Loss':<11}\n"
                email += "-" * 80 + "\n"
                for symbol, perf in worst_performers[:3]:  # Just top 3 worst
                    email += f"{symbol.value:<8} | {perf['trades']:<6} | {perf['win_rate']:<7.1f}% | ${perf['total_pnl']:<11,.0f} | {perf['consecutive_losses']:<11}\n"
                email += "\n"
            
            # Recent Trade History (Last 15 trades)
            email += "RECENT TRADE HISTORY (Last 15)\n"
            email += "-" * 80 + "\n"
            if self.trade_history:
                for trade in list(self.trade_history)[-15:]:
                    email += f"{trade}\n"
            else:
                email += "No recent trades\n"
            email += "\n"
            
            # Risk Metrics
            email += "RISK METRICS\n"
            email += "-" * 80 + "\n"
            email += f"Maximum Drawdown:      {current_drawdown:.2%}\n"
            email += f"Cash Allocation:       {(self.portfolio.cash / current_equity * 100):.1f}%\n"
            email += f"Equity Allocation:     {(total_position_value / current_equity * 100):.1f}%\n"
            email += f"Risk Per Trade:        ~{(self.config.trading.stop_loss_pct * 100):.1f}%\n"
            email += f"Max Positions:         {self.max_positions}\n\n"
            
            # Weekly Goals & Notes
            email += "WEEKLY PERFORMANCE NOTES\n"
            email += "-" * 80 + "\n"
            
            if total_return > 0.05:
                email += "Strong weekly performance! Portfolio significantly above target.\n"
            elif total_return > 0.02:
                email += "Good weekly performance, on track with targets.\n"
            elif total_return > -0.02:
                email += "Neutral weekly performance, within acceptable range.\n"
            else:
                email += "Poor weekly performance, review risk management.\n"
                
            if win_rate > 70:
                email += "Excellent win rate, strategy performing well.\n"
            elif win_rate > 50:
                email += "Acceptable win rate, monitor for consistency.\n"
            else:
                email += "Low win rate, consider strategy adjustments.\n"
                
            if current_drawdown > 0.05:
                email += "WARNING: High drawdown detected, consider position sizing review.\n"
                
            email += "\n" + "=" * 80 + "\n"
            email += f"Weekly Report Generated: {self.time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            email += "=" * 80 + "\n"
            
            return email
            
        except Exception as e:
            self.logger.error(f"Weekly summary formatting error: {e}")
            return f"Error generating weekly summary: {e}"


    # Keep daily summaries but create a different weekly method
    def _send_weekly_summary_email(self) -> None:
        """Send comprehensive weekly portfolio summary"""
        if self.is_warming_up:
            return  # Silent return during warmup
        
        try:
            # More detailed weekly analysis
            email_body = self._format_weekly_summary()
            subject = f"Weekly Portfolio Summary - {self.time.strftime('%Y-%m-%d')}"
#            self.email_alerter.send_email(subject, email_body)
            self.logger.info(f"Weekly summary email sent")
        except Exception as e:
            self.logger.error(f"Weekly email error: {e}")


    def _save_state_to_object_store(self) -> None:
        """Save trading state to ObjectStore for persistence across restarts"""
        try:
            # Save algo-managed positions as list of ticker strings
            positions_list = [str(symbol.value) if hasattr(symbol, 'value') else str(symbol) 
                            for symbol in self._algo_managed_positions]
            self.object_store.save(self.MANAGED_POSITIONS_KEY, json.dumps(positions_list))
            
            # Save entry times, prices, highest prices as JSON
            # Convert Symbol objects to strings for JSON serialization
            entry_times_str = {str(k.value) if hasattr(k, 'value') else str(k): 
                             v.isoformat() if hasattr(v, 'isoformat') else str(v) 
                             for k, v in self.entry_time.items()}
            self.object_store.save(self.ENTRY_TIMES_KEY, json.dumps(entry_times_str))
            
            entry_prices_str = {str(k.value) if hasattr(k, 'value') else str(k): v 
                              for k, v in self.entry_price.items()}
            self.object_store.save(self.ENTRY_PRICES_KEY, json.dumps(entry_prices_str))
            
            highest_prices_str = {str(k.value) if hasattr(k, 'value') else str(k): v 
                                for k, v in self.highest_price.items()}
            self.object_store.save(self.HIGHEST_PRICES_KEY, json.dumps(highest_prices_str))
            
        except Exception as e:
            self.logger.error(f"Failed to save state to ObjectStore: {e}")
    

    def on_end_of_algorithm(self) -> None:
        try:
    
            # Persist final state before algorithm ends
            self._save_state_to_object_store()
            self.logger.info("Final state persisted to ObjectStore")

            # Apply same exit logic as _evaluate_signals instead of blindly liquidating
            for symbol in list(self._algo_managed_positions):
                if not self.portfolio[symbol].invested:
                    continue
                    
                try:
                    current_price = float(self.securities[symbol].price)
                    if current_price <= 0:
                        continue
                        
                    avg_entry = self.portfolio[symbol].average_price
                    qty = abs(self.portfolio[symbol].quantity)
                    pnl_pct = (current_price - avg_entry) / avg_entry
                    held_time = self.time - self.entry_time.get(symbol, self.time)
                    
                    if symbol not in self.highest_price:
                        self.highest_price[symbol] = current_price
                    else:
                        self.highest_price[symbol] = max(self.highest_price[symbol], current_price)
                    
                    should_exit = False
                    reason = ""
                    
                    # Apply same exit conditions as in _evaluate_signals
                    if pnl_pct <= -0.03:
                        should_exit, reason = True, "stop_loss"
                    elif pnl_pct >= 0.08:
                        should_exit, reason = True, "take_profit"
                    elif held_time >= timedelta(hours=30) and pnl_pct >= 0.018:
                        should_exit, reason = True, "profit_lock"
                    elif held_time >= timedelta(days=14):
                        should_exit, reason = True, "time_exit"
                    
                    # Gap down protection
                    if pnl_pct <= -0.05:
                        self.logger.critical(f"GAP DOWN DETECTED: {symbol.value} at {pnl_pct:.2%}")
                        should_exit, reason = True, "gap_protection"
                    
                    # Only exit if conditions are met
                    if should_exit:
                        # Use market_order instead of liquidate
                        self.market_order(symbol, -qty, tag=f"end_of_algo_{reason}")
                        
                        pnl_dollar = qty * (current_price - avg_entry)
                        trade_exit = f"{self.time.strftime('%Y-%m-%d %H:%M')} SELL {symbol.value} - Qty: {qty} @ ${current_price:.2f} | P&L: ${pnl_dollar:.2f} | Reason: {reason}"
                        self.trade_history.append(trade_exit)
                        
                        self.logger.info(f"END EXIT {symbol.value}: {reason}, P&L ${pnl_dollar:.0f}")
                        self._algo_managed_positions.discard(symbol)
                    else:
                        self.logger.info(f"HOLDING {symbol.value}: No exit condition met (P&L: {pnl_pct:.2%})")
                        
                except Exception as e:
                    self.logger.error(f"Error processing {symbol.value} at end: {e}")

            final_value = self.portfolio.total_portfolio_value
            total_return = (final_value - self._starting_cash) / self._starting_cash

            # ✅ FIX: Add comprehensive trade summary with safe parsing
            total_trades = len(self.trade_history)
            wins = 0
            losses = 0
            total_pnl = 0.0
            
            # Parse trade history more safely
            for trade in self.trade_history:
                if "SELL" in trade:
                    try:
                        # Extract P&L from format: "P&L: $123.45"
                        pnl_str = trade.split("P&L: $")[1].split()[0]
                        pnl = float(pnl_str)
                        total_pnl += pnl
                        if pnl > 0:
                            wins += 1
                        else:
                            losses += 1
                    except (IndexError, ValueError) as e:
                        self.logger.warning(f"Could not parse P&L from trade: {trade}")
                        
            win_rate = wins / total_trades if total_trades > 0 else 0
            avg_pnl = total_pnl / total_trades if total_trades > 0 else 0
        
            self.logger.info("=" * 60)
            self.logger.critical("FINAL RESULTS")
            self.logger.info("=" * 60)
            self.logger.info(f"Starting Capital: ${self._starting_cash:,.0f}")
            self.logger.info(f"Final Value: ${final_value:,.0f}")
            self.logger.info(f"Total Return: {total_return:.2%}")
            self.logger.info(f"Total P&L: ${total_pnl:,.2f}")
            self.logger.info(f"Total Trades: {total_trades}")
            self.logger.info(f"Wins/Losses: {wins}/{losses}")
            self.logger.info(f"Win Rate: {win_rate:.1%}")
            self.logger.info(f"Avg P&L per Trade: ${avg_pnl:,.2f}")
            self.logger.info(f"Market Regime (Final): {self.market_regime}")
            
            # ✅ FIX: Log positions still held
            remaining_positions = [s for s in self._algo_managed_positions if self.portfolio[s].invested]
            if remaining_positions:
                self.logger.info(f"Positions Still Held: {len(remaining_positions)}")
                for symbol in remaining_positions:
                    current_price = float(self.securities[symbol].price)
                    avg_entry = self.portfolio[symbol].average_price
                    qty = abs(self.portfolio[symbol].quantity)
                    pnl_pct = (current_price - avg_entry) / avg_entry
                    self.logger.info(f"  {symbol.value}: {qty} shares @ ${current_price:.2f} (P&L: {pnl_pct:.2%})")
            
            self.logger.info("=" * 60)
        
            # Log trade details
            for trade in self.trade_history:
                self.logger.info(trade)
            
        except Exception as e:
            self.logger.error(f"End error: {e}")
    
    def on_data(self, data) -> None:
        pass

