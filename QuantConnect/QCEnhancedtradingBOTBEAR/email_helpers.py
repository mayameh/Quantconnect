"""Email formatting helpers — extracted to reduce main.py size."""

def format_summary_email(algo) -> str:
    """Format comprehensive portfolio summary email."""
    current_equity = algo.portfolio.total_portfolio_value
    total_return = (current_equity - algo._starting_cash) / algo._starting_cash
    daily_pnl = current_equity - algo._starting_cash

    positions = []
    for symbol, holding in algo.portfolio.items():
        if holding.invested:
            cp = float(algo.securities[symbol].price)
            ae = holding.average_price
            q = abs(holding.quantity)
            positions.append({
                'symbol': symbol.value,
                'qty': q,
                'entry_price': ae,
                'current_price': cp,
                'pnl': q * (cp - ae),
                'pnl_pct': (cp - ae) / ae if ae > 0 else 0,
                'time_held': str(algo.time - algo.entry_time.get(symbol, algo.time)),
                'value': q * cp
            })

    recent = list(algo.trade_history)[-10:]

    e = "=" * 70 + "\nPORTFOLIO SUMMARY\n" + "=" * 70 + "\n\n"
    e += "PORTFOLIO OVERVIEW\n" + "-" * 70 + "\n"
    e += f"Brokerage:          {algo.brokerage_name}\n"
    e += f"Current Equity:    ${current_equity:,.2f}\n"
    e += f"Total Return:      {total_return:.2%}\n"
    e += f"Daily P&L:         ${daily_pnl:,.2f}\n"
    e += f"Cash Position:     ${algo.portfolio.cash:,.2f}\n\n"
    e += "MARKET REGIME\n" + "-" * 70 + "\n"
    e += f"Regime Status:     {algo.market_regime}\n"
    e += f"Symbols Scanned:   {len(algo._all_symbols)}\n"

    ap = len([s for s in algo.portfolio.keys() if algo.portfolio[s].invested and s in algo._algo_managed_positions])
    mp = len([s for s in algo.portfolio.keys() if algo.portfolio[s].invested and s not in algo._algo_managed_positions])
    e += f"Algo Positions:    {ap}/{algo.max_positions}\nManual Positions:  {mp}\n\n"

    e += "OPEN POSITIONS\n" + "-" * 70 + "\n"
    if positions:
        for p in positions:
            pt = "ALGO" if any(s.value == p['symbol'] and s in algo._algo_managed_positions for s in algo.portfolio.keys()) else "MANUAL"
            e += f"{p['symbol']:<8} | Qty: {p['qty']:<6} | Entry: ${p['entry_price']:<8.2f} | Cur: ${p['current_price']:<8.2f} | P&L: ${p['pnl']:<10.2f} ({p['pnl_pct']:>6.2%}) | {pt:<6} | {p['time_held']}\n"
    else:
        e += "No open positions\n"
    e += "\nRECENT TRADES (Last 10)\n" + "-" * 70 + "\n"
    for t in recent:
        e += f"{t}\n"
    e += "\n" + "=" * 70 + f"\nReport: {algo.time.strftime('%Y-%m-%d %H:%M:%S')}\n"
    return e


def format_weekly_summary(algo) -> str:
    """Format comprehensive weekly summary with performance analytics."""
    try:
        ce = algo.portfolio.total_portfolio_value
        tr = (ce - algo._starting_cash) / algo._starting_cash
        wc = len(algo.winning_trades)
        lc = len(algo.losing_trades)
        tc = wc + lc
        wr = (wc / tc * 100) if tc > 0 else 0
        dd = (algo.peak_equity - ce) / algo.peak_equity if algo.peak_equity > 0 else 0

        top = sorted([(s, d) for s, d in algo.symbol_performance.items() if d['trades'] > 0],
                     key=lambda x: x[1]['total_pnl'], reverse=True)[:5]
        worst = sorted([(s, d) for s, d in algo.symbol_performance.items() if d['trades'] > 0],
                       key=lambda x: x[1]['total_pnl'])[:3]

        e = "=" * 80 + "\nWEEKLY PORTFOLIO ANALYSIS\n" + "=" * 80 + "\n\n"
        e += "PORTFOLIO PERFORMANCE\n" + "-" * 80 + "\n"
        e += f"Brokerage:            {algo.brokerage_name}\n"
        e += f"Current Equity:        ${ce:,.2f}\n"
        e += f"Starting Capital:      ${algo._starting_cash:,.2f}\n"
        e += f"Total Return:          {tr:.2%}\n"
        e += f"Cash Available:        ${algo.portfolio.cash:,.2f}\n"
        e += f"Peak Equity:           ${algo.peak_equity:,.2f}\n"
        e += f"Current Drawdown:      {dd:.2%}\n\n"

        e += "TRADING STATISTICS\n" + "-" * 80 + "\n"
        e += f"Total Trades:          {tc}\n"
        e += f"Win/Loss:              {wc}/{lc}\n"
        e += f"Win Rate:              {wr:.1f}%\n"
        e += f"Market Regime:         {algo.market_regime}\n"
        e += f"Symbols in Universe:   {len(algo._all_symbols)}\n\n"

        # Current Holdings
        e += "CURRENT HOLDINGS\n" + "-" * 80 + "\n"
        total_pv = 0
        holdings = []
        for sym, h in algo.portfolio.items():
            if h.invested:
                cp = float(algo.securities[sym].price)
                ae = h.average_price
                q = abs(h.quantity)
                pv = q * cp
                total_pv += pv
                holdings.append((sym.value, q, ae, cp, pv, q * (cp - ae),
                                 (cp - ae) / ae if ae > 0 else 0,
                                 str(algo.time - algo.entry_time.get(sym, algo.time)).split('.')[0],
                                 pv / ce * 100))
        if holdings:
            e += f"{'Sym':<8}|{'Qty':<5}|{'Entry':<9}|{'Cur':<9}|{'Value':<10}|{'P&L':<10}|{'%':<7}|{'Alloc':<6}|{'Held'}\n"
            for h in sorted(holdings, key=lambda x: x[5], reverse=True):
                e += f"{h[0]:<8}|{h[1]:<5}|${h[2]:<8.2f}|${h[3]:<8.2f}|${h[4]:<9,.0f}|${h[5]:<9,.0f}|{h[6]:<6.2%}|{h[8]:<5.1f}%|{h[7]}\n"
            e += f"\nTotal Value: ${total_pv:,.2f} ({total_pv / ce * 100:.1f}%)\n\n"
        else:
            e += "No holdings\n\n"

        # Top / Worst performers
        if top:
            e += "TOP PERFORMERS\n" + "-" * 80 + "\n"
            for s, p in top:
                avg = p['total_pnl'] / p['trades'] if p['trades'] > 0 else 0
                e += f"{s.value:<8} | Trades: {p['trades']:<4} | WR: {p['win_rate']:<5.1f}% | P&L: ${p['total_pnl']:<9,.0f} | Avg: ${avg:<7,.0f}\n"
            e += "\n"
        if worst:
            e += "WORST PERFORMERS\n" + "-" * 80 + "\n"
            for s, p in worst:
                e += f"{s.value:<8} | Trades: {p['trades']:<4} | WR: {p['win_rate']:<5.1f}% | P&L: ${p['total_pnl']:<9,.0f} | ConsecL: {p['consecutive_losses']}\n"
            e += "\n"

        # Recent trades
        e += "RECENT TRADES (Last 15)\n" + "-" * 80 + "\n"
        for t in list(algo.trade_history)[-15:]:
            e += f"{t}\n"
        e += "\n"

        # Risk
        e += "RISK METRICS\n" + "-" * 80 + "\n"
        e += f"Drawdown: {dd:.2%} | Cash: {algo.portfolio.cash / ce * 100:.1f}% | Equity: {total_pv / ce * 100:.1f}%\n"
        e += f"Risk/Trade: ~{algo.config.trading.stop_loss_pct * 100:.1f}% | Max Pos: {algo.max_positions}\n\n"

        # Notes
        e += "NOTES\n" + "-" * 80 + "\n"
        if tr > 0.05:
            e += "Strong performance.\n"
        elif tr > -0.02:
            e += "Within acceptable range.\n"
        else:
            e += "Review risk management.\n"
        if dd > 0.05:
            e += "WARNING: High drawdown.\n"
        e += "\n" + "=" * 80 + f"\nWeekly Report: {algo.time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        return e
    except Exception as ex:
        return f"Error generating weekly summary: {ex}"
