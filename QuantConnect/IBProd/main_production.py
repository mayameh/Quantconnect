"""
Production-Ready AI-Enhanced Trading Algorithm
Integrates main algorithm with safety features, monitoring, and risk management
"""

from AlgorithmImports import *
from datetime import datetime, timedelta
from production_config import BOT_Config
from production_wrapper import ProductionAlgorithmWrapper, RiskManager, OrderExecutor
from production_config import ProductionLogger, EmailAlerter, PerformanceTracker
from collections import defaultdict, deque

class MayankAlgo_Production(QCAlgorithm):
    """
    Production-ready version of AI-Enhanced algorithm with:
    - Risk management & circuit breakers
    - Order execution safety
    - Position reconciliation
    - Comprehensive logging & alerting
    - Emergency stop capability
    """
    
    def initialize(self) -> None:
        """Initialize algorithm with production safety features"""
        
        # Configuration
        self.config = BOT_Config()
        
        # Initialize logging
        self.logger = ProductionLogger(self.config)
        self.logger.info("Initializing Production Algorithm")
        
        # Initialize email alerter
        self.email_alerter = EmailAlerter(self.config, self.logger)
        self.email_alerter.configure(
            smtp_server=self.config.email.smtp_server,
            smtp_port=self.config.email.smtp_port,
            email=self.config.email.sender_email,
            password=self.config.email.sender_password
        )
        
        # Initialize performance tracker
        self.perf_tracker = PerformanceTracker(self.logger)
        
        # ============================================================
        # BACKTEST/LIVE CONFIGURATION
        # ============================================================
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
        
        # ============================================================
        # CORE ALGORITHM INITIALIZATION
        # ============================================================
        
        # Core tracking
        self._core_symbols = []
        self._dynamic_symbols = set()
        self._all_symbols = set()
        self._algo_managed_positions = set()
        
        self._indicators = {}
        self._indicator_ready_time = {}
        
        self.entry_time = {}
        self.entry_price = {}
        self.highest_price = {}
        
        # Trading state
        self.market_regime = "NEUTRAL"
        self.weekly_trades_count = defaultdict(int)
        self.last_trade_date = None
        self._symbol_last_trade = {}
        self._last_eval_time = None
        self._last_universe_selection = None
        
        # AI enhancements
        self.trade_history = deque(maxlen=50)
        self.winning_trades = deque(maxlen=30)
        self.losing_trades = deque(maxlen=20)
        self.symbol_performance = defaultdict(lambda: {
            'trades': 0,
            'wins': 0,
            'total_pnl': 0.0,
            'consecutive_losses': 0,
            'win_rate': 0.0
        })
        
        # ============================================================
        # SAFETY MANAGERS
        # ============================================================
        self.risk_manager = RiskManager(self.logger, self.config)
        self.order_executor = OrderExecutor(self.logger, self.config)
        
        # Track peak equity for drawdown
        self.peak_equity = self._starting_cash
        self.daily_start_time = self.time
        
        # ============================================================
        # ADD CORE SYMBOLS
        # ============================================================
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
        
        # ============================================================
        # ADD MARKET REGIME TRACKER (SPY)
        # ============================================================
        try:
            self.spy = self.add_equity("SPY", Resolution.MINUTE, "USA").symbol
            self.spy_ema_20 = self.ema(self.spy, 20, Resolution.DAILY)
            self.spy_ema_50 = self.ema(self.spy, 50, Resolution.DAILY)
            self.spy_rsi = self.rsi(self.spy, 14, MovingAverageType.EXPONENTIAL, Resolution.DAILY)
            self.logger.info("Added market regime tracker (SPY)")
        except Exception as e:
            self.logger.error(f"Failed to add SPY: {e}")
        
        # ============================================================
        # ADD DYNAMIC UNIVERSE
        # ============================================================
        try:
            self.add_universe(
                lambda f: self._select_coarse(f),
                lambda f: self._select_fine(f)
            )
            self.logger.info("Added dynamic universe selection")
        except Exception as e:
            self.logger.error(f"Failed to add universe: {e}")
        
        # ============================================================
        # SCHEDULING
        # ============================================================
        self.schedule.on(
            self.date_rules.every_day(),
            self.time_rules.every(timedelta(minutes=self.config.trading.eval_interval_minutes)),
            self._evaluate_signals_safe
        )
        
        self.schedule.on(
            self.date_rules.every_day(),
            self.time_rules.at(9, 35),
            self._detect_market_regime
        )
        
        self.schedule.on(
            self.date_rules.every_day(),
            self.time_rules.at(16, 0),  # Before market close
            self._daily_risk_summary
        )
        
        self.set_warm_up(200, Resolution.HOUR)
        
        # Log initialization complete
        self.logger.info("=" * 60)
        self.logger.info("PRODUCTION ALGORITHM INITIALIZED SUCCESSFULLY")
        self.logger.info(f"Mode: {self.config.general.mode}")
        self.logger.info(f"Starting Capital: ${self._starting_cash:,.2f}")
        self.logger.info(f"Max Daily Loss: ${self.config.risk.max_daily_loss:,.2f}")
        self.logger.info(f"Max Drawdown: {self.config.risk.max_drawdown_pct:.1%}")
        self.logger.info("=" * 60)
    
    def _evaluate_signals_safe(self) -> None:
        """Main trading logic with safety wrappers"""
        try:
            # Check if trading is allowed
            if not self.risk_manager.can_trade():
                self.logger.warning(f"Trading disabled: {self.risk_manager.state}")
                return
            
            # Check risk limits
            current_equity = self.portfolio.total_portfolio_value
            
            # Daily loss check
            daily_loss = self._starting_cash - current_equity
            if daily_loss > self.config.risk.max_daily_loss:
                self.logger.critical(f"DAILY LOSS LIMIT HIT: ${daily_loss:,.2f}")
                self.risk_manager.emergency_stop("Daily loss limit exceeded")
                self.email_alerter.send_alert(
                    "Circuit Breaker: Daily Loss Limit",
                    f"Daily loss: ${daily_loss:,.2f} exceeds limit of ${self.config.risk.max_daily_loss:,.2f}",
                    level="CRITICAL"
                )
                return
            
            # Drawdown check
            if current_equity > self.peak_equity:
                self.peak_equity = current_equity
            
            drawdown = (self.peak_equity - current_equity) / self.peak_equity if self.peak_equity > 0 else 0
            if drawdown > self.config.risk.max_drawdown_pct:
                self.logger.critical(f"DRAWDOWN LIMIT HIT: {drawdown:.1%}")
                self.risk_manager.emergency_stop(f"Drawdown limit exceeded: {drawdown:.1%}")
                self.email_alerter.send_alert(
                    "Circuit Breaker: Drawdown Limit",
                    f"Current drawdown: {drawdown:.1%} exceeds limit of {self.config.risk.max_drawdown_pct:.1%}",
                    level="CRITICAL"
                )
                return
            
            # Call base algorithm logic
            self._evaluate_signals()
        
        except Exception as e:
            self.logger.error(f"Error in signal evaluation: {e}")
            self.email_alerter.send_alert(
                "Algorithm Error",
                f"Signal evaluation error: {str(e)}",
                level="ERROR"
            )
    
    def _daily_risk_summary(self) -> None:
        """Daily risk and performance summary"""
        try:
            current_equity = self.portfolio.total_portfolio_value
            daily_pnl = current_equity - self._starting_cash
            daily_return = daily_pnl / self._starting_cash
            
            self.logger.info("=" * 60)
            self.logger.info("DAILY SUMMARY")
            self.logger.info(f"Equity: ${current_equity:,.2f}")
            self.logger.info(f"Daily P&L: ${daily_pnl:,.2f} ({daily_return:.2%})")
            self.logger.info(f"Peak Equity: ${self.peak_equity:,.2f}")
            self.logger.info(f"Drawdown: {(self.peak_equity - current_equity) / self.peak_equity:.2%}")
            self.logger.info(f"Positions: {len([s for s in self._all_symbols if self.portfolio[s].invested])}")
            self.logger.info("=" * 60)
            
            # Email daily summary if configured
            if self.config.monitoring.send_daily_summary:
                self.email_alerter.send_alert(
                    f"Daily Summary - {self.time.date()}",
                    f"""Equity: ${current_equity:,.2f}
Daily P&L: ${daily_pnl:,.2f} ({daily_return:.2%})
Drawdown: {(self.peak_equity - current_equity) / self.peak_equity:.2%}""",
                    level="INFO"
                )
        except Exception as e:
            self.logger.error(f"Error in daily summary: {e}")
    
    # ============================================================
    # PLACEHOLDER METHODS (implement from main.py)
    # ============================================================
    
    def _evaluate_signals(self) -> None:
        """Base algorithm signal evaluation - implement from main.py"""
        pass
    
    def _detect_market_regime(self) -> None:
        """Market regime detection"""
        pass
    
    def _select_coarse(self, coarse) -> list:
        """Coarse universe selection"""
        return []
    
    def _select_fine(self, fine) -> list:
        """Fine universe selection"""
        return self._core_symbols
    
    def _create_indicators_for_symbol(self, symbol) -> None:
        """Create indicators for symbol"""
        pass
    
    def on_data(self, data: Slice) -> None:
        pass
