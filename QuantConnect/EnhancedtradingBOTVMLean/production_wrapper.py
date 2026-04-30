
"""
Production-Ready Trading Wrapper
Adds safety features, risk management, and monitoring to the AI algorithm
"""
from datetime import datetime, timedelta
from collections import defaultdict
from enum import Enum

class TradingState(Enum):
    """Trading system states"""
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"

class RiskManager:
    """Manages portfolio-level risk and circuit breakers"""
    
    def __init__(self, logger, config):
        self.logger = logger
        self.config = config
        self.daily_loss = 0
        self.peak_equity = 0
        self.state = TradingState.RUNNING
        self.loss_lock_time = None
    
    def check_daily_loss_limit(self, current_equity: float, starting_equity: float) -> bool:
        """Check if daily loss limit exceeded"""
        daily_loss = starting_equity - current_equity
        
        if daily_loss > self.config.risk.max_daily_loss:
            self.logger.critical(
                f"DAILY LOSS LIMIT EXCEEDED: ${daily_loss:.2f} > ${self.config.risk.max_daily_loss:.2f}"
            )
            self.state = TradingState.CIRCUIT_BREAKER
            return False
        
        return True
    
    def check_drawdown_limit(self, current_equity: float, peak_equity: float) -> bool:
        """Check if max drawdown exceeded"""
        if peak_equity > 0:
            drawdown = (peak_equity - current_equity) / peak_equity
            
            if drawdown > self.config.risk.max_drawdown_pct:
                self.logger.critical(
                    f"MAX DRAWDOWN EXCEEDED: {drawdown:.1%} > {self.config.risk.max_drawdown_pct:.1%}"
                )
                self.state = TradingState.CIRCUIT_BREAKER
                return False
        
        return True
    
    def can_trade(self) -> bool:
        """Check if trading is allowed"""
        return self.state == TradingState.RUNNING
    
    def emergency_stop(self, reason: str):
        """Emergency stop trading"""
        self.logger.critical(f"EMERGENCY STOP TRIGGERED: {reason}")
        self.state = TradingState.EMERGENCY_STOP

class OrderExecutor:
    """Safe order execution with validation and error handling"""
    
    def __init__(self, logger, config):
        self.logger = logger
        self.config = config
        self.pending_orders = {}
    
    def place_order(self, algorithm, symbol, qty: int, order_type: str = "LIMIT", 
                    limit_price: float = None, tag: str = "") -> bool:
        """Place order with safety checks"""
        
        try:
            # Validate order
            if not self._validate_order(algorithm, symbol, qty, limit_price):
                return False
            
            # Place order
            if order_type == "LIMIT":
                if limit_price is None:
                    self.logger.error(f"LIMIT order requires limit_price")
                    return False
                
                # Place limit order (5% buffer from current price for safety)
                current_price = float(algorithm.securities[symbol].price)
                safe_limit = limit_price * 0.95  # 5% margin
                
                ticket = algorithm.limit_order(symbol, qty, safe_limit, tag=tag)
            else:  # MARKET
                ticket = algorithm.market_order(symbol, qty, tag=tag)
            
            if ticket:
                self.pending_orders[symbol] = {
                    'ticket': ticket,
                    'qty': qty,
                    'time': algorithm.time,
                    'price': float(algorithm.securities[symbol].price)
                }
                self.logger.info(
                    f"Order placed: {symbol} | Qty: {qty} | Type: {order_type} | Tag: {tag}"
                )
                return True
            else:
                self.logger.error(f"Failed to place order for {symbol}")
                return False
        
        except Exception as e:
            self.logger.error(f"Order placement error for {symbol}: {e}")
            return False
    
    def _validate_order(self, algorithm, symbol, qty: int, limit_price: float = None) -> bool:
        """Validate order before placing"""
        
        # Check quantity
        if qty <= 0:
            self.logger.error(f"Invalid quantity: {qty}")
            return False
        
        # Check price
        try:
            current_price = float(algorithm.securities[symbol].price)
            if current_price <= 0:
                self.logger.error(f"Invalid price for {symbol}: {current_price}")
                return False
        except:
            self.logger.error(f"Could not get price for {symbol}")
            return False
        
        # Check buying power
        order_value = qty * current_price
        if order_value > algorithm.portfolio.cash * 0.75:
            self.logger.warning(
                f"Order would exceed 75% cash buffer: ${order_value:.2f} > ${algorithm.portfolio.cash * 0.75:.2f}"
            )
            return False
        
        return True

class PositionReconciler:
    """Reconciles algorithm positions with actual portfolio"""
    
    def __init__(self, logger):
        self.logger = logger
        self.reconciliation_history = []
    
    def reconcile(self, algorithm, algo_managed_positions: set) -> dict:
        """Check for discrepancies between tracked and actual positions"""
        
        issues = {
            'orphaned_positions': [],
            'missing_positions': [],
            'qty_mismatch': []
        }
        
        # Check for orphaned positions (not in tracking but in portfolio)
        for symbol in algorithm.portfolio.positions:
            if algorithm.portfolio[symbol].invested:
                if symbol not in algo_managed_positions:
                    issues['orphaned_positions'].append({
                        'symbol': symbol.value,
                        'qty': algorithm.portfolio[symbol].quantity
                    })
                    self.logger.warning(f"Orphaned position found: {symbol.value}")
        
        # Check for missing positions (in tracking but not in portfolio)
        for symbol in algo_managed_positions:
            if symbol not in algorithm.portfolio or not algorithm.portfolio[symbol].invested:
                issues['missing_positions'].append(symbol.value)
                self.logger.warning(f"Missing position: {symbol.value}")
        
        return issues

class ProductionAlgorithmWrapper:
    """
    Production wrapper that adds safety features to the base algorithm
    Manages risk, logging, monitoring, and order execution
    """
    
    def __init__(self, base_algorithm, config, logger, email_alerter):
        self.base_algo = base_algorithm
        self.config = config
        self.logger = logger
        self.email_alerter = email_alerter
        
        # Initialize managers
        self.risk_manager = RiskManager(logger, config)
        self.order_executor = OrderExecutor(logger, config)
        self.position_reconciler = PositionReconciler(logger)
        
        # Tracking
        self.daily_pnl_start = None
        self.daily_pnl_peak = None
        self.last_reconciliation = None
        self.trades_today = 0
    
    def on_initialize(self):
        """Called on algorithm initialization"""
        try:
            self.logger.info("=" * 60)
            self.logger.info("PRODUCTION ALGORITHM INITIALIZED")
            self.logger.info(f"Mode: {'LIVE' if self.config.live_trading else 'PAPER'}")
            self.logger.info(f"Starting Capital: ${self.config.starting_capital:.2f}")
            self.logger.info(f"Max Daily Loss: ${self.config.risk.max_daily_loss:.2f}")
            self.logger.info(f"Max Drawdown: {self.config.risk.max_drawdown_pct:.1%}")
            self.logger.info("=" * 60)
        except Exception as e:
            self.logger.error(f"Initialization error: {e}")
    
    def on_data(self, algorithm):
        """Called on each data update - main trading loop"""
        try:
            # Daily risk checks
            if self._should_check_daily_risk(algorithm):
                current_equity = algorithm.portfolio.total_portfolio_value
                starting_equity = self.config.starting_capital
                
                # Check circuit breakers
                if not self.risk_manager.check_daily_loss_limit(current_equity, starting_equity):
                    self.logger.critical("CIRCUIT BREAKER: Daily loss limit triggered")
                    self.email_alerter.send_alert(
                        "Circuit Breaker Triggered",
                        f"Daily loss limit exceeded. Portfolio paused.",
                        level="CRITICAL"
                    )
                    return
                
                # Check drawdown
                if self.daily_pnl_peak is None:
                    self.daily_pnl_peak = current_equity
                else:
                    self.daily_pnl_peak = max(self.daily_pnl_peak, current_equity)
                
                if not self.risk_manager.check_drawdown_limit(current_equity, self.daily_pnl_peak):
                    self.logger.critical("CIRCUIT BREAKER: Max drawdown triggered")
                    self.email_alerter.send_alert(
                        "Circuit Breaker Triggered",
                        f"Max drawdown limit exceeded. Portfolio paused.",
                        level="CRITICAL"
                    )
                    return
            
            # Position reconciliation
            if self._should_reconcile(algorithm):
                issues = self.position_reconciler.reconcile(
                    algorithm, 
                    self.base_algo._algo_managed_positions
                )
                
                if issues['orphaned_positions'] or issues['missing_positions']:
                    self.logger.warning(f"Position reconciliation issues: {issues}")
                    self.email_alerter.send_alert(
                        "Position Reconciliation Issue",
                        f"Orphaned: {issues['orphaned_positions']}\nMissing: {issues['missing_positions']}",
                        level="WARNING"
                    )
                
                self.last_reconciliation = algorithm.time
            
            # Check if can trade
            if not self.risk_manager.can_trade():
                self.logger.warning(f"Trading disabled due to {self.risk_manager.state}")
                return
            
            # Call base algorithm logic
            self.base_algo._evaluate_signals()
        
        except Exception as e:
            self.logger.error(f"Data processing error: {e}")
            self.email_alerter.send_alert(
                "Algorithm Error",
                f"Error in data processing: {e}",
                level="ERROR"
            )
    
    def _should_check_daily_risk(self, algorithm) -> bool:
        """Check if it's time for daily risk assessment"""
        if self.daily_pnl_start is None:
            self.daily_pnl_start = algorithm.time
            return True
        
        # Check once per hour
        if (algorithm.time - self.daily_pnl_start).total_seconds() > 3600:
            self.daily_pnl_start = algorithm.time
            return True
        
        return False
    
    def _should_reconcile(self, algorithm) -> bool:
        """Check if it's time for position reconciliation"""
        if self.last_reconciliation is None:
            return True
        
        # Reconcile every 6 hours
        if (algorithm.time - self.last_reconciliation).total_seconds() > 6 * 3600:
            return True
        
        return False
    
    def place_order(self, algorithm, symbol, qty: int, order_type: str = "LIMIT", 
                   limit_price: float = None) -> bool:
        """Wrapper for safe order placement"""
        return self.order_executor.place_order(
            algorithm, symbol, qty, order_type, limit_price
        )
    
    def emergency_stop(self, reason: str):
        """Emergency stop all trading"""
        self.risk_manager.emergency_stop(reason)
        self.email_alerter.send_alert(
            "Emergency Stop",
            f"Emergency stop triggered: {reason}",
            level="CRITICAL"
        )