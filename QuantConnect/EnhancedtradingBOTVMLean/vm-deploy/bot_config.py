"""
Bot Configuration — native ib_insync version
Matches the original production_config.py settings
"""
import os


class BOT_Config:
    """Configuration for the ib_insync trading bot on headless VPS."""

    # ============================================================
    # GENERAL
    # ============================================================
    class general:
        strategy_name = "AI-Enhanced Hybrid Universe Strategy"
        mode = "LIVE"                  # "PAPER" or "LIVE"
        starting_capital = 11000

    # ============================================================
    # IB GATEWAY CONNECTION
    # (Gateway is already running via IBC + systemd on this VM)
    # ============================================================
    class ib:
        host = "127.0.0.1"
        port = int(os.environ.get("IB_PORT", "4002"))  # 4001=live, 4002=paper
        client_id = int(os.environ.get("IB_CLIENT_ID", "1"))
        reconnect_attempts = 5
        reconnect_delay = 10        # Seconds between retries

    # ============================================================
    # RISK MANAGEMENT
    # ============================================================
    class risk:
        max_daily_loss = 100        # $100 daily loss → circuit breaker
        max_drawdown_pct = 0.05     # 5% max drawdown

        max_position_size_pct = 0.25
        min_entry_notional = 4000
        target_position_value = 0.85

        symbol_max_loss = 100
        max_consecutive_losses = 3

    # ============================================================
    # TRADING PARAMETERS
    # ============================================================
    class trading:
        max_positions = 4
        min_hold_hours = 24
        max_hold_days = 14

        # Exit thresholds (proven settings)
        stop_loss_pct = 0.03        # 3%
        take_profit_pct = 0.08      # 8%
        profit_lock_hours = 30
        profit_lock_min_gain_pct = 0.018  # 1.8%

        trailing_stop_pct = 0.03
        trailing_activation_pct = 0.04

        max_weekly_trades = 2
        min_days_between_trades = 3
        symbol_cooldown_days = 10

    # ============================================================
    # UNIVERSE
    # ============================================================
    class universe:
        core_symbols = ["NVDA", "AAPL", "MSFT", "AMZN", "META"]

        # Dynamic universe selection (core + top-N momentum)
        dynamic_enabled = True
        refresh_days = 14
        top_n_dynamic = 10

        # Candidate pool used for ranking dynamic names.
        # Keep this curated to avoid IB pacing issues from very large scans.
        candidate_symbols = [
            "NVDA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "AVGO", "TSLA", "NFLX", "AMD",
            "ADBE", "CRM", "ORCL", "INTU", "QCOM", "TXN", "AMAT", "PANW", "MU", "LRCX",
            "ANET", "KLAC", "SHOP", "NOW", "CRWD", "PLTR", "SNOW", "MELI", "UBER", "ABNB",
        ]
        exclude_symbols = ["SPY"]

        # Pre-filters for tradability/liquidity during dynamic ranking.
        min_price = 50.0
        min_avg_dollar_volume = 100_000_000
        avg_volume_window_days = 20

        # Ranking parameters.
        momentum_lookback_days = 22  # approx. 1 month of trading days
        momentum_weight = 0.60
        revenue_growth_weight = 0.40

        # External fundamentals source (Alpaca) for live revenue growth.
        fundamentals_external_enabled = True
        fundamentals_provider = "alpaca"
        fundamentals_base_url = os.environ.get("BOT_FUNDAMENTALS_BASE_URL", "https://data.alpaca.markets")
        fundamentals_path_template = os.environ.get(
            "BOT_ALPACA_FINANCIALS_PATH",
            "/v1beta1/fundamentals/{symbol}",
        )
        fundamentals_api_key = os.environ.get("BOT_ALPACA_API_KEY", "")
        fundamentals_api_secret = os.environ.get("BOT_ALPACA_API_SECRET", "")
        fundamentals_timeout_seconds = 8
        fundamentals_cache_hours = 24
        fundamentals_fallback_to_static = True

        # Optional fundamental proxy map (decimal values, e.g. 0.20 == 20% YoY).
        # If ticker is missing, revenue growth defaults to 0.
        revenue_growth_1y = {
            "NVDA": 1.20,
            "META": 0.22,
            "AVGO": 0.34,
            "PLTR": 0.26,
            "CRWD": 0.33,
            "MELI": 0.27,
            "ABNB": 0.18,
            "TSLA": 0.05,
            "AAPL": 0.03,
            "MSFT": 0.15,
        }

        # Small pacing delay between IB historical requests during ranking.
        request_pause_seconds = 0.25

    # ============================================================
    # MONITORING
    # ============================================================
    class monitoring:
        log_dir = os.environ.get(
            "BOT_LOG_DIR",
            os.path.expanduser("~/trading/tradingbot/logs"),
        )
        log_level = "INFO"

    # ============================================================
    # EMAIL
    # ============================================================
    class email:
        smtp_server = "smtp.gmail.com"
        smtp_port = 587
        sender_email = os.environ.get("BOT_EMAIL_USER", "mehrotram@gmail.com")
        sender_password = os.environ.get("BOT_EMAIL_PASS", "")
        recipient_email = os.environ.get("BOT_EMAIL_TO", "mayank@mehrotra.co.in")
