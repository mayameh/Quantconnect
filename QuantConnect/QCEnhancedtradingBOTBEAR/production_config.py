
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
        max_daily_loss = 100  # $100 = ~0.9% of starting capital
        
        # Maximum portfolio drawdown from peak
        max_drawdown_pct = 0.08  # 8% max drawdown (wider for bear volatility)
        
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
        eval_interval_minutes = 180  # ORIGINAL: Every 3 hours (proven)
        
        # Position management
        max_positions = 4  # Number of concurrent positions
        min_hold_hours = 24
        max_hold_days = 14
        
        # Profit taking (ORIGINAL ORANGE COBRA SETTINGS - 61.90% win rate)
        stop_loss_pct = 0.03  # 3% stop loss
        take_profit_pct = 0.08  # 8% take profit (ORIGINAL - proven)
        profit_lock_hours = 30  # Hold for profit lock (ORIGINAL)
        profit_lock_min_gain_pct = 0.018  # 1.8% minimum gain
        
        # Trailing stop
        trailing_stop_pct = 0.03
        trailing_activation_pct = 0.04
        
        # Trading frequency limits (ORIGINAL PROVEN SETTINGS)
        max_weekly_trades = 2  # ORIGINAL - 2 per week
        min_days_between_trades = 3  # ORIGINAL - 3 days
        symbol_cooldown_days = 10  # ORIGINAL - 10 days
    
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
        stop_loss_pct = 0.04          # 4% stop — tight to cut losers fast
        take_profit_pct = 0.04        # 4% take profit (lock more winnings quickly in volatile markets)
        profit_lock_hours = 36        # Lock profit after 36 hours
        profit_lock_min_gain_pct = 0.02   # 2% min gain to lock
        max_hold_days = 10            # 10 days max (bear rallies are brief)
        
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
    # TESTING & DEVELOPMENT
    # ============================================================
    class development:
        # Enable test mode
        test_mode = False
        
        # Dry run (process signals without placing orders)
        dry_run = False
        
        # Debug logging
        verbose_logging = False
