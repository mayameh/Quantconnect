
"""
Production-Ready Trading Wrapper
Adds safety features, risk management, and monitoring to the AI algorithm
"""
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