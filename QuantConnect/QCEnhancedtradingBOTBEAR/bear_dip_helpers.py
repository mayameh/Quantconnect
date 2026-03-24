"""Bear-dip entry helpers — extracted to reduce main.py size."""

from datetime import timedelta


def evaluate_bear_dip_entries(algo, algo_invested) -> None:
    """Process bear/extreme-bear dip-buy entries with scale-in support."""

    # ── SCALE-IN CHECK: top-up partial positions that are confirming ──
    if algo.config.bear_dip_buy.scale_in_enabled:
        for sym, info in list(algo._bear_dip_scale_in_pending.items()):
            try:
                if not algo.portfolio[sym].invested:
                    algo._bear_dip_scale_in_pending.pop(sym, None)
                    continue
                cp = float(algo.securities[sym].price)
                gain = (cp - info['entry_price']) / info['entry_price']
                held = algo.time - algo.entry_time.get(sym, algo.time)
                if gain >= algo.config.bear_dip_buy.scale_in_gain_pct and held >= timedelta(hours=algo.config.bear_dip_buy.scale_in_min_hours):
                    add_qty = info['target_qty']
                    if add_qty > 0 and algo.portfolio.cash >= cp * add_qty:
                        algo.market_order(sym, add_qty)
                        algo.trade_history.append(f"{algo.time.strftime('%Y-%m-%d %H:%M')} SCALE-IN {sym.value} +{add_qty} @ ${cp:.2f} | gain={gain:.1%}")
                        algo.logger.info(f"BEAR SCALE-IN: {sym.value} +{add_qty} @ ${cp:.2f} gain={gain:.1%}")
                    algo._bear_dip_scale_in_pending.pop(sym, None)
                elif gain <= -algo.config.bear_dip_buy.stop_loss_pct:
                    # Stop hit before scale-in — cancel pending
                    algo._bear_dip_scale_in_pending.pop(sym, None)
            except Exception:
                algo._bear_dip_scale_in_pending.pop(sym, None)

    bear_dip_count = len([s for s in algo_invested if s in algo._bear_dip_positions])
    bear_max = algo.config.bear_dip_buy.max_positions

    if bear_dip_count >= bear_max:
        algo.debug(f"SKIP BEAR DIP: max bear positions ({bear_dip_count}/{bear_max})")
        return
    if algo.portfolio.cash < 3000:
        algo.debug(f"SKIP BEAR DIP: insufficient cash ${algo.portfolio.cash:.0f}")
        return

    # ── MARKET BREADTH CHECK ──
    if algo.config.bear_dip_buy.breadth_enabled:
        above_ema_count = 0
        total_checked = 0
        for s in algo._core_symbols:
            ind = algo._indicators.get(s)
            if ind and ind.get('ema_50') and ind['ema_50'].is_ready:
                total_checked += 1
                try:
                    if float(algo.securities[s].price) > ind['ema_50'].current.value:
                        above_ema_count += 1
                except Exception:
                    pass
        breadth = above_ema_count / total_checked if total_checked > 0 else 0
        algo.debug(f"BREADTH: {above_ema_count}/{total_checked} = {breadth:.1%} above EMA50")
        if breadth < algo.config.bear_dip_buy.min_breadth_pct:
            algo.debug(f"SKIP BEAR DIP: breadth {breadth:.1%} < min {algo.config.bear_dip_buy.min_breadth_pct:.0%}")
            return

    # Scan symbols for discount entries
    scan_symbols = algo._core_symbols if algo.config.bear_dip_buy.core_only else algo._all_symbols
    bear_candidates = []
    algo.debug(f"BEAR DIP SCAN: {len(scan_symbols)} symbols")

    for symbol in scan_symbols:
        try:
            if symbol == algo.spy or algo.portfolio[symbol].invested:
                continue
            if not algo._is_symbol_ready(symbol):
                continue
            indicators = algo._indicators.get(symbol)
            if not indicators:
                continue

            rsi = indicators["rsi"]
            ema_50 = indicators["ema_50"]
            vol_sma = indicators.get("volume_sma")
            current_price = float(algo.securities[symbol].price)
            if current_price <= 0:
                continue

            rsi_val = rsi.current.value
            rsi_prev = rsi.previous.value if rsi.previous else rsi_val
            ema_val = ema_50.current.value

            # Oversold + trading at discount to EMA50
            if ema_val <= 0:
                continue
            discount = (ema_val - current_price) / ema_val

            if rsi_val >= algo.config.bear_dip_buy.symbol_rsi_max or discount < algo.config.bear_dip_buy.symbol_discount_pct:
                continue

            # ── BOUNCE CONFIRMATION: RSI must be turning up ──
            if algo.config.bear_dip_buy.require_bounce and rsi_val <= rsi_prev:
                algo.debug(f"SKIP {symbol.value}: no bounce (RSI {rsi_prev:.1f}\u2192{rsi_val:.1f})")
                continue

            # ── VOLUME CONFIRMATION: current volume > 1.2x average ──
            if algo.config.bear_dip_buy.require_volume_spike and vol_sma and vol_sma.is_ready:
                try:
                    current_vol = float(algo.securities[symbol].volume)
                    avg_vol = vol_sma.current.value
                    if avg_vol > 0 and current_vol < avg_vol * algo.config.bear_dip_buy.volume_spike_ratio:
                        algo.debug(f"SKIP {symbol.value}: low volume ({current_vol:.0f} < {avg_vol * algo.config.bear_dip_buy.volume_spike_ratio:.0f})")
                        continue
                except Exception:
                    pass  # If volume check fails, allow entry anyway

            # Score: deeper discount + more oversold + stronger bounce = better
            bounce_strength = max(0, rsi_val - rsi_prev) / 10.0
            score = discount * (1.0 - rsi_val / 100.0) * (1.0 + bounce_strength)
            bear_candidates.append((symbol, score, discount, rsi_val))
            algo.debug(f"BEAR CANDIDATE: {symbol.value} discount={discount:.1%} RSI={rsi_val:.1f} bounce={rsi_val - rsi_prev:+.1f}")
        except Exception:
            pass

    if bear_candidates:
        bear_candidates.sort(key=lambda x: x[1], reverse=True)
        slots_available = bear_max - bear_dip_count
        for cand_symbol, _, cand_discount, cand_rsi in bear_candidates[:slots_available]:
            current_price = float(algo.securities[cand_symbol].price)
            full_qty = algo._calculate_position_size(cand_symbol, current_price)
            if full_qty <= 0:
                continue

            # ── SCALE-IN: enter with partial size ──
            if algo.config.bear_dip_buy.scale_in_enabled:
                initial_qty = max(1, int(full_qty * algo.config.bear_dip_buy.initial_size_pct))
                remaining_qty = full_qty - initial_qty
            else:
                initial_qty = full_qty
                remaining_qty = 0

            if initial_qty > 0 and algo.portfolio.cash >= current_price * initial_qty:
                ticket = algo.market_order(cand_symbol, initial_qty)
                tag = f"bear_dip|discount={cand_discount:.1%}|RSI={cand_rsi:.0f}|scale={'partial' if remaining_qty > 0 else 'full'}"
                trade_entry = f"{algo.time.strftime('%Y-%m-%d %H:%M')} BEAR-DIP BUY {cand_symbol.value} - Qty: {initial_qty}/{full_qty} @ ${current_price:.2f} | {tag}"
                algo.trade_history.append(trade_entry)
                algo.logger.info(f"BEAR DIP BUY: {cand_symbol.value} {initial_qty}/{full_qty} @ ${current_price:.2f} discount={cand_discount:.1%} RSI={cand_rsi:.0f}")

                if ticket:
                    algo._algo_managed_positions.add(cand_symbol)
                    algo._bear_dip_positions.add(cand_symbol)
                    algo.entry_time[cand_symbol] = algo.time
                    algo.highest_price[cand_symbol] = current_price
                    if remaining_qty > 0:
                        algo._bear_dip_scale_in_pending[cand_symbol] = {
                            'target_qty': remaining_qty,
                            'entry_price': current_price
                        }
