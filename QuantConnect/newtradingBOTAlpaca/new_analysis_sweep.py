from contextlib import redirect_stdout
from io import StringIO
from datetime import timedelta
from itertools import product

from new_backtest_harness import AlpacaBacktestHarness
from new_bot_config import BOT_Config


def run_cached_backtest(base: AlpacaBacktestHarness, style: str) -> dict:
    harness = AlpacaBacktestHarness(
        base.config.universe.core_symbols,
        str(base.start_date.date()),
        str(base.end_date.date()),
        base.starting_cash,
        style,
    )
    harness.data_client._daily_data = base.data_client._daily_data
    harness.data_client._hourly_data = base.data_client._hourly_data
    harness.loaded_symbols = set(base.loaded_symbols)
    harness._active_universe = [s for s in base._active_universe if s in harness.loaded_symbols]
    harness._refresh_dynamic_universe(harness.start_date, force=True)

    current = harness.start_date
    while current <= harness.end_date:
        if current.weekday() < 5:
            harness.process_day(current)
        current += timedelta(days=1)
    return harness.report()


def set_params(bull: float, neutral: float, bear: float, momentum_min: int, max_position_value: int):
    BOT_Config.trading.bull_entry_score_threshold = bull
    BOT_Config.trading.neutral_entry_score_threshold = neutral
    BOT_Config.trading.bear_entry_score_threshold = bear
    BOT_Config.trading.momentum_rsi_min = momentum_min
    BOT_Config.risk.max_position_value = max_position_value


def main():
    original = {
        "bull": BOT_Config.trading.bull_entry_score_threshold,
        "neutral": BOT_Config.trading.neutral_entry_score_threshold,
        "bear": BOT_Config.trading.bear_entry_score_threshold,
        "momentum_min": BOT_Config.trading.momentum_rsi_min,
        "max_position_value": BOT_Config.risk.max_position_value,
    }

    base_buf = StringIO()
    with redirect_stdout(base_buf):
        base = AlpacaBacktestHarness(BOT_Config.universe.core_symbols, "2024-01-01", "2024-12-31", 11000, "INTRADAY")
        base.load_all_data()
        base._refresh_dynamic_universe(base.start_date, force=True)

    candidates = []
    grid = product(
        [0.25, 0.35, 0.45, 0.55],
        [0.25, 0.35, 0.45],
        [0.14, 0.20],
        [52, 56, 60],
        [2000, 2500, 3000],
    )
    for bull, neutral, bear, momentum_min, max_position_value in grid:
        set_params(bull, neutral, bear, momentum_min, max_position_value)
        run_buf = StringIO()
        with redirect_stdout(run_buf):
            report = run_cached_backtest(base, "INTRADAY")
        if report:
            candidates.append({
                "bull": bull,
                "neutral": neutral,
                "bear": bear,
                "momentum_min": momentum_min,
                "max_position_value": max_position_value,
                **report,
            })

    set_params(
        original["bull"],
        original["neutral"],
        original["bear"],
        original["momentum_min"],
        original["max_position_value"],
    )

    ranked = sorted(
        candidates,
        key=lambda item: (item["return_pct"], -item["max_drawdown"], item["sharpe"]),
        reverse=True,
    )
    for row in ranked[:12]:
        print(row)


if __name__ == "__main__":
    main()