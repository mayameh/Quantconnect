from AlgorithmImports import *
from datetime import timedelta, time as dt_time
from collections import defaultdict, deque
from production_config import BOT_Config, ProductionLogger
from production_wrapper import RiskManager, TradingState
from email_helpers import format_summary_email, format_weekly_summary

class MayankAlgo_Production(QCAlgorithm):
    def initialize(self) -> None:
        self.config = BOT_Config()
        self.logger = ProductionLogger(self.config)
        self.debug("Initializing Production Algorithm")
        
        if self.config.general.mode == "LIVE":
            self.logger.critical("LIVE TRADING MODE ENABLED")
            self.set_brokerage_model(
                BrokerageName.INTERACTIVE_BROKERS_BROKERAGE, 
                AccountType.CASH
            )
        else:
            self.logger.info("PAPER TRADING MODE")
        
        self.set_start_date(2020, 1, 1)
        self.set_end_date(2025, 12, 28)
        self.set_cash(self.config.general.starting_capital)
        self._starting_cash = self.portfolio.cash
        
        self._core_symbols = []
        self._dynamic_symbols = set()
        self._all_symbols = set()
        self._algo_managed_positions = set()
        self.spy = None  # Initialize to None, will be set later
        self.spy_ema_20 = None
        self.spy_ema_50 = None
        self.spy_rsi = None
        self._indicators = {}
        self.entry_time = {}
        self.highest_price = {}
        self.market_regime = "NEUTRAL"
        self._bear_dip_positions = set()  # Track positions opened during bear dips
        self._bear_dip_scale_in_pending = {}  # symbol -> {'target_qty': int, 'entry_price': float}
        self._last_universe_selection = None
        self.trade_history = deque(maxlen=50)
        self.winning_trades = deque(maxlen=30)
        self.losing_trades = deque(maxlen=20)
        self.symbol_performance = defaultdict(lambda: {'trades': 0, 'wins': 0, 'total_pnl': 0.0, 'consecutive_losses': 0, 'win_rate': 0.0})
        
        self.risk_manager = RiskManager(self.logger, self.config)
        self.peak_equity = self._starting_cash
        self._daily_open_equity = self._starting_cash  # Track daily open for proper daily loss calc
        self.max_positions = self.config.trading.max_positions
        
        try:
            # Main SPY symbol is critical for scheduling - fail if not added
            self.spy = self.add_equity("SPY", Resolution.DAILY, "USA").symbol
            
            # Indicators are secondary but important for regime
            self.spy_ema_20 = self.ema(self.spy, 20, Resolution.DAILY)
            self.spy_ema_50 = self.ema(self.spy, 50, Resolution.DAILY)
            self.spy_rsi = self.rsi(self.spy, 14, MovingAverageType.EXPONENTIAL, Resolution.DAILY)
            self.logger.info("Added SPY regime tracker")
            
            # Warm SPY regime indicators so regime detection is ready immediately
            self._warm_up_spy_regime()
        except Exception as e:
            self.logger.error(f"Failed to add SPY: {e}")

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
            self.add_universe(self._select_universe)
            self.logger.info("Added dynamic universe")
        except Exception as e:
            self.logger.error(f"Failed to add universe: {e}")
        
        if self.live_mode:
            self.logger.critical("LIVE TRADING MODE - Market hours will be enforced")
        else:
            self.logger.info("BACKTESTING MODE - Market hours check disabled")
  

        self.schedule.on(self.date_rules.every_day("SPY"), self.time_rules.at(9, 25), self._reset_daily_state)
        self.schedule.on(self.date_rules.every_day("SPY"), self.time_rules.at(9, 25), self._detect_market_regime)
        self.schedule.on(self.date_rules.every_day("SPY"), self.time_rules.at(9, 35), self._evaluate_signals_safe)
        self.schedule.on(self.date_rules.every_day("SPY"), self.time_rules.at(9, 35), self._send_portfolio_summary_email)
        self.schedule.on(self.date_rules.every_day("SPY"), self.time_rules.at(10, 30), self._evaluate_signals_safe)
        self.schedule.on(self.date_rules.every_day("SPY"), self.time_rules.at(11, 30), self._evaluate_signals_safe)
        self.schedule.on(self.date_rules.every_day("SPY"), self.time_rules.at(12, 0), self._detect_market_regime)
        self.schedule.on(self.date_rules.every_day("SPY"), self.time_rules.at(12, 30), self._evaluate_signals_safe)
        self.schedule.on(self.date_rules.every_day("SPY"), self.time_rules.at(13, 30), self._evaluate_signals_safe)
        self.schedule.on(self.date_rules.every_day("SPY"), self.time_rules.at(14, 30), self._evaluate_signals_safe)
        self.schedule.on(self.date_rules.every_day("SPY"), self.time_rules.at(15, 0), self._detect_market_regime)
        self.schedule.on(self.date_rules.every_day("SPY"), self.time_rules.at(15, 30), self._evaluate_signals_safe)
        self.schedule.on(self.date_rules.every_day("SPY"), self.time_rules.at(15, 30), self._send_portfolio_summary_email)
        self.schedule.on(self.date_rules.every_day("SPY"), self.time_rules.at(16, 0), self._daily_risk_summary)
        self.schedule.on(self.date_rules.every_day("SPY"), self.time_rules.at(16, 0), self._send_portfolio_summary_email)
        self.schedule.on(self.date_rules.every(DayOfWeek.FRIDAY), self.time_rules.at(16, 0), self._send_weekly_summary_email)

        # Option A: Use hour-level global warmup to avoid long daily delays
        self.set_warm_up(200, Resolution.HOUR)
        
        self.logger.info("=" * 60)
        self.logger.info("PRODUCTION ALGORITHM INITIALIZED")
        self.logger.info(f"Mode: {self.config.general.mode}")
        self.logger.info("=" * 60)
    
    def _reset_daily_state(self) -> None:
        """Reset daily tracking at market open — allows trading after previous day's losses."""
        self._daily_open_equity = self.portfolio.total_portfolio_value
        # Reset risk manager daily — prevents permanent lockout after drawdown
        if not self.risk_manager.can_trade():
            self.logger.info(f"DAILY RESET: reopening trading, current equity ${self._daily_open_equity:,.2f}")
            self.risk_manager.state = TradingState.RUNNING
            # Update peak to current equity to prevent immediate re-trigger of drawdown check
            if self._daily_open_equity < self.peak_equity * (1 - self.config.risk.max_drawdown_pct * 0.5):
                self.peak_equity = self._daily_open_equity
                self.logger.info(f"PEAK RESET: new baseline ${self.peak_equity:,.2f} to prevent lockout")

    def _is_market_open(self) -> bool:
        """Check if US market is currently open"""
        try:
            current_time = self.time.time()
            is_open = self.time.weekday() < 5 and dt_time(9, 30) <= current_time <= dt_time(16, 0)
            return is_open
        except Exception:
            return True

    def _evaluate_signals_safe(self) -> None:
        try:
            # ✅ RECOMMENDED: Skip market check in backtesting
            if self.live_mode:
                # Only enforce market hours in live trading
                if not self._is_market_open():
                    self.debug("Market closed - skipping evaluation")
                    return

            # ── ALWAYS run exit logic first (even during emergency stop) ──
            self._run_exit_logic()
            
            # ── Check if portfolio recovered after exits — reset risk manager ──
            current_equity = self.portfolio.total_portfolio_value
            if current_equity > self.peak_equity:
                self.peak_equity = current_equity
            drawdown = (self.peak_equity - current_equity) / self.peak_equity if self.peak_equity > 0 else 0
            if not self.risk_manager.can_trade() and drawdown < self.config.risk.max_drawdown_pct * 0.8:
                self.logger.info(f"RISK RESET: drawdown recovered to {drawdown:.1%}, resuming trading")
                self.risk_manager.state = TradingState.RUNNING
            
            if not self.risk_manager.can_trade():
                return
            
            current_equity = self.portfolio.total_portfolio_value
            daily_loss = self._daily_open_equity - current_equity  # Compare to today's open, not starting capital
            
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
            
            self._evaluate_entries()
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
            if self.spy_ema_20 is None or self.spy_ema_50 is None or not (self.spy_ema_20.is_ready and self.spy_ema_50.is_ready):
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

            # ---- EXTREME BEAR REGIME (deep selloff = discount buying opportunity) ----
            # Simplified: no momentum_bear required — RSI + discount is sufficient
            elif (
                price_below_50
                and ema_structure_bear
                and rsi_ready
                and rsi_val < self.config.bear_dip_buy.spy_rsi_threshold
                and abs(ema_50) > 1e-9
                and ((ema_50 - spy_price) / ema_50) >= self.config.bear_dip_buy.spy_discount_pct
            ):
                self.market_regime = "EXTREME_BEAR"

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
                elif previous_regime == "EXTREME_BEAR" and price_below_50 and rsi_ready and rsi_val < 40:
                    self.market_regime = "EXTREME_BEAR"
                else:
                    self.market_regime = "NEUTRAL"

            # ─────────────────────────────────────────────
            # LOGGING
            # ─────────────────────────────────────────────
            
            # Log current stats for debugging "always BULL" issues
            self.logger.info(
                f"Regime Scan: {self.market_regime} | "
                f"Price: {spy_price:.2f} | EMA20: {ema_20:.2f} | EMA50: {ema_50:.2f} | "
                f"Rising: {ema_20_rising}"
            )

            if previous_regime != self.market_regime:
                self.logger.critical(f"REGIME CHANGE: {previous_regime} -> {self.market_regime} | SPY {spy_price:.2f} EMA20 {ema_20:.2f} EMA50 {ema_50:.2f}" + (f" RSI {rsi_val:.1f}" if rsi_ready else ""))

        except Exception as e:
            self.logger.error(f"Regime detection error: {e}")

    def _select_universe(self, fundamentals: list) -> list:
        """Unified modern universe selector - handles coarse + fine in one pass"""
        if self._last_universe_selection is not None:
            days_since = (self.time.date() - self._last_universe_selection.date()).days
            if days_since < 14:
                return []
        
        # Quality filters (coarse + fine combined)
        quality_stocks = [f for f in fundamentals 
                         if f.price >= 50.0 
                         and f.dollar_volume >= 100000000 
                         and f.volume > 0
                         and f.has_fundamental_data
                         and not f.symbol.value.startswith(('SPY', 'QQQ', 'IWM', 'DIA', 'VTI', 'VOO', 'XL'))
                         and f.asset_classification.morningstar_sector_code != MorningstarSectorCode.FINANCIAL_SERVICES
                         and f.valuation_ratios.pe_ratio > 0 
                         and f.valuation_ratios.pe_ratio < 100
                         and f.asset_classification.morningstar_industry_group_code > 0]
        
        # Score non-core stocks by momentum
        momentum_scores = []
        for stock in quality_stocks:
            if stock.symbol in self._core_symbols:
                continue
            try:
                price_momentum = stock.valuation_ratios.price_change_1m or 0
                revenue_growth = stock.operation_ratios.revenue_growth.one_year or 0
                score = (price_momentum * 0.6 + revenue_growth * 0.4)
                momentum_scores.append((stock.symbol, score))
            except:
                continue
        
        # Select top 10 momentum stocks (excluding ACN)
        momentum_scores.sort(key=lambda x: x[1], reverse=True)
        selected = [s for s, _ in momentum_scores[:10] if s.value != "ACN"]
        
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
            # HOUR resolution for timely signals
            self._indicators[symbol] = {
                'macd': self.macd(symbol, 12, 26, 9, MovingAverageType.EXPONENTIAL, Resolution.HOUR),
                'rsi': self.rsi(symbol, 14, MovingAverageType.EXPONENTIAL, Resolution.HOUR),
                'ema_50': self.ema(symbol, 50, Resolution.HOUR),
                'ema_9': self.ema(symbol, 9, Resolution.HOUR),
                'ema_21': self.ema(symbol, 21, Resolution.HOUR),
                'volume_sma': self.sma(symbol, 20, Resolution.HOUR, Field.VOLUME),
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
        ema_9 = indicators.get("ema_9")
        return all([macd and macd.is_ready, rsi and rsi.is_ready, ema_50 and ema_50.is_ready, ema_9 and ema_9.is_ready])

    def _warm_up_symbol_indicators(self, symbol, resolution: Resolution = Resolution.HOUR) -> None:
        """Warm per-symbol indicators using historical data so they are ready immediately."""
        try:
            indicators = self._indicators.get(symbol)
            if not indicators:
                return

            for key in ["ema_50", "ema_9", "ema_21", "rsi", "macd", "volume_sma"]:
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
            # properly warm up with history
            history = self.history(self.spy, 75, Resolution.DAILY)
            if history.empty:
                self.logger.warning("SPY History empty for warmup")
                return

            # Iterate through history to update indicators
            # history.loc[self.spy] gets the dataframe for the symbol
            if self.spy not in history.index:
                 self.logger.warning("SPY not in history index")
                 return
                 
            spy_hist = history.loc[self.spy]
            for time, row in spy_hist.iterrows():
                # Check for close price
                if 'close' in row:
                    price = row['close']
                    if self.spy_ema_20: self.spy_ema_20.update(time, price)
                    if self.spy_ema_50: self.spy_ema_50.update(time, price)
                    if hasattr(self, "spy_rsi") and self.spy_rsi: 
                        self.spy_rsi.update(time, price)
            
            self.logger.info("SPY regime indicators warmed up via History.")
                
        except Exception as e:
            self.logger.error(f"SPY regime warmup error: {e}")
    
    def _calculate_position_size(self, symbol, current_price) -> int:
        try:
            available_cash = self.portfolio.cash
            safe_available = available_cash * 0.75
            # Smaller positions = more diversification, less risk per trade
            target_value = min(safe_available * 0.60, 5000)
            if target_value < 3000:
                return 0
            # Account for estimated commissions (Interactive Brokers style)
            qty = int(target_value / current_price)
            if qty <= 0:
                return 0

            fee = self._estimate_order_fee(symbol, qty, current_price)
            total_cost = qty * current_price + fee

            # If fees push us over the target_value, scale down once
            if total_cost > target_value:
                qty = int((target_value - fee) / current_price)
                fee = self._estimate_order_fee(symbol, qty, current_price)
                total_cost = qty * current_price + fee

            return qty if qty > 0 and total_cost >= 3000 else 0
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
        """Full evaluation: exits + entries (called from legacy paths)."""
        self._run_exit_logic()
        self._evaluate_entries()

    def _run_exit_logic(self) -> None:
        """Process exits for all open positions — runs even during emergency stop.
        Includes trailing stop, RSI overbought exit, and regime-aware thresholds."""
        if self.is_warming_up:
            return
        
        # Get all invested positions (exclude SPY - it's only for regime tracking)
        all_invested = [s for s in self.portfolio.keys() if self.portfolio[s].invested and s != self.spy]
        
        # Get ONLY algorithm-managed positions for counting
        algo_invested = [s for s in all_invested if s in self._algo_managed_positions]
        
        # EXIT LOGIC - Only manage algorithmic positions
        for symbol in algo_invested:
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
                
                # Use bear dip-buy parameters for bear-dip positions
                is_bear_dip = symbol in self._bear_dip_positions
                sl_pct = self.config.bear_dip_buy.stop_loss_pct if is_bear_dip else self.config.trading.stop_loss_pct
                tp_pct = self.config.bear_dip_buy.take_profit_pct if is_bear_dip else self.config.trading.take_profit_pct
                pl_hours = self.config.bear_dip_buy.profit_lock_hours if is_bear_dip else self.config.trading.profit_lock_hours
                pl_min = self.config.bear_dip_buy.profit_lock_min_gain_pct if is_bear_dip else self.config.trading.profit_lock_min_gain_pct
                max_hold = timedelta(days=self.config.bear_dip_buy.max_hold_days) if is_bear_dip else timedelta(days=self.config.trading.max_hold_days)
                
                should_exit = False
                reason = ""
                
                # 1. STOP LOSS
                if pnl_pct <= -sl_pct:
                    should_exit, reason = True, "stop_loss"
                
                # 2. TAKE PROFIT (hard cap)
                elif pnl_pct >= tp_pct:
                    should_exit, reason = True, "take_profit"
                
                # 3. TRAILING STOP — let winners run, lock in gains
                elif (getattr(self.config.trading, 'trailing_stop_enabled', False)
                      and pnl_pct >= self.config.trading.trailing_activation_pct):
                    # Trailing is active — check if price dropped from high
                    drop_from_high = (self.highest_price[symbol] - current_price) / self.highest_price[symbol]
                    if drop_from_high >= self.config.trading.trailing_stop_pct:
                        should_exit, reason = True, f"trailing_stop|high=${self.highest_price[symbol]:.2f}"
                
                # 4. PROFIT LOCK — time-based gain lock
                elif held_time >= timedelta(hours=pl_hours) and pnl_pct >= pl_min:
                    should_exit, reason = True, "profit_lock"
                
                # 5. RSI OVERBOUGHT EXIT — sell when extreme momentum exhausted
                elif pnl_pct > 0.02:  # Only if in decent profit
                    indicators = self._indicators.get(symbol)
                    if indicators and indicators.get('rsi') and indicators['rsi'].is_ready:
                        rsi_val = indicators['rsi'].current.value
                        if rsi_val > 85:  # Only exit at extreme overbought
                            should_exit, reason = True, f"rsi_overbought|RSI={rsi_val:.0f}"
                
                # 6. TIME EXIT — max hold
                elif held_time >= max_hold:
                    should_exit, reason = True, "time_exit"
            
                # 7. GAP DOWN PROTECTION
                if pnl_pct <= -0.05:
                    self.logger.critical(f"GAP DOWN DETECTED: {symbol.value} at {pnl_pct:.2%}")
                    should_exit, reason = True, "gap_protection"
            
                if should_exit:
                    self.liquidate(symbol, tag=reason)

                    pnl_dollar = qty * (current_price - avg_entry)
                    trade_exit = f"{self.time.strftime('%Y-%m-%d %H:%M')} SELL {symbol.value} - Qty: {qty} @ ${current_price:.2f} | P&L: ${pnl_dollar:.2f}"
                    self.trade_history.append(trade_exit)

                    self.debug(f"EXIT {symbol.value}: {reason}, P&L ${pnl_dollar:.0f}, pnl%={pnl_pct:.2%}")
                    self._algo_managed_positions.discard(symbol)
                    self._bear_dip_positions.discard(symbol)
                    self._bear_dip_scale_in_pending.pop(symbol, None)
            except Exception as e:
                self.logger.error(f"Exit error: {e}")

    def _evaluate_entries(self) -> None:
        """Process new entries — only called when risk checks pass."""
        if self.is_warming_up:
            return
        
        # Get all invested positions (exclude SPY)
        all_invested = [s for s in self.portfolio.keys() if self.portfolio[s].invested and s != self.spy]
        algo_invested = [s for s in all_invested if s in self._algo_managed_positions]
        
        # ENTRY LOGIC - Count only algorithm-managed positions
        current_equity = self.portfolio.total_portfolio_value
        portfolio_return = (current_equity - self._starting_cash) / self._starting_cash

        # ─────────────────────────────────────────────
        # BEAR / EXTREME BEAR → dip-buy blue-chip discounts
        # ─────────────────────────────────────────────
        if self.market_regime in ("BEAR", "EXTREME_BEAR") and self.config.bear_dip_buy.enabled:
            # ── SCALE-IN CHECK: top-up partial positions that are confirming ──
            if self.config.bear_dip_buy.scale_in_enabled:
                for sym, info in list(self._bear_dip_scale_in_pending.items()):
                    try:
                        if not self.portfolio[sym].invested:
                            self._bear_dip_scale_in_pending.pop(sym, None)
                            continue
                        cp = float(self.securities[sym].price)
                        gain = (cp - info['entry_price']) / info['entry_price']
                        held = self.time - self.entry_time.get(sym, self.time)
                        if gain >= self.config.bear_dip_buy.scale_in_gain_pct and held >= timedelta(hours=self.config.bear_dip_buy.scale_in_min_hours):
                            add_qty = info['target_qty']
                            if add_qty > 0 and self.portfolio.cash >= cp * add_qty:
                                self.market_order(sym, add_qty)
                                self.trade_history.append(f"{self.time.strftime('%Y-%m-%d %H:%M')} SCALE-IN {sym.value} +{add_qty} @ ${cp:.2f} | gain={gain:.1%}")
                                self.logger.info(f"BEAR SCALE-IN: {sym.value} +{add_qty} @ ${cp:.2f} gain={gain:.1%}")
                            self._bear_dip_scale_in_pending.pop(sym, None)
                        elif gain <= -self.config.bear_dip_buy.stop_loss_pct:
                            # Stop hit before scale-in — cancel pending
                            self._bear_dip_scale_in_pending.pop(sym, None)
                    except Exception:
                        self._bear_dip_scale_in_pending.pop(sym, None)

            bear_dip_count = len([s for s in algo_invested if s in self._bear_dip_positions])
            bear_max = self.config.bear_dip_buy.max_positions
            
            if bear_dip_count >= bear_max:
                self.debug(f"SKIP BEAR DIP: max bear positions ({bear_dip_count}/{bear_max})")
                return
            if self.portfolio.cash < 3000:
                self.debug(f"SKIP BEAR DIP: insufficient cash ${self.portfolio.cash:.0f}")
                return
            
            # ── MARKET BREADTH CHECK ──
            if self.config.bear_dip_buy.breadth_enabled:
                above_ema_count = 0
                total_checked = 0
                for s in self._core_symbols:
                    ind = self._indicators.get(s)
                    if ind and ind.get('ema_50') and ind['ema_50'].is_ready:
                        total_checked += 1
                        try:
                            if float(self.securities[s].price) > ind['ema_50'].current.value:
                                above_ema_count += 1
                        except Exception:
                            pass
                breadth = above_ema_count / total_checked if total_checked > 0 else 0
                self.debug(f"BREADTH: {above_ema_count}/{total_checked} = {breadth:.1%} above EMA50")
                if breadth < self.config.bear_dip_buy.min_breadth_pct:
                    self.debug(f"SKIP BEAR DIP: breadth {breadth:.1%} < min {self.config.bear_dip_buy.min_breadth_pct:.0%}")
                    return

            # Scan symbols for discount entries
            scan_symbols = self._core_symbols if self.config.bear_dip_buy.core_only else self._all_symbols
            bear_candidates = []
            self.debug(f"BEAR DIP SCAN: {len(scan_symbols)} symbols")
            
            for symbol in scan_symbols:
                try:
                    if symbol == self.spy or self.portfolio[symbol].invested:
                        continue
                    if not self._is_symbol_ready(symbol):
                        continue
                    indicators = self._indicators.get(symbol)
                    if not indicators:
                        continue
                    
                    rsi = indicators["rsi"]
                    ema_50 = indicators["ema_50"]
                    vol_sma = indicators.get("volume_sma")
                    current_price = float(self.securities[symbol].price)
                    if current_price <= 0:
                        continue
                    
                    rsi_val = rsi.current.value
                    rsi_prev = rsi.previous.value if rsi.previous else rsi_val
                    ema_val = ema_50.current.value
                    
                    # Oversold + trading at discount to EMA50
                    if ema_val <= 0:
                        continue
                    discount = (ema_val - current_price) / ema_val
                    
                    if rsi_val >= self.config.bear_dip_buy.symbol_rsi_max or discount < self.config.bear_dip_buy.symbol_discount_pct:
                        continue
                    
                    # ── BOUNCE CONFIRMATION: RSI must be turning up ──
                    if self.config.bear_dip_buy.require_bounce and rsi_val <= rsi_prev:
                        self.debug(f"SKIP {symbol.value}: no bounce (RSI {rsi_prev:.1f}→{rsi_val:.1f})")
                        continue
                    
                    # ── VOLUME CONFIRMATION: current volume > 1.2x average ──
                    if self.config.bear_dip_buy.require_volume_spike and vol_sma and vol_sma.is_ready:
                        try:
                            current_vol = float(self.securities[symbol].volume)
                            avg_vol = vol_sma.current.value
                            if avg_vol > 0 and current_vol < avg_vol * self.config.bear_dip_buy.volume_spike_ratio:
                                self.debug(f"SKIP {symbol.value}: low volume ({current_vol:.0f} < {avg_vol * self.config.bear_dip_buy.volume_spike_ratio:.0f})")
                                continue
                        except Exception:
                            pass  # If volume check fails, allow entry anyway
                    
                    # Score: deeper discount + more oversold + stronger bounce = better
                    bounce_strength = max(0, rsi_val - rsi_prev) / 10.0
                    score = discount * (1.0 - rsi_val / 100.0) * (1.0 + bounce_strength)
                    bear_candidates.append((symbol, score, discount, rsi_val))
                    self.debug(f"BEAR CANDIDATE: {symbol.value} discount={discount:.1%} RSI={rsi_val:.1f} bounce={rsi_val - rsi_prev:+.1f}")
                except Exception:
                    pass
            
            if bear_candidates:
                bear_candidates.sort(key=lambda x: x[1], reverse=True)
                slots_available = bear_max - bear_dip_count
                for cand_symbol, _, cand_discount, cand_rsi in bear_candidates[:slots_available]:
                    current_price = float(self.securities[cand_symbol].price)
                    full_qty = self._calculate_position_size(cand_symbol, current_price)
                    if full_qty <= 0:
                        continue
                    
                    # ── SCALE-IN: enter with partial size ──
                    if self.config.bear_dip_buy.scale_in_enabled:
                        initial_qty = max(1, int(full_qty * self.config.bear_dip_buy.initial_size_pct))
                        remaining_qty = full_qty - initial_qty
                    else:
                        initial_qty = full_qty
                        remaining_qty = 0
                    
                    if initial_qty > 0 and self.portfolio.cash >= current_price * initial_qty:
                        ticket = self.market_order(cand_symbol, initial_qty)
                        tag = f"bear_dip|discount={cand_discount:.1%}|RSI={cand_rsi:.0f}|scale={'partial' if remaining_qty > 0 else 'full'}"
                        trade_entry = f"{self.time.strftime('%Y-%m-%d %H:%M')} BEAR-DIP BUY {cand_symbol.value} - Qty: {initial_qty}/{full_qty} @ ${current_price:.2f} | {tag}"
                        self.trade_history.append(trade_entry)
                        self.logger.info(f"BEAR DIP BUY: {cand_symbol.value} {initial_qty}/{full_qty} @ ${current_price:.2f} discount={cand_discount:.1%} RSI={cand_rsi:.0f}")
                        
                        if ticket:
                            self._algo_managed_positions.add(cand_symbol)
                            self._bear_dip_positions.add(cand_symbol)
                            self.entry_time[cand_symbol] = self.time
                            self.highest_price[cand_symbol] = current_price
                            if remaining_qty > 0:
                                self._bear_dip_scale_in_pending[cand_symbol] = {
                                    'target_qty': remaining_qty,
                                    'entry_price': current_price
                                }
            return
        
        # ─────────────────────────────────────────────
        # BEAR (normal) → dip-buy logic above already handled; skip momentum entries
        # ─────────────────────────────────────────────
        if self.market_regime == "BEAR":
            self.debug(f"SKIP ENTRY: BEAR market regime (momentum entries skipped)")
            return
            
        # ─────────────────────────────────────────────
        # BULL / NEUTRAL → momentum entries with scoring system
        # ─────────────────────────────────────────────
        # Dynamic position sizing based on regime and performance
        if self.market_regime == "BULL" and portfolio_return > 0.0:
            max_allowed = self.max_positions  # Full capacity in bull
        elif self.market_regime == "BULL":
            max_allowed = min(3, self.max_positions)
        elif self.market_regime == "NEUTRAL":
            max_allowed = min(3, self.max_positions)  # Allow 3 in neutral too
        else:
            max_allowed = 1

        # COUNT ONLY ALGORITHM-MANAGED POSITIONS
        algo_position_count = len(algo_invested)
        
        if algo_position_count >= max_allowed:
            self.debug(f"SKIP ENTRY: Max algo positions for {self.market_regime} regime ({algo_position_count}/{max_allowed})")
            return
            
        if self.portfolio.cash < 3000:
            self.debug(f"SKIP ENTRY: insufficient cash ${self.portfolio.cash:.0f}")
            return

        candidates = []
        self.debug(f"SCANNING {len(self._all_symbols)} symbols for entry | regime={self.market_regime}")
        not_ready_symbols = []
        for symbol in self._all_symbols:
            try:
                if symbol == self.spy:
                    continue
                if self.portfolio[symbol].invested:
                    continue

                if not self._is_symbol_ready(symbol):
                    not_ready_symbols.append(symbol.value)
                    continue
                indicators = self._indicators.get(symbol)
                if not indicators:
                    continue
                macd = indicators["macd"]
                rsi = indicators["rsi"]
                ema_50 = indicators["ema_50"]
                ema_9 = indicators.get("ema_9")
                ema_21 = indicators.get("ema_21")
                vol_sma = indicators.get("volume_sma")
                current_price = float(self.securities[symbol].price)
                if current_price <= 0:
                    continue
                
                macd_val = macd.current.value
                macd_sig = macd.signal.current.value
                rsi_val = rsi.current.value
                ema_50_val = ema_50.current.value
                ema_9_val = ema_9.current.value if ema_9 and ema_9.is_ready else current_price
                ema_21_val = ema_21.current.value if ema_21 and ema_21.is_ready else ema_50_val
                
                # ── SIMPLE MOMENTUM ENTRY ──
                # Core filter: MACD bullish + price above EMA50
                if macd_val <= macd_sig:
                    continue
                if current_price < ema_50_val:
                    continue
                if rsi_val > 75 or rsi_val < 30:
                    continue  # Skip extremes
                
                # Score based on multiple factors
                score = 0.0
                
                # MACD strength
                macd_diff = macd_val - macd_sig
                score += min(macd_diff * 100, 3.0)  # Cap at 3 points
                
                # RSI sweet spot (40-60 is ideal for entry)
                if 40 <= rsi_val <= 60:
                    score += 2.0
                elif 35 <= rsi_val < 40 or 60 < rsi_val <= 70:
                    score += 1.0
                
                # EMA alignment: 9 > 21 > 50 = strong trend
                if ema_9_val > ema_21_val > ema_50_val:
                    score += 2.0
                elif ema_9_val > ema_50_val:
                    score += 1.0
                
                # Volume confirmation
                if vol_sma and vol_sma.is_ready:
                    try:
                        cur_vol = float(self.securities[symbol].volume)
                        avg_vol = vol_sma.current.value
                        if avg_vol > 0 and cur_vol > avg_vol * 1.1:
                            score += 1.0
                    except Exception:
                        pass
                
                # Price momentum: how far above EMA50
                pct_above_ema50 = (current_price - ema_50_val) / ema_50_val
                if 0 < pct_above_ema50 <= 0.05:
                    score += 1.0  # Near EMA50 — better entry point
                elif pct_above_ema50 > 0.10:
                    score -= 0.5  # Extended — higher risk
                
                if score >= 3.0:
                    candidates.append((symbol, score))
            except Exception as e:
                pass
        
        # Log which symbols weren't ready (sample up to 3 for brevity)
        if not_ready_symbols:
            sample = ", ".join(not_ready_symbols[:3])
            self.debug(f"Not ready: {sample} (total {len(not_ready_symbols)})")
        
        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            # Buy the top candidate(s) up to available slots
            slots_available = max_allowed - algo_position_count
            for best_symbol, best_score in candidates[:slots_available]:
                current_price = float(self.securities[best_symbol].price)
                qty = self._calculate_position_size(best_symbol, current_price)
                if qty > 0:
                    ticket = self.market_order(best_symbol, qty)
                    
                    trade_entry = f"{self.time.strftime('%Y-%m-%d %H:%M')} BUY {best_symbol.value} - Qty: {qty} @ ${current_price:.2f} | score={best_score:.1f} | {self.market_regime}"
                    self.trade_history.append(trade_entry)
                    
                    if ticket:
                        self._algo_managed_positions.add(best_symbol)
                        self.entry_time[best_symbol] = self.time
                        self.highest_price[best_symbol] = current_price
                        self.debug(f"BUY {best_symbol.value}: qty={qty}, ${current_price:.2f}, score={best_score:.1f}")
    
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
            
            # Send email via QuantConnect notify
            subject = f"Portfolio Summary - {self.time.strftime('%Y-%m-%d %H:%M')}"
            self.notify.email(self.config.monitoring.email_to[0], subject, email_body)
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
        return format_summary_email(self)
    
    def _format_weekly_summary(self) -> str:
        return format_weekly_summary(self)


    # Keep daily summaries but create a different weekly method
    def _send_weekly_summary_email(self) -> None:
        """Send comprehensive weekly portfolio summary"""
        if self.is_warming_up:
            return  # Silent return during warmup
        
        try:
            # More detailed weekly analysis
            email_body = self._format_weekly_summary()
            subject = f"Weekly Portfolio Summary - {self.time.strftime('%Y-%m-%d')}"
            self.notify.email(self.config.monitoring.email_to[0], subject, email_body)
            self.logger.info(f"Weekly summary email sent")
        except Exception as e:
            self.logger.error(f"Weekly email error: {e}")

    def on_end_of_algorithm(self) -> None:
        try:
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
                    
                    # Use bear dip-buy parameters for bear-dip positions
                    is_bear_dip = symbol in self._bear_dip_positions
                    sl_pct = self.config.bear_dip_buy.stop_loss_pct if is_bear_dip else self.config.trading.stop_loss_pct
                    tp_pct = self.config.bear_dip_buy.take_profit_pct if is_bear_dip else self.config.trading.take_profit_pct
                    pl_hours = self.config.bear_dip_buy.profit_lock_hours if is_bear_dip else self.config.trading.profit_lock_hours
                    pl_min = self.config.bear_dip_buy.profit_lock_min_gain_pct if is_bear_dip else self.config.trading.profit_lock_min_gain_pct
                    max_hold = timedelta(days=self.config.bear_dip_buy.max_hold_days) if is_bear_dip else timedelta(days=self.config.trading.max_hold_days)
                    
                    # Apply exit conditions with regime-aware thresholds
                    if pnl_pct <= -sl_pct:
                        should_exit, reason = True, "stop_loss"
                    elif pnl_pct >= tp_pct:
                        should_exit, reason = True, "take_profit"
                    elif held_time >= timedelta(hours=pl_hours) and pnl_pct >= pl_min:
                        should_exit, reason = True, "profit_lock"
                    elif held_time >= max_hold:
                        should_exit, reason = True, "time_exit"
                    
                    # Gap down protection
                    if pnl_pct <= -0.035:
                        self.logger.critical(f"GAP DOWN DETECTED: {symbol.value} at {pnl_pct:.2%}")
                        should_exit, reason = True, "gap_protection"
                    
                    # Only exit if conditions are met
                    if should_exit:
                        # Use market_order instead of liquidate
                        self.market_order(symbol, -qty, tag=f"end_of_algo_{reason}")
                        
                        pnl_dollar = qty * (current_price - avg_entry)
                        trade_exit = f"{self.time.strftime('%Y-%m-%d %H:%M')} SELL {symbol.value} - Qty: {qty} @ ${current_price:.2f} | P&L: ${pnl_dollar:.2f} | Reason: {reason}"
                        self.trade_history.append(trade_exit)
                        
                        self.logger.info(f"END EXIT {symbol.value}: {reason}, P&L ${pnl_dollar:.0f}, bear_dip={is_bear_dip}")
                        self._algo_managed_positions.discard(symbol)
                        self._bear_dip_positions.discard(symbol)
                    else:
                        self.logger.info(f"HOLDING {symbol.value}: No exit condition met (P&L: {pnl_pct:.2%}, bear_dip={is_bear_dip})")
                        
                except Exception as e:
                    self.logger.error(f"Error processing {symbol.value} at end: {e}")

            final_value = self.portfolio.total_portfolio_value
            total_return = (final_value - self._starting_cash) / self._starting_cash

            # ✅ ADD: Comprehensive trade summary
            total_trades = len(self.trade_history)
            wins = len([t for t in self.trade_history if "SELL" in t and float(t.split("P&L: $")[1].split()[0]) > 0])
            losses = total_trades - wins
            win_rate = wins / total_trades if total_trades > 0 else 0
        
            self.logger.info("=" * 60)
            self.logger.critical("FINAL RESULTS")
            self.logger.info("=" * 60)
            self.logger.info(f"Starting Capital: ${self._starting_cash:,.0f}")
            self.logger.info(f"Final Value: ${final_value:,.0f}")
            self.logger.info(f"Total Return: {total_return:.2%}")
            self.logger.info(f"Total Trades: {total_trades}")
            self.logger.info(f"Wins/Losses: {wins}/{losses}")
            self.logger.info(f"Win Rate: {win_rate:.1%}")
            self.logger.info(f"Market Regime (Final): {self.market_regime}")
            self.logger.info("=" * 60)
        
            # Log trade details
            for trade in self.trade_history:
                self.logger.info(trade)
            
        except Exception as e:
            self.logger.error(f"End error: {e}")
    
    def on_data(self, data) -> None:
        pass

