
"""
Production Algorithm Configuration
Customize these settings for your trading environment
"""

class ProductionLogger:
    def __init__(self, config):
        self.config = config
    def info(self, message):
        print(f"[INFO] {message}")
    def warning(self, message):
        print(f"[WARNING] {message}")
    def error(self, message):
        print(f"[ERROR] {message}")
    def critical(self, message):
        print(f"[CRITICAL] {message}")
    def debug(self, message):
        print(f"[DEBUG] {message}")

class BOT_Config:
    """Production configuration"""
    
    # ============================================================
    # GENERAL SETTINGS
    # ============================================================
    class general:
        strategy_name = "AI-Enhanced Hybrid Universe Strategy"
        mode = "LIVE"  # "PAPER" or "LIVE" - PAPER TRADING ACTIVE
        starting_capital = 11000
    
    # ============================================================
    # RISK MANAGEMENT
    # ============================================================
    class risk:
        # Daily loss limit - stop trading if daily loss exceeds this
        max_daily_loss = 250  # $250 = ~2.3% of starting capital (room for 2-3 normal stops)
        
        # Maximum portfolio drawdown from peak
        max_drawdown_pct = 0.15  # 15% max drawdown (give strategy room to work)
        
        # Position sizing
        max_position_size_pct = 0.25  # Max 25% of portfolio per position
        min_entry_notional = 3000  # Minimum trade size in dollars
        target_position_value = 0.85  # Use 85% of available cash
        
        # Per-symbol limits
        symbol_max_loss = 100  # Stop trading a symbol if it loses $100
        max_consecutive_losses = 3  # Disable symbol after 3 losses
    
    # ============================================================
    # TRADING PARAMETERS
    # ============================================================
    class trading:
        eval_interval_minutes = 60  # Evaluate every hour for more opportunities
        
        # Position management
        max_positions = 4  # Number of concurrent positions
        min_hold_hours = 6  # Shorter hold for faster turnover
        max_hold_days = 14  # Cut stale positions faster
        
        # Profit taking - tuned for better win/loss asymmetry
        stop_loss_pct = 0.03  # 3% stop loss (cut losses fast, match avg win size)
        take_profit_pct = 0.08  # 8% take profit (2.67:1 reward:risk)
        profit_lock_hours = 72  # Lock profit after 3 days (give trades room)
        profit_lock_min_gain_pct = 0.05  # 5% minimum gain to lock (don't cut winners early)
        
        # Trailing stop — primary winner management tool
        trailing_stop_enabled = True
        trailing_stop_pct = 0.03  # 3% trailing stop (wider to avoid shakeouts)
        trailing_activation_pct = 0.04  # Activate at 4% gain (worst exit = +1%)
        
        # Trading frequency limits — RELAXED to allow more trades
        max_weekly_trades = 8  # Allow up to 8 per week
        min_days_between_trades = 0  # No cooldown between trades
        symbol_cooldown_days = 2  # 2-day symbol cooldown (faster re-entry)
        symbol_cooldown_after_stop = 5  # 5-day cooldown after stop-loss on same symbol
    
    # ============================================================
    # BEAR DIP-BUY PARAMETERS (blue-chip discount buying)
    # ============================================================
    class bear_dip_buy:
        enabled = True
        
        # Regime thresholds — what qualifies as "extreme" bear
        spy_rsi_threshold = 45        # SPY RSI must be below this
        spy_discount_pct = 0.02       # SPY must be >2% below EMA50
        
        # Per-symbol entry filters
        symbol_rsi_max = 50           # Symbol RSI must be oversold (generous)
        symbol_discount_pct = 0.03    # Symbol must be >3% below its EMA50
        
        # Position management — tighter exits for bear bounces
        max_positions = 3             # Max 3 bear dip-buy positions
        stop_loss_pct = 0.03          # 3% stop
        take_profit_pct = 0.05        # 5% take profit (1.67:1 R:R)
        profit_lock_hours = 24        # Lock profit after 24 hours
        profit_lock_min_gain_pct = 0.015  # 1.5% min gain to lock
        max_hold_days = 7             # 7 days max (bear rallies are brief)
        
        # Scan full universe (not just core) for NASDAQ-100 opportunities
        core_only = False
        
        # Scale-in: enter with partial size, add on confirmation
        scale_in_enabled = True
        initial_size_pct = 0.50       # Enter with 50% of full position
        scale_in_gain_pct = 0.015     # Add remaining when +1.5% gain confirmed
        scale_in_min_hours = 24       # Wait at least 24h before scaling in
        
        # Bounce confirmation: require RSI to be turning up
        require_bounce = True
        
        # Volume confirmation: require above-average volume on entry day
        require_volume_spike = True
        volume_spike_ratio = 1.2      # Volume must be 1.2x the 20-period average
        
        # Market breadth: min % of NASDAQ-100 above their EMA50
        breadth_enabled = True
        min_breadth_pct = 0.15        # At least 15% of stocks above EMA50 (floor)
    
    # ============================================================
    # UNIVERSE SELECTION
    # ============================================================
    class universe:
        core_symbols = [
            # NASDAQ-100 large-cap blue chips
            "AAPL", "MSFT", "AMZN", "NVDA", "META", "GOOGL", "GOOG", "AVGO",
            "COST", "TSLA", "NFLX", "AMD", "ADBE", "PEP", "CSCO", "INTC",
            "TMUS", "CMCSA", "TXN", "QCOM", "AMGN", "INTU", "ISRG", "AMAT",
            "HON", "BKNG", "LRCX", "VRTX", "REGN", "ADI", "MU", "MDLZ",
            "PANW", "KLAC", "SNPS", "CDNS", "MELI", "PYPL", "ABNB", "CRWD",
            "FTNT", "MAR", "ORLY", "DASH", "CTAS", "MRVL", "CHTR", "WDAY",
            "KDP", "MNST", "AEP", "DXCM", "PCAR", "PAYX", "IDXX", "ODFL",
            "EXC", "ROST", "FAST", "CPRT", "LULU", "CEG", "AZN", "BKR",
            "VRSK", "KHC", "CTSH", "ON", "GEHC", "GFS", "TTWO", "ANSS",
            "CDW", "DDOG", "FANG", "WBD", "ZS", "TEAM", "ILMN", "BIIB",
            "ALGN", "ENPH", "SIRI", "WBA", "DLTR", "LCID", "RIVN",
        ]
        
        # Dynamic stock selection
        max_dynamic_stocks = 10
        universe_refresh_days = 7
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
    # TESTING & DEVELOPMENT
    # ============================================================
    class development:
        # Enable test mode
        test_mode = False
        
        # Dry run (process signals without placing orders)
        dry_run = False
        
        # Debug logging
        verbose_logging = False
