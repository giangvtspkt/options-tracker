from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import yfinance as yf
import math
import datetime

app = FastAPI()
templates = Jinja2Templates(directory="templates")

def norm_cdf(x):
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

def calc_put_delta(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0: return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return norm_cdf(d1) - 1.0

def calc_call_delta(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0: return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return norm_cdf(d1)

def count_business_days(start_date, end_date):
    days = 0
    curr = start_date + datetime.timedelta(days=1)
    while curr <= end_date:
        if curr.weekday() < 5:
            days += 1
        curr += datetime.timedelta(days=1)
    return max(days, 1)

@app.get("/api/data")
def get_options_data(tickers: str = "IREN,RKLB", delta: float = 0.15):
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    target_periods = [7, 14, 30, 45, 60, 90]
    today = datetime.date.today()

    market_data = {}
    results_puts = {t: {} for t in target_periods}
    results_calls = {t: {} for t in target_periods}

    for ticker in ticker_list:
        tkr = yf.Ticker(ticker)
        try:
            spot_price = tkr.fast_info.get("lastPrice", 0)
            expirations = tkr.options
            df_hist = tkr.history(period="30d")
        except Exception:
            continue

        if not expirations or spot_price == 0:
            continue

        if not df_hist.empty and len(df_hist) >= 2:
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

        market_data[ticker] = {
            "spot": round(spot_price, 2),
            "s1": round(s1, 2),
            "r1": round(r1, 2),
            "floor": round(rolling_support, 2),
            "ceiling": round(rolling_resistance, 2),
            "support": round(min(s1, rolling_support), 2),
            "resistance": round(max(r1, rolling_resistance), 2)
        }

        for target in target_periods:
            max_b_days_limit = math.ceil(target * (5.0 / 7.0))
            closest_exp, actual_b_days = None, 0
            max_b = -1

            for exp in expirations:
                exp_date = datetime.datetime.strptime(exp, "%Y-%m-%d").date()
                if exp_date <= today: continue
                b_days = count_business_days(today, exp_date)
                if b_days <= max_b_days_limit and b_days > max_b:
                    max_b = b_days
                    closest_exp = exp
                    actual_b_days = b_days

            if closest_exp:
                try:
                    chain = tkr.option_chain(closest_exp)
                    puts, calls = chain.puts, chain.calls
                except Exception:
                    continue

                T = max(actual_b_days, 1) / 252.0
                r = 0.05

                # Put selection
                best_put = None
                min_p_diff = float("inf")
                for _, row in puts.iterrows():
                    K, iv = row['strike'], row['impliedVolatility']
                    d = calc_put_delta(spot_price, K, T, r, sigma=iv)
                    if abs(d - (-delta)) < min_p_diff:
                        min_p_diff = abs(d - (-delta))
                        best_put = row

                if best_put is not None:
                    prem = best_put['bid'] if best_put['bid'] > 0 else best_put['lastPrice']
                    yield_pct = (prem / best_put['strike'] * 100) if best_put['strike'] > 0 else 0
                    ann_pct = yield_pct * 252 / actual_b_days
                    pct_diff = ((best_put['strike'] - spot_price) / spot_price) * 100
                    results_puts[target][ticker] = {
                        "exp": f"{closest_exp} ({actual_b_days}bd)",
                        "strike": round(best_put['strike'], 2),
                        "pct_diff": f"{pct_diff:+.1f}%",
                        "iv": round(best_put['impliedVolatility'] * 100, 1),
                        "prem": round(prem, 2),
                        "ann": round(ann_pct, 1),
                        "is_safe": best_put['strike'] < market_data[ticker]["support"]
                    }

                # Call selection
                best_call = None
                min_c_diff = float("inf")
                for _, row in calls.iterrows():
                    K, iv = row['strike'], row['impliedVolatility']
                    d = calc_call_delta(spot_price, K, T, r, sigma=iv)
                    if abs(d - delta) < min_c_diff:
                        min_c_diff = abs(d - delta)
                        best_call = row

                if best_call is not None:
                    prem = best_call['bid'] if best_call['bid'] > 0 else best_call['lastPrice']
                    yield_pct = (prem / spot_price * 100) if spot_price > 0 else 0
                    ann_pct = yield_pct * 252 / actual_b_days
                    pct_diff = ((best_call['strike'] - spot_price) / spot_price) * 100
                    results_calls[target][ticker] = {
                        "exp": f"{closest_exp} ({actual_b_days}bd)",
                        "strike": round(best_call['strike'], 2),
                        "pct_diff": f"{pct_diff:+.1f}%",
                        "iv": round(best_call['impliedVolatility'] * 100, 1),
                        "prem": round(prem, 2),
                        "ann": round(ann_pct, 1),
                        "is_safe": best_call['strike'] > market_data[ticker]["resistance"]
                    }

    return {
        "market": market_data,
        "puts": results_puts,
        "calls": results_calls,
        "tickers": list(market_data.keys()),
        "targets": target_periods
    }

@app.get("/", response_class=HTMLResponse)
def render_index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})
