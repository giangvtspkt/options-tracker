from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import yfinance as yf
import math
import datetime

app = FastAPI()

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

HTML_CONTENT = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>Options Yield Tracker</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-100 text-slate-800 p-2.5 sm:p-4 font-sans text-xs">
  <!-- Controls Card -->
  <div class="bg-white p-3.5 rounded-xl shadow-sm mb-3 border border-slate-200">
    <div class="grid grid-cols-2 gap-2 mb-2">
      <div>
        <label class="text-[11px] font-bold text-slate-500 uppercase tracking-wide">Tickers</label>
        <input id="tickers" type="text" value="IREN, RKLB" class="w-full border rounded p-2 text-sm uppercase font-semibold">
      </div>
      <div>
        <label class="text-[11px] font-bold text-slate-500 uppercase tracking-wide">Delta</label>
        <input id="delta" type="number" step="0.01" value="0.15" class="w-full border rounded p-2 text-sm font-semibold">
      </div>
    </div>
    <button onclick="fetchData()" id="refreshBtn" class="bg-blue-600 active:bg-blue-700 text-white font-bold py-2 px-4 rounded-lg text-sm w-full mt-1">
      Refresh Data
    </button>
    <div id="status" class="text-[11px] text-slate-500 mt-1.5 text-right font-medium">Ready</div>
  </div>

  <!-- Spot & Key Levels Bar -->
  <div id="levels" class="bg-blue-50 border border-blue-200 text-blue-900 p-3 rounded-xl space-y-1 mb-3 font-mono leading-relaxed">
    Loading market levels...
  </div>

  <!-- Strategy Guidelines & Technical Notes Card (Identical to Desktop) -->
  <div class="bg-amber-50 border border-amber-200 text-amber-950 p-3 rounded-xl mb-3 space-y-1.5 leading-relaxed shadow-sm">
    <div class="font-bold text-amber-900 flex items-center gap-1">
      <span>📌</span> Technical Definitions &amp; Safe Zone Guidelines:
    </div>
    <p><strong class="text-blue-700">• Key Levels:</strong> S1/Floor = Support floors; R1/Ceiling = Resistance ceilings.</p>
    <p><strong class="text-emerald-700">• Safe Zone CSP (Puts):</strong> Highlighted in green when Strike &lt; Support/Floor (safely cushioned below the technical support floor).</p>
    <p><strong class="text-rose-700">• Safe Zone CC (Calls):</strong> Highlighted in green when Strike &gt; Resistance/Ceiling (safely above resistance to keep shares and maximize upside buffer).</p>
    <p><strong class="text-sky-700">• Optimal Expiration:</strong> The 30-45 Day target row is highlighted as the primary sweet spot for theta decay.</p>
  </div>

  <!-- Puts Table -->
  <div class="mb-4">
    <h2 class="text-xs font-bold text-sky-900 bg-sky-100 p-2.5 rounded-t-lg border-t border-x border-sky-200">
      📉 Cash-Secured Puts (Green = Strike &lt; Support/Floor)
    </h2>
    <div class="overflow-x-auto bg-white border border-slate-200 rounded-b-lg shadow-sm">
      <table class="w-full text-left" id="putsTable">
        <tbody id="putsBody"></tbody>
      </table>
    </div>
  </div>

  <!-- Calls Table -->
  <div class="mb-6">
    <h2 class="text-xs font-bold text-amber-900 bg-amber-100 p-2.5 rounded-t-lg border-t border-x border-amber-200">
      📈 Covered Calls (Green = Strike &gt; Resistance/Ceiling)
    </h2>
    <div class="overflow-x-auto bg-white border border-slate-200 rounded-b-lg shadow-sm">
      <table class="w-full text-left" id="callsTable">
        <tbody id="callsBody"></tbody>
      </table>
    </div>
  </div>

  <script>
    async function fetchData() {
      const btn = document.getElementById('refreshBtn');
      const status = document.getElementById('status');
      btn.disabled = true;
      status.innerText = "Fetching live quotes...";

      const tickers = document.getElementById('tickers').value;
      const delta = document.getElementById('delta').value;

      try {
        const res = await fetch(`/api/data?tickers=${encodeURIComponent(tickers)}&delta=${delta}`);
        if (!res.ok) throw new Error("API error");
        const data = await res.json();

        let levelsHtml = '';
        for (const [t, m] of Object.entries(data.market)) {
          levelsHtml += `<div><strong>${t}</strong> Spot: $${m.spot} | Supp: [S1: $${m.s1} / Floor: $${m.floor}] | Res: [R1: $${m.r1} / Ceiling: $${m.ceiling}]</div>`;
        }
        document.getElementById('levels').innerHTML = levelsHtml || 'No data found.';

        renderTable('putsBody', data.puts, data.tickers, data.targets);
        renderTable('callsBody', data.calls, data.tickers, data.targets);
        status.innerText = "Updated: " + new Date().toLocaleTimeString();
      } catch (err) {
        status.innerText = "Fetch error";
      } finally {
        btn.disabled = false;
      }
    }

    function renderTable(elementId, results, tickers, targets) {
      const tbody = document.getElementById(elementId);
      tbody.innerHTML = '';

      // Main header row
      let headHtml = `<tr class="bg-slate-200 font-bold border-b text-[11px]"><th class="p-2 border-r">Target</th>`;
      tickers.forEach(t => { 
        headHtml += `<th class="p-2 border-r text-center" colspan="5">${t}</th>`; 
      });
      headHtml += `</tr>`;

      // Sub-header row with IV included
      headHtml += `<tr class="bg-slate-100 border-b text-[10px] text-slate-600 font-semibold"><th class="p-1 border-r"></th>`;
      tickers.forEach(() => { 
        headHtml += `
          <th class="p-1.5 border-r whitespace-nowrap">Exp</th>
          <th class="p-1.5 border-r whitespace-nowrap">Strike (% Spot)</th>
          <th class="p-1.5 border-r whitespace-nowrap">IV %</th>
          <th class="p-1.5 border-r whitespace-nowrap">Prem</th>
          <th class="p-1.5 border-r whitespace-nowrap">Ann %</th>
        `; 
      });
      headHtml += `</tr>`;
      tbody.innerHTML += headHtml;

      // Table data rows
      targets.forEach(tgt => {
        const isSweetSpot = (tgt === 30 || tgt === 45);
        let rowClass = isSweetSpot ? 'bg-emerald-50/80 font-semibold' : 'hover:bg-slate-50';
        let badge = isSweetSpot ? '★ ' : '';

        let rowHtml = `<tr class="border-b ${rowClass}"><td class="p-2 border-r whitespace-nowrap font-bold text-slate-700">${badge}${tgt}d</td>`;
        tickers.forEach(t => {
          const item = results[tgt] && results[tgt][t];
          if (item) {
            const strikeBg = item.is_safe ? 'bg-green-200 text-green-900 font-bold' : '';
            rowHtml += `
              <td class="p-1.5 border-r whitespace-nowrap text-[11px] text-slate-600">${item.exp}</td>
              <td class="p-1.5 border-r whitespace-nowrap ${strikeBg}">$${item.strike} <span class="text-[9px]">(${item.pct_diff})</span></td>
              <td class="p-1.5 border-r whitespace-nowrap text-slate-500 font-mono">${item.iv}%</td>
              <td class="p-1.5 border-r whitespace-nowrap font-bold">$${item.prem}</td>
              <td class="p-1.5 border-r whitespace-nowrap text-emerald-700 font-bold">${item.ann}%</td>
            `;
          } else {
            rowHtml += `<td class="p-1.5 border-r text-center text-slate-400" colspan="5">-</td>`;
          }
        });
        rowHtml += `</tr>`;
        tbody.innerHTML += rowHtml;
      });
    }

    fetchData();
    setInterval(fetchData, 60000);
  </script>
</body>
</html>
"""

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

                # Puts
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

                # Calls
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
def render_index():
    return HTMLResponse(content=HTML_CONTENT)
