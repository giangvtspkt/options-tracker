@app.get("/api/data")
def get_options_data(tickers: str = "IREN,RKLB", contract_tickers: str = "", delta: float = 0.2):
    positions, _ = get_positions_from_github()
    cache_store = load_cached_data()
    
    primary_tickers = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    combined_ticker_set = set(primary_tickers)

    for t in contract_tickers.split(","):
        if t.strip():
            combined_ticker_set.add(t.strip().upper())

    for p in positions:
        if p.get("ticker"):
            combined_ticker_set.add(p["ticker"].strip().upper())

    combined_ticker_list = sorted(list(combined_ticker_set))
    target_periods = [7, 14, 21, 30]
    today = datetime.date.today()

    # Pre-populate from cache so nothing drops to empty
    market_data = cache_store.get("market", {}).copy()
    all_spots = cache_store.get("all_spots", {}).copy()
    results_puts = cache_store.get("puts", {str(t): {} for t in target_periods}).copy()
    results_calls = cache_store.get("calls", {str(t): {} for t in target_periods}).copy()
    live_positions = cache_store.get("live_positions", {}).copy()
    diagnostics = {}
    
    successful_fetches = 0

    for ticker in combined_ticker_list:
        is_primary = ticker in primary_tickers
        spot_price = 0.0
        expirations = []
        df_hist = None

        try:
            tkr = yf.Ticker(ticker)
            try:
                if hasattr(tkr, 'fast_info'):
                    spot_price = float(tkr.fast_info.get('last_price') or tkr.fast_info.get('lastPrice') or 0.0)
            except Exception:
                pass

            if not spot_price or spot_price <= 0:
                hist_1d = tkr.history(period="5d")
                if not hist_1d.empty:
                    spot_price = float(hist_1d['Close'].iloc[-1])

            expirations = list(tkr.options) if tkr.options else []
            df_hist = tkr.history(period="30d")
            if spot_price > 0:
                successful_fetches += 1
        except Exception as e:
            if is_primary:
                diagnostics[ticker] = f"Error reaching Yahoo Finance: {str(e)}"

        # If live fetch failed for this ticker, retain the pre-populated cache
        if not spot_price or spot_price <= 0:
            if is_primary and ticker not in all_spots:
                diagnostics[ticker] = "Could not fetch spot price and no cache available"
            continue

        all_spots[ticker] = round(spot_price, 2)
        if not expirations and is_primary:
            continue

        if df_hist is not None and not df_hist.empty and len(df_hist) >= 2:
            prev_high = float(df_hist['High'].iloc[-2])
            prev_low = float(df_hist['Low'].iloc[-2])
            prev_close = float(df_hist['Close'].iloc[-2])
            pivot = (prev_high + prev_low + prev_close) / 3.0
            s1 = (2 * pivot) - prev_high
            r1 = (2 * pivot) - prev_low
            rolling_support = float(df_hist['Low'].min())
            rolling_resistance = float(df_hist['High'].max())
        else:
            s1, r1 = spot_price * 0.95, spot_price * 1.05
            rolling_support, rolling_resistance = spot_price * 0.90, spot_price * 1.10

        if is_primary:
            market_data[ticker] = {
                "spot": round(spot_price, 2),
                "s1": round(s1, 2),
                "r1": round(r1, 2),
                "floor": round(rolling_support, 2),
                "ceiling": round(rolling_resistance, 2),
                "support": round(min(s1, rolling_support), 2),
                "resistance": round(max(r1, rolling_resistance), 2)
            }

        target_to_exp = {}
        if is_primary:
            for target in target_periods:
                closest_exp = None
                min_diff = float("inf")
                for exp in expirations:
                    try:
                        exp_date = datetime.datetime.strptime(exp, "%Y-%m-%d").date()
                    except Exception:
                        continue
                    if exp_date <= today:
                        continue
                    diff = abs((exp_date - today).days - target)
                    if diff < min_diff:
                        min_diff = diff
                        closest_exp = exp
                if closest_exp:
                    target_to_exp[target] = closest_exp

        needed_exps = set(target_to_exp.values())
        for p in positions:
            if p.get("ticker", "").upper() == ticker and p.get("exp") in expirations:
                needed_exps.add(p["exp"])

        loaded_chains = {}
        for exp in needed_exps:
            try:
                loaded_chains[exp] = tkr.option_chain(exp)
            except Exception:
                continue

        # Update live mark prices
        for p in positions:
            if p.get("ticker", "").upper() == ticker and p.get("exp") in loaded_chains:
                chain = loaded_chains[p["exp"]]
                df_opts = chain.puts if p.get("type", "").upper() == "PUT" else chain.calls
                k_target = float(p.get("strike", 0))
                
                match = df_opts[abs(df_opts["strike"] - k_target) < 0.05]
                if not match.empty:
                    row_data = match.iloc[0]
                    ask = float(row_data.get("ask", 0) or 0)
                    bid = float(row_data.get("bid", 0) or 0)
                    last_p = float(row_data.get("lastPrice", 0) or 0)
                    mark = ask if ask > 0 else (last_p if last_p > 0 else (bid if bid > 0 else 0.0))
                    
                    contract_key = f"{ticker}_{p['exp']}_{float(p.get('strike', 0)):.2f}_{p['type'].upper()}"
                    live_positions[contract_key] = round(mark, 2)

        if is_primary:
            for target, exp in target_to_exp.items():
                if exp not in loaded_chains:
                    continue

                chain = loaded_chains[exp]
                puts, calls = chain.puts, chain.calls
                exp_date = datetime.datetime.strptime(exp, "%Y-%m-%d").date()
                actual_b_days = count_business_days(today, exp_date)

                T = max(actual_b_days, 1) / 252.0
                r = 0.05
                
                exp_short = exp_date.strftime("%b %d")
                exp_stacked = f"{exp_short}<br><span class='text-[9px] text-slate-400 font-mono'>({actual_b_days}d)</span>"

                # Puts
                if puts is not None and not puts.empty:
                    best_put = None
                    min_p_diff = float("inf")
                    for _, row in puts.iterrows():
                        try:
                            K = float(row['strike'])
                            iv = float(row.get('impliedVolatility', 0.45) or 0.45)
                            d = calc_put_delta(spot_price, K, T, r, sigma=iv)
                            if abs(d - (-delta)) < min_p_diff:
                                min_p_diff = abs(d - (-delta))
                                best_put = (row, K, iv)
                        except Exception:
                            continue

                    if best_put is not None:
                        row, k_val, iv_val = best_put
                        bid = float(row.get('bid', 0) or 0)
                        last_p = float(row.get('lastPrice', 0) or 0)
                        prem = bid if bid > 0 else last_p
                        yield_pct = (prem / k_val * 100) if k_val > 0 else 0
                        ann_pct = yield_pct * 252 / actual_b_days
                        pct_diff = ((k_val - spot_price) / spot_price) * 100

                        results_puts[str(target)][ticker] = {
                            "raw_exp": exp,
                            "exp": exp_stacked,
                            "strike": round(k_val, 2),
                            "pct_diff": f"{pct_diff:+.1f}%",
                            "iv": round((iv_val or 0.45) * 100, 1),
                            "prem": round(prem, 2),
                            "ann": round(ann_pct, 1),
                            "is_safe": k_val < market_data.get(ticker, {}).get("support", 0)
                        }

                # Calls
                if calls is not None and not calls.empty:
                    best_call = None
                    min_c_diff = float("inf")
                    for _, row in calls.iterrows():
                        try:
                            K = float(row['strike'])
                            iv = float(row.get('impliedVolatility', 0.45) or 0.45)
                            d = calc_call_delta(spot_price, K, T, r, sigma=iv)
                            if abs(d - delta) < min_c_diff:
                                min_c_diff = abs(d - delta)
                                best_call = (row, K, iv)
                        except Exception:
                            continue

                    if best_call is not None:
                        row, k_val, iv_val = best_call
                        bid = float(row.get('bid', 0) or 0)
                        last_p = float(row.get('lastPrice', 0) or 0)
                        prem = bid if bid > 0 else last_p
                        yield_pct = (prem / spot_price * 100) if spot_price > 0 else 0
                        ann_pct = yield_pct * 252 / actual_b_days
                        pct_diff = ((k_val - spot_price) / spot_price) * 100

                        results_calls[str(target)][ticker] = {
                            "raw_exp": exp,
                            "exp": exp_stacked,
                            "strike": round(k_val, 2),
                            "pct_diff": f"{pct_diff:+.1f}%",
                            "iv": round((iv_val or 0.45) * 100, 1),
                            "prem": round(prem, 2),
                            "ann": round(ann_pct, 1),
                            "is_safe": k_val > market_data.get(ticker, {}).get("resistance", 0)
                        }

    is_cached_payload = (successful_fetches == 0 and bool(cache_store))

    primary_market_data = {t: market_data[t] for t in primary_tickers if t in market_data}
    
    payload = {
        "market": primary_market_data,
        "all_spots": all_spots,
        "puts": results_puts,
        "calls": results_calls,
        "tickers": primary_tickers,
        "targets": target_periods,
        "live_positions": live_positions,
        "diagnostics": diagnostics,
        "is_cached": is_cached_payload,
        "cached_at": cache_store.get("cached_at") if is_cached_payload else datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    if successful_fetches > 0:
        save_cached_data(payload)

    return payload
