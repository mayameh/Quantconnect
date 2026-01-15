# region imports
from AlgorithmImports import *
# endregion
"""
Production Algorithm Configuration
Customize these settings for your trading environment
"""

class BOT_Config:
    """Production configuration"""
    
    # ============================================================
    # GENERAL SETTINGS
    # ============================================================
    class general:
        strategy_name = "AI-Enhanced Hybrid Universe Strategy"
        mode = "LIVE"  # "PAPER" or "LIVE" - START WITH PAPER TRADING
        starting_capital = 11000
    
    # ============================================================
    # RISK MANAGEMENT
    # ============================================================
    class risk:
        # Daily loss limit - stop trading if daily loss exceeds this
        max_daily_loss = 100  # $100 = ~0.9% of starting capital
        
        # Maximum portfolio drawdown from peak
        max_drawdown_pct = 0.05  # 5% max drawdown
        
        # Position sizing
        max_position_size_pct = 0.25  # Max 25% of portfolio per position
        min_entry_notional = 4000  # Minimum trade size in dollars
        target_position_value = 0.85  # Use 85% of available cash
        
        # Per-symbol limits
        symbol_max_loss = 100  # Stop trading a symbol if it loses $100
        max_consecutive_losses = 3  # Disable symbol after 3 losses
    
    # ============================================================
    # TRADING PARAMETERS
    # ============================================================
    class trading:
        eval_interval_minutes = 180  # Evaluate signals every 3 hours
        
        # Position management
        max_positions = 1  # Number of concurrent positions
        min_hold_hours = 24
        max_hold_days = 14
        
        # Profit taking
        stop_loss_pct = 0.03  # 3% stop loss
        take_profit_pct = 0.08  # 8% take profit
        profit_lock_hours = 30  # Hold for profit lock
        profit_lock_min_gain_pct = 0.018  # 1.8% minimum gain
        
        # Trailing stop
        trailing_stop_pct = 0.03
        trailing_activation_pct = 0.04
        
        # Trading frequency limits
        max_weekly_trades = 2
        min_days_between_trades = 3
        symbol_cooldown_days = 10
    
    # ============================================================
    # UNIVERSE SELECTION
    # ============================================================
    class universe:
        core_symbols = ["NVDA", "AAPL", "MSFT", "AMZN", "META"]
        
        # Dynamic stock selection
        max_dynamic_stocks = 5
        universe_refresh_days = 14
        indicator_warmup_hours = 60
        
        # Fundamental filters
        min_price = 50.0
        min_dollar_volume = 100000000  # $100M
        max_pe_ratio = 100
        min_revenue_growth = 0  # Positive growth
    
    # ============================================================
    # MONITORING & ALERTING
    # ============================================================
    class monitoring:
        # Logging
        log_to_file = True
        log_file_path = "/logs/algo_trading.log"
        log_level = "INFO"  # DEBUG, INFO, WARNING, ERROR, CRITICAL
        
        # Email alerts
        enable_email_alerts = True
        email_to = ["mayank@mehrotra.co.in"]  # ADD YOUR EMAIL
        
        # Alert thresholds
        alert_on_daily_loss = 100  # Alert if daily loss > $100
        alert_on_drawdown = 0.03  # Alert if drawdown > 3%
        alert_on_error = True  # Alert on any error
        
        # Daily summary
        send_daily_summary = True
        daily_summary_time = "16:30"  # After market close
        
        # Reconciliation
        reconcile_interval_hours = 6
    
    # ============================================================
    # LIVE TRADING SETTINGS (only used if mode=LIVE)
    # ============================================================
    class live:
        # Brokerage settings
        brokerage = "INTERACTIVE_BROKERS"
        account_type = "CASH"  # CASH only - no margin
        
        # Order settings
        use_limit_orders = True  # Use limit orders instead of market
        limit_order_buffer_pct = 0.05  # 5% buffer from current price
        order_timeout_minutes = 30  # Cancel unfilled orders after 30 min
        
        # Connection
        auto_reconnect = True
        reconnect_attempts = 5
        reconnect_delay_seconds = 10
        
        # Automatic actions on errors
        auto_liquidate_on_disconnect = False  # DANGEROUS - requires explicit override
        emergency_liquidate_threshold = 0.10  # Liquidate if drawdown > 10%
    
    # ============================================================
    # EMAIL CONFIGURATION (for alerts)
    # ============================================================
    class email:
        # Gmail SMTP settings (example)
        smtp_server = "smtp.gmail.com"
        smtp_port = 587
        sender_email = "mehrotram@gmail.com"  # CHANGE THIS
        sender_password = "ducu txsq uhck fdsx"  # CHANGE THIS - use app password, not Gmail password
    
    # ============================================================
    # TESTING & DEVELOPMENT
    # ============================================================
    class development:
        # Enable test mode
        test_mode = False
        
        # Dry run (process signals without placing orders)
        dry_run = False
        
        # Debug logging
        verbose_logging = False
