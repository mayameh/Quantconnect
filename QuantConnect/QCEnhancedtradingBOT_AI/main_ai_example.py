"""
AI-Enhanced Trading Algorithm - Example Integration
Shows how to integrate all AI components into the main algorithm
"""

from AlgorithmImports import *
from datetime import datetime, timedelta, time as dt_time
from production_config import BOT_Config, ProductionLogger, EmailAlerter, PerformanceTracker
from production_wrapper import RiskManager, OrderExecutor
from collections import defaultdict, deque

# Import AI modules
from ai_models import (
    VolatilityRegimePredictor,
    ReturnPredictor,
    PortfolioOptimizer,
    RiskMetricsCalculator
)
from ai_advanced import (
    SentimentAnalyzer,
    ReinforcementLearningAgent,
    AdaptiveRiskManager
)


class MayankAlgo_AI_Enhanced(QCAlgorithm):
    
    def initialize(self) -> None:
        """Initialize algorithm with AI enhancements"""
        
        self.config = BOT_Config()
        self.logger = ProductionLogger(self.config)
        self.debug("Initializing AI-Enhanced Algorithm")
        
        # Basic setup
        self.email_alerter = EmailAlerter(self.config, self.logger)
        self.perf_tracker = PerformanceTracker(self.logger)
        
        if self.config.general.mode == "PAPER":
            self.logger.critical("PAPER TRADING MODE ENABLED")
            self.set_brokerage_model(BrokerageName.ALPACA, AccountType.CASH)
        
        self.set_start_date(2025, 4, 10)
        self.set_end_date(2025, 10, 28)
        self.set_cash(self.config.general.starting_capital)
        self._starting_cash = self.portfolio.cash
        
        # Traditional components
        self._core_symbols = []
        self._all_symbols = set()
        self._algo_managed_positions = set()
        self._indicators = {}
        self.entry_time = {}
        self.entry_price = {}
        self.market_regime = "NEUTRAL"
        self.trade_history = deque(maxlen=50)
        
        # ═══════════════════════════════════════════════════════════
        # AI COMPONENTS INITIALIZATION
        # ═══════════════════════════════════════════════════════════
        
        self.logger.info("Initializing AI Components...")
        
        # 1. Volatility Regime Prediction
        self.volatility_predictor = VolatilityRegimePredictor(self.logger)
        self.volatility_regime = "MEDIUM"
        
        # 2. Return Prediction for each stock
        self.return_predictors = {}  # symbol -> ReturnPredictor
        
        # 3. Portfolio Optimization
        self.portfolio_optimizer = PortfolioOptimizer(self.logger)
        
        # 4. Sentiment Analysis
        self.sentiment_analyzer = SentimentAnalyzer(self.logger)
        
        # 5. Reinforcement Learning for Position Sizing
        self.rl_agent = ReinforcementLearningAgent(
            self.logger,
            learning_rate=0.1,
            discount_factor=0.95,
            epsilon=0.2  # 20% exploration
        )
        self.rl_state_tracking = {}  # Track states for RL updates
        
        # 6. Adaptive Risk Management
        self.adaptive_risk = AdaptiveRiskManager(
            self.logger,
            base_stop_loss=0.03,
            base_position_size=0.2
        )
        
        # 7. Advanced Risk Metrics
        self.risk_metrics = RiskMetricsCalculator(self.logger)
        
        self.logger.info("✓ AI Components initialized")
        
        # ═══════════════════════════════════════════════════════════
        # TRADITIONAL SETUP
        # ═══════════════════════════════════════════════════════════
        
        self.risk_manager = RiskManager(self.logger, self.config)
        self.order_executor = OrderExecutor(self.logger, self.config)
        
        # Add core symbols
        for ticker in self.config.universe.core_symbols:
            try:
                eq = self.add_equity(ticker, Resolution.MINUTE, "USA")
                eq.set_leverage(1.0)
                self._core_symbols.append(eq.symbol)
                self._all_symbols.add(eq.symbol)
                self._create_indicators_for_symbol(eq.symbol)
                
                # Create AI return predictor for this symbol
                self.return_predictors[eq.symbol] = ReturnPredictor(self.logger, eq.symbol)
                self.portfolio_optimizer.add_predictor(eq.symbol, self.return_predictors[eq.symbol])
                
                self.logger.info(f"Added CORE: {ticker} with AI predictor")
            except Exception as e:
                self.logger.error(f"Failed to add {ticker}: {e}")
        
        # Add SPY for regime tracking
        try:
            self.spy = self.add_equity("SPY", Resolution.MINUTE, "USA").symbol
            self.spy_ema_20 = self.ema(self.spy, 20, Resolution.DAILY)
            self.spy_ema_50 = self.ema(self.spy, 50, Resolution.DAILY)
            self.spy_rsi = self.rsi(self.spy, 14, MovingAverageType.EXPONENTIAL, Resolution.DAILY)
            self._warm_up_spy_regime()
        except Exception as e:
            self.logger.error(f"Failed to add SPY: {e}")
        
        # ═══════════════════════════════════════════════════════════
        # SCHEDULING
        # ═══════════════════════════════════════════════════════════
        
        # AI Model Training (weekly)
        self.schedule.on(
            self.date_rules.week_start(),
            self.time_rules.at(9, 0),
            self._train_ai_models
        )
        
        # Traditional + AI Regime Detection
        self.schedule.on(
            self.date_rules.every_day(self.spy),
            self.time_rules.at(9, 25),
            self._detect_market_regime_ai
        )
        
        # AI-Enhanced Signal Evaluation
        self.schedule.on(
            self.date_rules.every_day(self.spy),
            self.time_rules.at(9, 30),
            self._evaluate_signals_ai
        )
        
        # Mid-day re-evaluation
        self.schedule.on(
            self.date_rules.every_day(self.spy),
            self.time_rules.at(12, 30),
            self._evaluate_signals_ai
        )
        
        # End of day risk reporting
        self.schedule.on(
            self.date_rules.every_day(self.spy),
            self.time_rules.at(16, 0),
            self._daily_ai_risk_summary
        )
        
        self.set_warm_up(200, Resolution.HOUR)
        
        self.logger.info("=" * 60)
        self.logger.info("AI-ENHANCED ALGORITHM INITIALIZED")
        self.logger.info("=" * 60)
    
    def on_data(self, data):
        """Update AI models with new market data"""
        
        if self.is_warming_up:
            return
        
        try:
            # Update volatility predictor with SPY data
            if self.spy in data and data[self.spy] is not None:
                spy_price = float(data[self.spy].close)
                spy_volume = float(data[self.spy].volume)
                self.volatility_predictor.update(spy_price, spy_volume)
            
            # Update AI models for all symbols
            for symbol in self._all_symbols:
                if symbol in data and data[symbol] is not None:
                    try:
                        price = float(data[symbol].close)
                        volume = float(data[symbol].volume)
                        
                        # Update return predictor
                        if symbol in self.return_predictors:
                            self.return_predictors[symbol].update(price, volume)
                        
                    except Exception as e:
                        self.logger.error(f"Data update error for {symbol}: {e}")
        
        except Exception as e:
            self.logger.error(f"OnData AI update error: {e}")
    
    def _train_ai_models(self):
        """Train AI models weekly"""
        
        self.logger.info("=" * 60)
        self.logger.info("TRAINING AI MODELS")
        self.logger.info("=" * 60)
        
        try:
            # 1. Train Volatility Predictor
            if len(self.volatility_predictor.volatility_history) >= 126:
                if not self.volatility_predictor.trained:
                    success = self.volatility_predictor.train()
                    if success:
                        self.logger.info("✓ Volatility regime predictor trained")
                        self.logger.info(f"  Data points: {len(self.volatility_predictor.volatility_history)}")
            
            # 2. Train Return Predictors
            trained_count = 0
            for symbol, predictor in self.return_predictors.items():
                if len(predictor.price_history) >= 60:
                    if not predictor.trained:
                        success = predictor.train()
                        if success:
                            trained_count += 1
            
            if trained_count > 0:
                self.logger.info(f"✓ Trained {trained_count} return predictors")
            
            # 3. Log AI Status
            self.logger.info(f"AI Status:")
            self.logger.info(f"  Volatility Predictor: {'Trained' if self.volatility_predictor.trained else 'Not trained'}")
            self.logger.info(f"  Return Predictors: {trained_count}/{len(self.return_predictors)} trained")
            self.logger.info(f"  RL Agent Q-Table size: {len(self.rl_agent.q_table)}")
            self.logger.info(f"  Adaptive Risk WR: {self.adaptive_risk.win_rate:.1%}")
            
        except Exception as e:
            self.logger.error(f"AI training error: {e}")
        
        self.logger.info("=" * 60)
    
    def _detect_market_regime_ai(self):
        """AI-Enhanced market regime detection"""
        
        try:
            # ═══════════════════════════════════════════════════════════
            # 1. PREDICT VOLATILITY REGIME
            # ═══════════════════════════════════════════════════════════
            
            if self.volatility_predictor.trained:
                self.volatility_regime = self.volatility_predictor.predict_regime()
            else:
                self.volatility_regime = "MEDIUM"
            
            # ═══════════════════════════════════════════════════════════
            # 2. TRADITIONAL REGIME DETECTION (EMA-based)
            # ═══════════════════════════════════════════════════════════
            
            if not (self.spy_ema_20.is_ready and self.spy_ema_50.is_ready):
                return
            
            spy_price = float(self.securities[self.spy].price)
            ema_20 = self.spy_ema_20.current.value
            ema_50 = self.spy_ema_50.current.value
            
            # Simple regime logic
            if spy_price > ema_50 and ema_20 > ema_50:
                traditional_regime = "BULL"
            elif spy_price < ema_50 and ema_20 < ema_50:
                traditional_regime = "BEAR"
            else:
                traditional_regime = "NEUTRAL"
            
            # ═══════════════════════════════════════════════════════════
            # 3. COMBINE TRADITIONAL + AI REGIMES
            # ═══════════════════════════════════════════════════════════
            
            previous_regime = self.market_regime
            
            # Downgrade if high volatility detected by AI
            if self.volatility_regime == "HIGH":
                if traditional_regime == "BULL":
                    self.market_regime = "NEUTRAL"
                elif traditional_regime == "NEUTRAL":
                    self.market_regime = "BEAR"
                else:
                    self.market_regime = traditional_regime
            else:
                self.market_regime = traditional_regime
            
            # Log regime changes
            if previous_regime != self.market_regime:
                self.logger.critical(f"REGIME CHANGE: {previous_regime} → {self.market_regime}")
                self.logger.info(f"Traditional: {traditional_regime}, Volatility: {self.volatility_regime}")
                self.logger.info(f"SPY: {spy_price:.2f}, EMA20: {ema_20:.2f}, EMA50: {ema_50:.2f}")
        
        except Exception as e:
            self.logger.error(f"AI regime detection error: {e}")
    
    def _evaluate_signals_ai(self):
        """AI-Enhanced signal evaluation"""
        
        if self.is_warming_up:
            return
        
        try:
            # ═══════════════════════════════════════════════════════════
            # 1. GENERATE ML PREDICTIONS
            # ═══════════════════════════════════════════════════════════
            
            predictions = {}
            for symbol in self._all_symbols:
                if symbol == self.spy:
                    continue
                
                if symbol in self.return_predictors:
                    predictor = self.return_predictors[symbol]
                    
                    # Train if not yet trained and enough data
                    if not predictor.trained and len(predictor.price_history) >= 60:
                        predictor.train()
                    
                    # Get prediction
                    if predictor.trained:
                        pred_return = predictor.predict_return()
                        predictions[symbol] = pred_return
            
            if not predictions:
                self.debug("No ML predictions available yet")
                return
            
            # ═══════════════════════════════════════════════════════════
            # 2. RANK BY PREDICTED RETURN
            # ═══════════════════════════════════════════════════════════
            
            ranked = sorted(predictions.items(), key=lambda x: x[1], reverse=True)
            
            self.debug(f"AI Predictions - Top 5:")
            for symbol, pred in ranked[:5]:
                self.debug(f"  {symbol.value}: {pred:+.3%}")
            
            # ═══════════════════════════════════════════════════════════
            # 3. FILTER & SELECT CANDIDATES
            # ═══════════════════════════════════════════════════════════
            
            candidates = []
            for symbol, predicted_return in ranked[:10]:  # Top 10
                
                # Filter 1: Positive prediction
                if predicted_return < 0.001:  # At least 0.1% predicted
                    continue
                
                # Filter 2: Check sentiment (if available)
                sentiment_signal = self.sentiment_analyzer.get_sentiment_signal(symbol)
                if sentiment_signal == "BEARISH":
                    self.debug(f"SKIP {symbol.value}: Negative sentiment")
                    continue
                
                # Filter 3: Traditional technical confirmation
                if not self._check_technical_signals(symbol):
                    continue
                
                candidates.append((symbol, predicted_return, sentiment_signal))
            
            # ═══════════════════════════════════════════════════════════
            # 4. POSITION MANAGEMENT
            # ═══════════════════════════════════════════════════════════
            
            # Exit logic (with AI-enhanced parameters)
            self._manage_positions_ai()
            
            # Entry logic
            current_positions = len([s for s in self._algo_managed_positions])
            max_positions = self._get_max_positions_for_regime()
            
            if current_positions >= max_positions:
                self.debug(f"Max positions reached: {current_positions}/{max_positions}")
                return
            
            # Enter top candidates
            for symbol, predicted_return, sentiment in candidates[:max_positions - current_positions]:
                self._enter_position_ai(symbol, predicted_return)
        
        except Exception as e:
            self.logger.error(f"AI signal evaluation error: {e}")
    
    def _manage_positions_ai(self):
        """AI-Enhanced position management"""
        
        for symbol in list(self._algo_managed_positions):
            if not self.portfolio[symbol].invested:
                self._algo_managed_positions.discard(symbol)
                continue
            
            try:
                current_price = float(self.securities[symbol].price)
                if current_price <= 0:
                    continue
                
                avg_entry = self.portfolio[symbol].average_price
                qty = abs(self.portfolio[symbol].quantity)
                pnl_pct = (current_price - avg_entry) / avg_entry
                
                # Get AI-enhanced stop loss
                stop_loss = self.adaptive_risk.get_stop_loss(self.volatility_regime)
                
                should_exit = False
                reason = ""
                
                # AI-enhanced exit conditions
                if pnl_pct <= -stop_loss:
                    should_exit, reason = True, "adaptive_stop_loss"
                elif pnl_pct >= 0.08:
                    should_exit, reason = True, "take_profit"
                elif (self.time - self.entry_time.get(symbol, self.time)) >= timedelta(days=10):
                    if pnl_pct >= 0.02:
                        should_exit, reason = True, "time_profit_lock"
                    elif pnl_pct < 0:
                        should_exit, reason = True, "time_exit_loss"
                
                # Exit if predicted return turns negative
                if symbol in self.return_predictors and self.return_predictors[symbol].trained:
                    pred = self.return_predictors[symbol].predict_return()
                    if pred < -0.005 and pnl_pct > 0.02:  # Preserve profits when prediction turns negative
                        should_exit, reason = True, "ai_signal_reversal"
                
                if should_exit:
                    self.liquidate(symbol, tag=reason)
                    
                    pnl_dollar = qty * (current_price - avg_entry)
                    
                    # Update adaptive risk manager
                    was_stop_loss = "stop" in reason.lower()
                    self.adaptive_risk.update_trade_result(pnl_pct, was_stop_loss)
                    
                    # Update RL agent (if we tracked the entry state)
                    if symbol in self.rl_state_tracking:
                        entry_state = self.rl_state_tracking[symbol]['state']
                        entry_action = self.rl_state_tracking[symbol]['action']
                        
                        reward = self.rl_agent.calculate_reward(pnl_pct)
                        current_state = self._get_rl_state()
                        
                        self.rl_agent.update_q_value(entry_state, entry_action, reward, current_state)
                        del self.rl_state_tracking[symbol]
                    
                    self.logger.info(f"EXIT {symbol.value}: {reason} | P&L: ${pnl_dollar:.2f} ({pnl_pct:+.2%})")
                    self._algo_managed_positions.discard(symbol)
            
            except Exception as e:
                self.logger.error(f"Position management error for {symbol}: {e}")
    
    def _enter_position_ai(self, symbol, predicted_return: float):
        """AI-Enhanced position entry"""
        
        try:
            if self.portfolio.cash < 4000:
                return
            
            current_price = float(self.securities[symbol].price)
            if current_price <= 0:
                return
            
            # ═══════════════════════════════════════════════════════════
            # 1. GET RL AGENT POSITION SIZE RECOMMENDATION
            # ═══════════════════════════════════════════════════════════
            
            rl_state = self._get_rl_state()
            rl_position_size = self.rl_agent.select_action(rl_state)
            
            # ═══════════════════════════════════════════════════════════
            # 2. GET ADAPTIVE RISK POSITION SIZE
            # ═══════════════════════════════════════════════════════════
            
            confidence = min(1.0, abs(predicted_return) * 50)  # Scale prediction to confidence
            adaptive_size = self.adaptive_risk.get_position_size(confidence)
            
            # ═══════════════════════════════════════════════════════════
            # 3. COMBINE SIZING RECOMMENDATIONS
            # ═══════════════════════════════════════════════════════════
            
            combined_size = (rl_position_size * 0.4 + adaptive_size * 0.6)
            
            # Calculate quantity
            available_cash = self.portfolio.cash
            target_value = available_cash * combined_size * 0.8
            
            if target_value < 4000:
                return
            
            qty = int(target_value / current_price)
            if qty <= 0:
                return
            
            # ═══════════════════════════════════════════════════════════
            # 4. EXECUTE TRADE
            # ═══════════════════════════════════════════════════════════
            
            ticket = self.market_order(symbol, qty, tag="ai_entry")
            
            if ticket:
                self.entry_time[symbol] = self.time
                self.entry_price[symbol] = current_price
                self._algo_managed_positions.add(symbol)
                
                # Track state for RL learning
                self.rl_state_tracking[symbol] = {
                    'state': rl_state,
                    'action': rl_position_size
                }
                
                self.logger.info(f"ENTRY {symbol.value}: {qty} @ ${current_price:.2f}")
                self.logger.info(f"  Predicted Return: {predicted_return:+.2%}")
                self.logger.info(f"  RL Size: {rl_position_size:.2%}, Adaptive: {adaptive_size:.2%}, Combined: {combined_size:.2%}")
                self.logger.info(f"  Stop Loss: {self.adaptive_risk.get_stop_loss(self.volatility_regime):.2%}")
        
        except Exception as e:
            self.logger.error(f"AI entry error for {symbol}: {e}")
    
    def _get_rl_state(self) -> str:
        """Get current state for RL agent"""
        portfolio_return = (self.portfolio.total_portfolio_value / self._starting_cash) - 1
        drawdown = self._calculate_current_drawdown()
        
        return self.rl_agent.get_state(
            self.market_regime,
            self.volatility_regime,
            portfolio_return,
            drawdown
        )
    
    def _calculate_current_drawdown(self) -> float:
        """Calculate current drawdown"""
        if not hasattr(self, 'peak_equity'):
            self.peak_equity = self._starting_cash
        
        current_equity = self.portfolio.total_portfolio_value
        self.peak_equity = max(self.peak_equity, current_equity)
        
        if self.peak_equity > 0:
            return (self.peak_equity - current_equity) / self.peak_equity
        return 0.0
    
    def _get_max_positions_for_regime(self) -> int:
        """Determine max positions based on regime"""
        base_max = self.config.trading.max_positions if hasattr(self.config.trading, 'max_positions') else 5
        
        if self.market_regime == "BULL" and self.volatility_regime == "LOW":
            return base_max
        elif self.market_regime == "BULL":
            return max(3, base_max - 2)
        elif self.market_regime == "NEUTRAL":
            return max(2, base_max - 3)
        else:  # BEAR
            return 1
    
    def _check_technical_signals(self, symbol) -> bool:
        """Check traditional technical indicators"""
        if symbol not in self._indicators:
            return False
        
        indicators = self._indicators[symbol]
        
        try:
            macd = indicators.get('macd')
            rsi = indicators.get('rsi')
            
            if not (macd and macd.is_ready and rsi and rsi.is_ready):
                return False
            
            # Basic technical filters
            if rsi.current.value > 70:  # Overbought
                return False
            if rsi.current.value < 30:  # Oversold (can be good for contrarian)
                return True
            if macd.current.value > macd.signal.current.value:  # MACD bullish
                return True
            
            return False
        
        except Exception as e:
            self.logger.error(f"Technical check error for {symbol}: {e}")
            return False
    
    def _daily_ai_risk_summary(self):
        """Daily AI risk metrics summary"""
        
        try:
            current_equity = self.portfolio.total_portfolio_value
            daily_return = (current_equity / self._starting_cash) - 1
            
            # Update risk metrics
            self.risk_metrics.update(daily_return)
            
            # Calculate advanced metrics
            var_95 = self.risk_metrics.calculate_var(0.95)
            cvar_95 = self.risk_metrics.calculate_cvar(0.95)
            sharpe = self.risk_metrics.calculate_sharpe_ratio()
            max_dd = self.risk_metrics.calculate_max_drawdown()
            
            # Log comprehensive summary
            self.logger.info("=" * 60)
            self.logger.info("DAILY AI RISK SUMMARY")
            self.logger.info("=" * 60)
            self.logger.info(f"Portfolio: ${current_equity:,.0f} ({daily_return:+.2%})")
            self.logger.info(f"Market Regime: {self.market_regime}")
            self.logger.info(f"Volatility Regime: {self.volatility_regime}")
            self.logger.info(f"")
            self.logger.info(f"Risk Metrics:")
            self.logger.info(f"  VaR (95%):  {var_95:.3%}")
            self.logger.info(f"  CVaR (95%): {cvar_95:.3%}")
            self.logger.info(f"  Sharpe:     {sharpe:.2f}")
            self.logger.info(f"  Max DD:     {max_dd:.3%}")
            self.logger.info(f"")
            self.logger.info(f"AI Components:")
            self.logger.info(f"  RL States Explored: {len(self.rl_agent.q_table)}")
            self.logger.info(f"  Adaptive Win Rate:  {self.adaptive_risk.win_rate:.1%}")
            self.logger.info(f"  Adaptive Stop Loss: {self.adaptive_risk.current_stop_loss:.2%}")
            self.logger.info(f"  Active Positions:   {len(self._algo_managed_positions)}")
            self.logger.info("=" * 60)
        
        except Exception as e:
            self.logger.error(f"Daily AI summary error: {e}")
    
    # ═══════════════════════════════════════════════════════════
    # HELPER METHODS (from original algorithm)
    # ═══════════════════════════════════════════════════════════
    
    def _create_indicators_for_symbol(self, symbol):
        """Create technical indicators for a symbol"""
        try:
            self._indicators[symbol] = {
                'macd': self.macd(symbol, 12, 26, 9, MovingAverageType.EXPONENTIAL, Resolution.HOUR),
                'rsi': self.rsi(symbol, 14, MovingAverageType.EXPONENTIAL, Resolution.HOUR),
                'ema_50': self.ema(symbol, 50, Resolution.HOUR)
            }
        except Exception as e:
            self.logger.error(f"Indicator creation error for {symbol}: {e}")
    
    def _warm_up_spy_regime(self):
        """Warm up SPY indicators"""
        try:
            for ind in [self.spy_ema_20, self.spy_ema_50, self.spy_rsi]:
                if ind:
                    self.warm_up_indicator(self.spy, ind, Resolution.DAILY)
        except Exception as e:
            self.logger.error(f"SPY warmup error: {e}")
