from AlgorithmImports import *
import json
from datetime import datetime
from collections import deque
from typing import Dict, List, Optional, Any

try:
    from mongodb_config import MongoDBConfig
    HAS_CONFIG_FILE = True
except ImportError:
    HAS_CONFIG_FILE = False


class MongoDBLogger:
    """
    MongoDB Atlas logger for QuantConnect algorithms.
    Handles trade logging, performance tracking, and portfolio snapshots.
    
    Configuration options (in priority order):
    1. QuantConnect UI Parameters (recommended for production)
    2. mongodb_config.py file (for testing)
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
        
        # Try to get configuration from QuantConnect UI parameters first
        self.api_base_url = algorithm.get_parameter("mongodb_api_url", "")
        self.api_key = algorithm.get_parameter("mongodb_api_key", "")
        self.database_name = algorithm.get_parameter("mongodb_database", "quantconnect")
        
        # If not found in UI parameters, try config file
        if not self.api_base_url and HAS_CONFIG_FILE:
            mongo_config = MongoDBConfig.get_config()
            if mongo_config.get('enabled', False):
                self.api_base_url = mongo_config.get('api_url', '')
                self.api_key = mongo_config.get('api_key', '')
                self.database_name = mongo_config.get('database', 'quantconnect')
                self.logger.info("MongoDB config loaded from mongodb_config.py")
        
        self.enabled = bool(self.api_base_url and self.api_key)
        
        # Batch queues for efficient API calls
        self.trade_batch = deque(maxlen=50)
        self.metric_batch = deque(maxlen=100)
        self.batch_size = 10
        
        # Session tracking
        self.session_id = f"{algorithm.project_id}_{int(datetime.now().timestamp())}"
        self.algorithm_id = str(algorithm.project_id)
        
        # Debug logging
        self.logger.info("=" * 60)
        self.logger.info("MongoDB Logger Configuration:")
        self.logger.info(f"  API URL: {self.api_base_url if self.api_base_url else 'NOT SET'}")
        self.logger.info(f"  API Key: {'SET' if self.api_key else 'NOT SET'} (length: {len(self.api_key) if self.api_key else 0})")
        self.logger.info(f"  Database: {self.database_name}")
        self.logger.info(f"  Enabled: {self.enabled}")
        self.logger.info(f"  Session ID: {self.session_id}")
        self.logger.info("=" * 60)
        
        if self.enabled:
            self.algorithm.debug(f"✅ MongoDB ENABLED - URL: {self.api_base_url[:50]}...")
            self.algorithm.debug(f"   Session ID: {self.session_id}")
            self.logger.info("MongoDB logger initialized - attempting session start...")
            self._log_session_start()
        else:
            self.algorithm.debug("❌ MongoDB DISABLED - No API credentials configured")
            self.algorithm.debug("   To enable: Set mongodb_api_url, mongodb_api_key parameters in Project Settings")
            self.logger.warning("MongoDB logger disabled - missing API credentials")
    
    def _make_api_request(self, endpoint: str, payload: Dict, method: str = "POST") -> bool:
        """
        Make API request to MongoDB REST API endpoint
        
        Args:
            endpoint: API endpoint path
            payload: Request payload
            method: HTTP method (POST, PUT, etc.)
            
        Returns:
            bool: Success status
        """
        if not self.enabled:
            self.logger.debug(f"MongoDB request skipped (disabled): {endpoint}")
            return False
        
        # Only send webhooks in LIVE mode (notify.web doesn't work in backtests)
        if not self.algorithm.live_mode:
            self.logger.debug(f"MongoDB request skipped (backtest mode): {endpoint}")
            return True  # Return True so batching logic continues normally
            
        try:
            url = f"{self.api_base_url}/{endpoint}"
            
            # Add metadata and authentication to payload
            # Since notify.web() doesn't support custom headers, we include auth in payload
            full_payload = {
                **payload,
                "api_key": self.api_key,  # Auth via payload instead of header
                "metadata": {
                    "algorithm_id": self.algorithm_id,
                    "session_id": self.session_id,
                    "mode": "LIVE" if self.algorithm.live_mode else "BACKTEST",
                    "timestamp": str(self.algorithm.utc_time)
                }
            }
            
            self.logger.info(f"MongoDB API request to: {url}")
            self.logger.debug(f"  Endpoint: {endpoint}")
            self.logger.debug(f"  Collection: {payload.get('collection', 'unknown')}")
            
            # DEBUG: Show in backtest logs
            self.algorithm.debug(f"MongoDB API Call: {endpoint}")
            self.algorithm.debug(f"   URL: {url}")
            self.algorithm.debug(f"   Collection: {payload.get('collection', 'unknown')}")

            # notify.web() only accepts (url, data) - no custom headers
            self.algorithm.notify.web(url, json.dumps(full_payload))
            self.logger.info(f"MongoDB API request sent successfully to {endpoint}")
            self.algorithm.debug(f"API request sent to {endpoint}")
            return True
            
        except Exception as e:
            self.logger.error(f"MongoDB API request error ({endpoint}): {e}")
            self.logger.error(f"  URL: {url}")
            self.algorithm.debug(f"API request failed: {endpoint} - {str(e)}")
            return False
    
    def _log_session_start(self) -> None:
        """Log algorithm session start"""
        try:
            self.logger.info("Attempting to log session start to MongoDB...")
            
            payload = {
                "collection": "sessions",
                "document": {
                    "session_id": self.session_id,
                    "algorithm_id": self.algorithm_id,
                    "start_time": str(self.algorithm.utc_time),
                    "start_date": str(self.algorithm.start_date),
                    "mode": "LIVE" if self.algorithm.live_mode else "BACKTEST",
                    "starting_capital": float(self.algorithm.portfolio.cash),
                    "status": "RUNNING"
                }
            }
            
            success = self._make_api_request("insert", payload)
            if success:
                self.logger.info(f"MongoDB session logged: {self.session_id}")
            else:
                self.logger.error(f"Failed to log MongoDB session")
            
        except Exception as e:
            self.logger.error(f"Session start logging error: {e}")
    
    def log_trade(self, symbol: Symbol, order_event, entry_price: float = None, 
                  entry_time: datetime = None, reason: str = "") -> None:
        """
        Log a trade (entry or exit)
        
        Args:
            symbol: Security symbol
            order_event: OrderEvent from on_order_event
            entry_price: Original entry price (for exits)
            entry_time: Original entry time (for exits)
            reason: Trade reason/tag
        """
        if not self.enabled:
            self.logger.debug(f"Trade logging skipped (MongoDB disabled): {symbol.value}")
            return
            
        self.logger.info(f"Logging trade: {symbol.value} - {reason}")
            
        try:
            # Determine trade type
            is_entry = order_event.direction == OrderDirection.BUY
            trade_type = "ENTRY" if is_entry else "EXIT"
            
            # Get order type from Order object (OrderEvent doesn't have order_type)
            order_type = "Unknown"
            try:
                order = self.algorithm.transactions.get_order_by_id(order_event.order_id)
                if order:
                    order_type = str(order.type)
            except:
                pass
            
            # Calculate P&L for exits
            pnl = None
            pnl_pct = None
            if not is_entry and entry_price:
                pnl = float(order_event.fill_quantity * (order_event.fill_price - entry_price))
                pnl_pct = float((order_event.fill_price - entry_price) / entry_price)
            
            # Calculate holding period for exits
            holding_period = None
            if not is_entry and entry_time:
                holding_period = str(self.algorithm.time - entry_time)
            
            trade_doc = {
                "symbol": str(symbol.value),
                "trade_type": trade_type,
                "direction": "BUY" if is_entry else "SELL",
                "quantity": float(order_event.fill_quantity),
                "fill_price": float(order_event.fill_price),
                "fill_time": str(self.algorithm.utc_time),
                "order_id": int(order_event.order_id),
                "order_type": order_type,
                "trade_value": float(order_event.fill_quantity * order_event.fill_price),
                "reason": reason,
                "entry_price": float(entry_price) if entry_price else None,
                "entry_time": str(entry_time) if entry_time else None,
                "pnl": float(pnl) if pnl is not None else None,
                "pnl_pct": float(pnl_pct) if pnl_pct is not None else None,
                "holding_period": holding_period
            }
            
            self.trade_batch.append(trade_doc)
            self.logger.info(f"Trade added to batch. Batch size: {len(self.trade_batch)}/{self.batch_size}")
            
            # Send batch if size reached
            if len(self.trade_batch) >= self.batch_size:
                self.logger.info(f"Trade batch size reached ({self.batch_size}), flushing...")
                self._flush_trade_batch()
                
        except Exception as e:
            self.logger.error(f"Trade logging error: {e}")
    
    def _flush_trade_batch(self) -> None:
        """Flush batched trades to MongoDB"""
        if not self.trade_batch:
            self.logger.debug("Trade batch flush called but batch is empty")
            return
            
        try:
            trades = list(self.trade_batch)
            self.logger.info(f"Flushing {len(trades)} trades to MongoDB...")
            
            payload = {
                "collection": "trades",
                "documents": trades
            }
            
            if self._make_api_request("insert_many", payload):
                self.trade_batch.clear()
                self.logger.info(f"Successfully flushed {len(trades)} trades to MongoDB")
            else:
                self.logger.error(f"Failed to flush trades to MongoDB")
                
        except Exception as e:
            self.logger.error(f"Trade batch flush error: {e}")
    
    def log_performance_snapshot(self, positions: List[Dict] = None, 
                                market_regime: str = None) -> None:
        """
        Log current portfolio performance snapshot
        
        Args:
            positions: List of current position dicts
            market_regime: Current market regime
        """
        if not self.enabled:
            return
            
        try:
            portfolio = self.algorithm.portfolio
            
            # Calculate metrics
            total_value = float(portfolio.total_portfolio_value)
            cash = float(portfolio.cash)
            invested_value = total_value - cash
            
            # Count positions
            total_positions = sum(1 for h in portfolio.values() if h.invested)
            
            # Calculate returns
            starting_cash = self.config.general.starting_capital
            total_return = (total_value - starting_cash) / starting_cash
            
            perf_doc = {
                "date": str(self.algorithm.time.date()),
                "time": str(self.algorithm.time.time()),
                "portfolio_value": total_value,
                "cash": cash,
                "invested_value": invested_value,
                "total_positions": total_positions,
                "total_return": float(total_return),
                "total_return_pct": float(total_return * 100),
                "market_regime": market_regime,
                "positions": positions or []
            }
            
            self.metric_batch.append(perf_doc)
            
            # Send batch if size reached
            if len(self.metric_batch) >= 20:
                self._flush_performance_batch()
                
        except Exception as e:
            self.logger.error(f"Performance logging error: {e}")
    
    def _flush_performance_batch(self) -> None:
        """Flush batched performance metrics to MongoDB"""
        if not self.metric_batch:
            return
            
        try:
            metrics = list(self.metric_batch)
            
            payload = {
                "collection": "performance",
                "documents": metrics
            }
            
            if self._make_api_request("insert_many", payload):
                self.metric_batch.clear()
                self.logger.info(f"Flushed {len(metrics)} performance snapshots to MongoDB")
                
        except Exception as e:
            self.logger.error(f"Performance batch flush error: {e}")
    
    def log_signal_evaluation(self, symbol: Symbol, indicators: Dict, 
                             signal_strength: float, action: str) -> None:
        """
        Log signal evaluation for analysis
        
        Args:
            symbol: Security symbol
            indicators: Dict of indicator values
            signal_strength: Signal strength score
            action: Action taken (ENTRY, SKIP, etc.)
        """
        if not self.enabled:
            return
            
        try:
            signal_doc = {
                "collection": "signals",
                "document": {
                    "symbol": str(symbol.value),
                    "timestamp": str(self.algorithm.utc_time),
                    "price": float(self.algorithm.securities[symbol].price),
                    "indicators": {k: float(v) if v is not None else None 
                                 for k, v in indicators.items()},
                    "signal_strength": float(signal_strength),
                    "action": action
                }
            }
            
            self._make_api_request("insert", signal_doc)
            
        except Exception as e:
            self.logger.error(f"Signal logging error: {e}")
    
    def log_risk_event(self, event_type: str, details: Dict) -> None:
        """
        Log risk management events (stops, limits, etc.)
        
        Args:
            event_type: Type of risk event
            details: Event details
        """
        if not self.enabled:
            return
            
        try:
            risk_doc = {
                "collection": "risk_events",
                "document": {
                    "event_type": event_type,
                    "timestamp": str(self.algorithm.utc_time),
                    "portfolio_value": float(self.algorithm.portfolio.total_portfolio_value),
                    "details": details
                }
            }
            
            self._make_api_request("insert", risk_doc)
            
        except Exception as e:
            self.logger.error(f"Risk event logging error: {e}")
    
    def flush_all_batches(self) -> None:
        """Flush all pending batches to MongoDB"""
        self._flush_trade_batch()
        self._flush_performance_batch()
    
    def log_session_end(self, final_stats: Dict) -> None:
        """
        Log algorithm session end with final statistics
        
        Args:
            final_stats: Final performance statistics
        """
        if not self.enabled:
            return
            
        try:
            # Flush all pending data
            self.flush_all_batches()
            
            # If BACKTEST mode, save to ObjectStore instead of MongoDB
            if not self.algorithm.live_mode:
                self._save_backtest_data_to_objectstore(final_stats)
                return
            
            # Update session document (LIVE mode only)
            payload = {
                "collection": "sessions",
                "filter": {"session_id": self.session_id},
                "update": {
                    "end_time": str(self.algorithm.utc_time),
                    "status": "COMPLETED",
                    "final_value": float(self.algorithm.portfolio.total_portfolio_value),
                    "final_stats": final_stats
                }
            }
            
            self._make_api_request("update", payload, method="PUT")
            self.logger.info(f"MongoDB session ended: {self.session_id}")
            
        except Exception as e:
            self.logger.error(f"Session end logging error: {e}")
    
    def _save_backtest_data_to_objectstore(self, final_stats: Dict) -> None:
        """
        Save backtest data to QuantConnect ObjectStore for later retrieval
        
        Args:
            final_stats: Final performance statistics
        """
        try:
            self.algorithm.debug("Saving backtest data to ObjectStore...")
            
            # Collect all batched data
            trades = list(self.trade_batch)
            performance = list(self.metric_batch)
            
            # Create comprehensive backtest report
            backtest_data = {
                "session_id": self.session_id,
                "algorithm_id": self.algorithm_id,
                "start_time": str(self.algorithm.start_date),
                "end_time": str(self.algorithm.utc_time),
                "final_stats": final_stats,
                "trades": trades,
                "performance": performance,
                "trade_count": len(trades),
                "generated_at": str(datetime.now())
            }
            
            # Save to ObjectStore with timestamp
            filename = f"backtest_{self.session_id}.json"
            self.algorithm.object_store.save(filename, json.dumps(backtest_data, indent=2))
            
            self.algorithm.debug(f"Backtest data saved to ObjectStore: {filename}")
            self.algorithm.debug(f"   Trades: {len(trades)}, Performance snapshots: {len(performance)}")
            self.logger.info(f"Backtest data saved to ObjectStore: {filename}")
            
        except Exception as e:
            self.algorithm.debug(f"ObjectStore save failed: {str(e)[:100]}")
            self.logger.error(f"ObjectStore save error: {e}")
    
    def query_performance_history(self, days: int = 30) -> Optional[List[Dict]]:
        """
        Query recent performance history
        
        Args:
            days: Number of days to query
            
        Returns:
            List of performance documents or None
        """
        if not self.enabled:
            return None
            
        try:
            payload = {
                "collection": "performance",
                "filter": {
                    "metadata.algorithm_id": self.algorithm_id,
                    "metadata.session_id": self.session_id
                },
                "sort": {"date": -1},
                "limit": days
            }
            
            # Note: This would require a GET endpoint on your MongoDB REST API
            # For now, this is a placeholder showing how you'd structure it
            self.logger.info(f"Performance query not implemented (read-only)")
            return None
            
        except Exception as e:
            self.logger.error(f"Performance query error: {e}")
            return None

    def on_end_of_algorithm(self):
        """Called at end of backtest - send ALL batched data"""
        if not self.enabled:
            return
        
        self.algorithm.debug("Sending batched data to MongoDB...")
        
        # Flush all batches
        self.flush_all_batches()
        
        self.algorithm.debug("All data sent to MongoDB")