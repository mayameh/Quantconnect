from contextlib import redirect_stdout
from io import StringIO
from collections import Counter
from datetime import timedelta

from new_backtest_harness import AlpacaBacktestHarness
from new_bot_config import BOT_Config


def main():
    cfg = BOT_Config()
    buf = StringIO()
    with redirect_stdout(buf):
        harness = AlpacaBacktestHarness(
            cfg.universe.core_symbols,
            "2024-01-01",
            "2024-12-31",
            11000,
            "INTRADAY",
        )
        harness.load_all_data()
        harness._refresh_dynamic_universe(harness.start_date, force=True)

    scores = []
    reasons = Counter()
    sample = []
    current = harness.start_date
    while current <= harness.end_date:
        if current.weekday() < 5:
            ts = harness._to_ts(current)
            harness.data_client.set_current_date(ts)
            harness._advance_prices(ts)
            harness._refresh_dynamic_universe(ts, force=False)
            harness.detect_market_regime(ts)
            if harness.market_regime != "BEAR":
                day_scores = []
                for ticker in harness._active_universe:
                    indicators = harness._compute_symbol_indicators(ticker, ts)
                    if not indicators or indicators["price"] <= 0:
                        continue
                    score, reason = harness._compute_portfolio_signal(ticker, indicators)
                    if score > 0:
                        rounded = round(score, 4)
                        scores.append(rounded)
                        reasons[reason] += 1
                        day_scores.append((ticker, rounded, reason))
                if day_scores and len(sample) < 10:
                    sample.append(
                        (str(ts.date()), harness.market_regime, sorted(day_scores, key=lambda x: x[1], reverse=True)[:3])
                    )
            harness.evaluate_signals(ts)
            harness.flatten_intraday_positions(ts)
            harness.update_equity(ts)
        current += timedelta(days=1)

    print("nonzero_scores", len(scores))
    print("top_score_counts", Counter(scores).most_common(12))
    print("top_reasons", reasons.most_common(12))
    print("samples")
    for row in sample:
        print(row)


if __name__ == "__main__":
    main()