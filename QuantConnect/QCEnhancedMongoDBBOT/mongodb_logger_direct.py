from AlgorithmImports import *
import json
from datetime import datetime
from collections import deque
from typing import Dict, List, Optional, Any

# Try to import pymongo for direct MongoDB connection
try:
    from pymongo import MongoClient
    from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
    PYMONGO_AVAILABLE = True
except ImportError:
    PYMONGO_AVAILABLE = False

try:
    from mongodb_config import MongoDBConfig
    HAS_CONFIG_FILE = True
except ImportError:
    HAS_CONFIG_FILE = False


class MongoDBLogger:
    """
    MongoDB Atlas logger for QuantConnect algorithms.
    Connects directly to MongoDB Atlas (no REST API needed).
    """
    
    def __init__(self, algorithm, logger, config):
        """
        Initialize MongoDB logger
        
        Args:
            algorithm: QCAlgorithm instance
            logger: ProductionLogger instance
            config: BOT_Config instance
        """
        self.algorithm = algorithm
        self.logger = logger
        self.config = config
        self.client = None
        self.db = None
        
        # Get MongoDB connection string from parameters
        self.mongo_uri = algorithm.get_parameter("mongodb_uri", "")
        self.database_name = algorithm.get_parameter("mongodb_database", "quantconnect")
        
        # If not in parameters, try config file
        if not self.mongo_uri and HAS_CONFIG_FILE:
            mongo_config = MongoDBConfig.get_config()
            if mongo_config.get('enabled', False):
                self.mongo_uri = mongo_config.get('uri', '')
                self.database_name = mongo_config.get('database', 'quantconnect')
        
        # Check if we have pymongo and credentials
        self.enabled = PYMONGO_AVAILABLE and bool(self.mongo_uri)
        
        # Batch queues for efficient writes
        self.trade_batch = deque(maxlen=50)
        self.metric_batch = deque(maxlen=100)
        self.batch_size = 10
        
        # Session tracking
        self.session_id = f"{algorithm.project_id}_{int(datetime.now().timestamp())}"
        self.algorithm_id = str(algorithm.project_id)
        
        # Debug logging
        self.logger.info("=" * 60)
        self.logger.info("MongoDB Logger Configuration:")
        self.logger.info(f"  PyMongo Available: {PYMONGO_AVAILABLE}")
        self.logger.info(f"  MongoDB URI: {'SET' if self.mongo_uri else 'NOT SET'}")
        self.logger.info(f"  Database: {self.database_name}")
        self.logger.info(f"  Enabled: {self.enabled}")
        self.logger.info(f"  Session ID: {self.session_id}")
        self.logger.info("=" * 60)
        
        if self.enabled:
            self.algorithm.debug(f"✅ MongoDB ENABLED - Direct Connection")
            self.algorithm.debug(f"   Session ID: {self.session_id}")
            self._connect_to_mongodb()
            if self.db:
                self._log_session_start()
        else:
            if not PYMONGO_AVAILABLE:
                self.algorithm.debug("❌ MongoDB DISABLED - PyMongo not available")
            else:
                self.algorithm.debug("❌ MongoDB DISABLED - No connection string configured")
            self.algorithm.debug("   To enable: Set mongodb_uri parameter in Project Settings")
            self.logger.warning("MongoDB logger disabled")
    
    def _connect_to_mongodb(self) -> bool:
        """Connect to MongoDB Atlas"""
        try:
            self.algorithm.debug("Connecting to MongoDB Atlas...")
            self.client = MongoClient(self.mongo_uri, serverSelectionTimeoutMS=5000)
            # Test connection
            self.client.admin.command('ping')
            self.db = self.client[self.database_name]
            self.algorithm.debug("✅ Connected to MongoDB Atlas")
            self.logger.info("Connected to MongoDB Atlas")
            return True
        except ServerSelectionTimeoutError as e:
            self.algorithm.debug(f"❌ MongoDB connection timeout: {str(e)[:100]}")
            self.logger.error(f"MongoDB connection timeout: {e}")
            self.enabled = False
            return False
        except Exception as e:
            self.algorithm.debug(f"❌ MongoDB connection failed: {str(e)[:100]}")
            self.logger.error(f"MongoDB connection failed: {e}")
            self.enabled = False
            return False
    
    def _log_session_start(self) -> None:
        """Log algorithm session start"""
        if not self.enabled or not self.db:
            return
        
        try:
            self.algorithm.debug("Logging session start to MongoDB...")
            
            session_doc = {
                "session_id": self.session_id,
                "algorithm_id": self.algorithm_id,
                "start_time": datetime.utcnow(),
                "start_date": str(self.algorithm.start_date),
                "mode": "LIVE" if self.algorithm.live_mode else "BACKTEST",
                "starting_capital": float(self.algorithm.portfolio.cash),
                "status": "RUNNING"
            }
            
            result = self.db.sessions.insert_one(session_doc)
            self.algorithm.debug(f"✅ Session logged to MongoDB")
            self.logger.info(f"Session logged: {self.session_id}")
            
        except Exception as e:
            self.algorithm.debug(f"❌ Session logging failed: {str(e)[:100]}")
            self.logger.error(f"Session start logging error: {e}")
    
    def log_trade(self, symbol: Symbol, order_event, entry_price: float = None, 
                  entry_time: datetime = None, reason: str = "") -> None:
        """Log a trade (entry or exit)"""
        if not self.enabled or not self.db:
            return
        
        try:
            is_entry = order_event.direction == OrderDirection.BUY
            trade_type = "ENTRY" if is_entry else "EXIT"
            
            # Calculate P&L for exits
            pnl = None
            pnl_pct = None
            if not is_entry and entry_price:
                pnl = float(order_event.fill_quantity * (order_event.fill_price - entry_price))
                pnl_pct = float((order_event.fill_price - entry_price) / entry_price)
            
            holding_period = None
            if not is_entry and entry_time:
                holding_period = str(self.algorithm.time - entry_time)
            
            trade_doc = {
                "session_id": self.session_id,
                "symbol": str(symbol.value),
                "trade_type": trade_type,
                "direction": "BUY" if is_entry else "SELL",
                "quantity": float(order_event.fill_quantity),
                "fill_price": float(order_event.fill_price),
                "fill_time": datetime.utcnow(),
                "order_id": int(order_event.order_id),
                "trade_value": float(order_event.fill_quantity * order_event.fill_price),
                "reason": reason,
                "entry_price": float(entry_price) if entry_price else None,
                "pnl": float(pnl) if pnl is not None else None,
                "pnl_pct": float(pnl_pct) if pnl_pct is not None else None,
                "holding_period": holding_period,
                "timestamp": datetime.utcnow()
            }
            
            self.trade_batch.append(trade_doc)
            
            # Flush if batch full
            if len(self.trade_batch) >= self.batch_size:
                self._flush_trade_batch()
                
        except Exception as e:
            self.logger.error(f"Trade logging error: {e}")
    
    def _flush_trade_batch(self) -> None:
        """Flush batched trades to MongoDB"""
        if not self.trade_batch or not self.db:
            return
        
        try:
            trades = list(self.trade_batch)
            self.db.trades.insert_many(trades)
            self.algorithm.debug(f"✅ Flushed {len(trades)} trades to MongoDB")
            self.trade_batch.clear()
            
        except Exception as e:
            self.logger.error(f"Trade batch flush error: {e}")
    
    def log_performance_snapshot(self, positions: List[Dict] = None, 
                                market_regime: str = None) -> None:
        """Log current portfolio performance snapshot"""
        if not self.enabled or not self.db:
            return
        
        try:
            portfolio = self.algorithm.portfolio
            
            total_value = float(portfolio.total_portfolio_value)
            cash = float(portfolio.cash)
            invested_value = total_value - cash
            total_positions = sum(1 for h in portfolio.values() if h.invested)
            
            starting_cash = self.config.general.starting_capital
            total_return = (total_value - starting_cash) / starting_cash
            
            perf_doc = {
                "session_id": self.session_id,
                "date": self.algorithm.time.date(),
                "time": str(self.algorithm.time.time()),
                "portfolio_value": total_value,
                "cash": cash,
                "invested_value": invested_value,
                "total_positions": total_positions,
                "total_return": float(total_return),
                "market_regime": market_regime,
                "positions": positions or [],
                "timestamp": datetime.utcnow()
            }
            
            self.metric_batch.append(perf_doc)
            
            if len(self.metric_batch) >= 20:
                self._flush_performance_batch()
                
        except Exception as e:
            self.logger.error(f"Performance logging error: {e}")
    
    def _flush_performance_batch(self) -> None:
        """Flush batched performance metrics to MongoDB"""
        if not self.metric_batch or not self.db:
            return
        
        try:
            metrics = list(self.metric_batch)
            self.db.performance.insert_many(metrics)
            self.algorithm.debug(f"✅ Flushed {len(metrics)} performance snapshots to MongoDB")
            self.metric_batch.clear()
            
        except Exception as e:
            self.logger.error(f"Performance batch flush error: {e}")
    
    def log_signal_evaluation(self, symbol: Symbol, indicators: Dict, 
                             signal_strength: float = None, action: str = "") -> None:
        """Log signal evaluation for analysis"""
        if not self.enabled or not self.db:
            return
        
        try:
            signal_doc = {
                "session_id": self.session_id,
                "symbol": str(symbol.value),
                "timestamp": datetime.utcnow(),
                "price": float(self.algorithm.securities[symbol].price),
                "indicators": {k: float(v) if v is not None else None 
                             for k, v in indicators.items()},
                "signal_strength": float(signal_strength) if signal_strength else None,
                "action": action
            }
            
            self.db.signals.insert_one(signal_doc)
            
        except Exception as e:
            self.logger.error(f"Signal logging error: {e}")
    
    def log_risk_event(self, event_type: str, details: Dict) -> None:
        """Log risk management events"""
        if not self.enabled or not self.db:
            return
        
        try:
            risk_doc = {
                "session_id": self.session_id,
                "event_type": event_type,
                "timestamp": datetime.utcnow(),
                "portfolio_value": float(self.algorithm.portfolio.total_portfolio_value),
                "details": details
            }
            
            self.db.risk_events.insert_one(risk_doc)
            
        except Exception as e:
            self.logger.error(f"Risk event logging error: {e}")
    
    def flush_all_batches(self) -> None:
        """Flush all pending batches to MongoDB"""
        self._flush_trade_batch()
        self._flush_performance_batch()
    
    def log_session_end(self, final_stats: Dict) -> None:
        """Log algorithm session end with final statistics"""
        if not self.enabled or not self.db:
            return
        
        try:
            self.flush_all_batches()
            
            self.db.sessions.update_one(
                {"session_id": self.session_id},
                {
                    "$set": {
                        "end_time": datetime.utcnow(),
                        "status": "COMPLETED",
                        "final_value": float(self.algorithm.portfolio.total_portfolio_value),
                        "final_stats": final_stats
                    }
                }
            )
            
            self.algorithm.debug(f"✅ Session ended in MongoDB")
            self.logger.info(f"Session ended: {self.session_id}")
            
        except Exception as e:
            self.logger.error(f"Session end logging error: {e}")
        
        finally:
            if self.client:
                self.client.close()