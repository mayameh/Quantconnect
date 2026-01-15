
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

class EmailAlerter:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self.smtp_server = None
        self.smtp_port = None
        self.email = None
        self.password = None
    def configure(self, smtp_server, smtp_port, email, password):
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.email = email
        self.password = password
    def send_alert(self, subject, message, level="INFO"):
        self.logger.info(f"ALERT [{level}]: {subject}")
    def send_email(self, subject: str, body: str):
        """Send email using configured SMTP settings"""
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            
            if not all([self.smtp_server, self.smtp_port, self.email, self.password]):
                self.logger.error("Email not configured properly")
                return False
            
            # Create message
            msg = MIMEMultipart()
            msg['From'] = self.email
            msg['To'] = self.email  # Send to same email
            msg['Subject'] = subject
            
            # Add body to email
            msg.attach(MIMEText(body, 'plain'))
            
            # Create SMTP session
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()  # Enable security
            server.login(self.email, self.password)
            
            # Send email
            text = msg.as_string()
            server.sendmail(self.email, self.email, text)
            server.quit()
            
            self.logger.info(f"Email sent successfully: {subject}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to send email: {e}")
            return False    

class PerformanceTracker:
    def __init__(self, logger):
        self.logger = logger
        self.trades = []
    def record_trade(self, symbol, entry_price, exit_price, quantity, pnl):
        pass

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


# Config = BOT_Config
