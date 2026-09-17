from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import os
import json
import base64
import requests

app = FastAPI()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")
GITHUB_FILE_PATH = os.getenv("GITHUB_FILE_PATH", "positions.json")

def get_positions_from_github():
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return [], None
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    try:
        r = requests.get(url, headers=headers, timeout=4)
        if r.status_code == 200:
            data = r.json()
            content = base64.b64decode(data['content']).decode('utf-8')
            return json.loads(content), data.get('sha')
    except Exception:
        pass
    return [], None

def save_positions_to_github(positions):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return False
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    _, sha = get_positions_from_github()
    content_str = json.dumps(positions, indent=2)
    encoded = base64.b64encode(content_str.encode('utf-8')).decode('utf-8')
    payload = {"message": "Update positions storage", "content": encoded}
    if sha:
        payload["sha"] = sha
    try:
        res = requests.put(url, headers=headers, json=payload, timeout=4)
        return res.status_code in [200, 201]
    except Exception:
        return False

class PositionModel(BaseModel):
    id: int
    action: str
    type: str
    ticker: str
    strike: float
    prem: float
    qty: int
    exp: str

HTML_CONTENT = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>Options Yield Tracker</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-100 text-slate-800 p-2.5 sm:p-4 font-sans text-xs">
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

  <div class="bg-white p-3.5 rounded-xl shadow-sm mb-3 border border-slate-200">
    <div class="flex items-center justify-between mb-2">
      <h2 class="text-xs font-bold text-slate-800 flex items-center gap-1">
        <span>💼</span> Performance &amp; Active Positions
      </h2>
      <button onclick="toggleAddForm()" id="toggleFormBtn" class="bg-slate-800 text-white text-[10px] font-bold px-2.5 py-1 rounded-md">
        + Add Position
      </button>
    </div>

    <div class="grid grid-cols-2 sm:grid-cols-5 gap-2 mb-3">
      <div class="bg-slate-50 border border-slate-200 rounded-lg p-2 text-center">
        <div class="text-[10px] font-bold text-slate-500">Total Realized</div>
        <div id="totalRealized" class="text-xs font-extrabold font-mono text-slate-700">$0.00</div>
      </div>
      <div class="bg-slate-50 border border-slate-200 rounded-lg p-2 text-center">
        <div class="text-[10px] font-bold text-slate-500">Last Mo Realized</div>
        <div id="lastMonthRealized" class="text-xs font-extrabold font-mono text-slate-700">$0.00</div>
      </div>
      <div class="bg-slate-50 border border-slate-200 rounded-lg p-2 text-center">
        <div class="text-[10px] font-bold text-slate-500">This Mo Realized</div>
        <div id="thisMonthRealized" class="text-xs font-extrabold font-mono text-slate-700">$0.00</div>
      </div>
      <div class="bg-slate-50 border border-slate-200 rounded-lg p-2 text-center">
        <div class="text-[10px] font-bold text-slate-500">This Mo Unrealized</div>
        <div id="thisMonthUnrealized" class="text-xs font-extrabold font-mono text-slate-700">$0.00</div>
      </div>
      <div class="bg-slate-50 border border-slate-200 rounded-lg p-2 text-center col-span-2 sm:col-span-1">
        <div class="text-[10px] font-bold text-slate-500">Total Unrealized</div>
        <div id="totalUnrealized" class="text-xs font-extrabold font-mono text-slate-700">$0.00</div>
      </div>
    </div>

    <div id="positionForm" class="hidden bg-slate-50 p-2.5 rounded-lg border border-slate-200 mb-3 space-y-2">
      <div class="grid grid-cols-3 gap-2">
        <div>
          <label class="text-[10px] font-bold text-slate-500">Action</label>
          <select id="posAction" class="w-full border rounded p-1.5 text-xs bg-white">
            <option value="SELL">Sell (Write)</option>
            <option value="BUY">Buy (Long)</option>
          </select>
        </div>
        <div>
          <label class="text-[10px] font-bold text-slate-500">Type</label>
          <select id="posType" class="w-full border rounded p-1.5 text-xs bg-white">
            <option value="PUT">PUT (CSP)</option>
            <option value="CALL">CALL (CC)</option>
          </select>
        </div>
        <div>
          <label class="text-[10px] font-bold text-slate-500">Ticker</label>
          <input id="posTicker" type="text" placeholder="IREN" class="w-full border rounded p-1.5 text-xs uppercase font-semibold">
        </div>
      </div>

      <div class="grid grid-cols-3 gap-2">
        <div>
          <label class="text-[10px] font-bold text-slate-500">Strike ($)</label>
          <input id="posStrike" type="number" step="0.5" placeholder="40" class="w-full border rounded p-1.5 text-xs">
        </div>
        <div>
          <label class="text-[10px] font-bold text-slate-500">Premium ($)</label>
          <input id="posPrem" type="number" step="0.01" placeholder="1.25" class="w-full border rounded p-1.5 text-xs">
        </div>
        <div>
          <label class="text-[10px] font-bold text-slate-500">Qty</label>
          <input id="posQty" type="number" step="1" value="1" class="w-full border rounded p-1.5 text-xs">
        </div>
      </div>

      <div>
        <label class="text-[10px] font-bold text-slate-500">Expiration Date</label>
        <input id="posExp" type="date" class="w-full border rounded p-1.5 text-xs bg-white">
      </div>

      <div class="flex gap-2 pt-1">
        <button onclick="savePosition()" class="bg-emerald-600 active:bg-emerald-700 text-white font-bold py-1.5 px-3 rounded text-xs flex-1">Save</button>
        <button onclick="toggleAddForm()" class="bg-slate-300 text-slate-700 font-bold py-1.5 px-3 rounded text-xs">Cancel</button>
      </div>
    </div>

    <div class="overflow-x-auto border border-slate-200 rounded-lg">
      <table class="w-full text-left text-[10px]">
        <thead class="bg-slate-100 border-b border-slate-200 text-slate-600 font-bold">
          <tr>
            <th class="p-1.5 border-r">Pos</th>
            <th class="p-1.5 border-r">Contract</th>
            <th class="p-1.5 border-r">Exp ▲</th>
            <th class="p-1.5 border-r">Prem</th>
            <th class="p-1.5 border-r">P/L ($)</th>
            <th class="p-1.5 border-r">Status</th>
            <th class="p-1.5 text-center">Delete</th>
          </tr>
        </thead>
        <tbody id="positionsBody">
          <tr><td colspan="7" class="p-2 text-center text-slate-400">Loading positions...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <div class="flex items-center gap-1.5 mb-3 overflow-x-auto py-1">
    <span class="text-[11px] font-bold text-slate-500 mr-1">View:</span>
    <div id="tickerPills" class="flex gap-1.5"></div>
  </div>

  <div class="mb-4">
    <h2 class="text-xs font-bold text-sky-900 bg-sky-100 p-2.5 rounded-t-lg border-t border-x border-sky-200">
      📉 Cash-Secured Puts
    </h2>
    <div class="overflow-x-auto bg-white border border-slate-200 rounded-b-lg shadow-sm">
      <table class="w-full text-left" id="putsTable">
        <tbody id="putsBody">
          <tr><td class="p-4 text-center text-slate-400">Ready to load quotes...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <div class="mb-6">
    <h2 class="text-xs font-bold text-amber-900 bg-amber-100 p-2.5 rounded-t-lg border-t border-x border-amber-200">
      📈 Covered Calls
    </h2>
    <div class="overflow-x-auto bg-white border border-slate-200 rounded-b-lg shadow-sm">
      <table class="w-full text-left" id="callsTable">
        <tbody id="callsBody">
          <tr><td class="p-4 text-center text-slate-400">Ready to load quotes...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <script>
    let globalData = null;
    let selectedTicker = 'ALL';
    let cloudPositions = [];
    const TARGET_DAYS = [7, 14, 21, 30];

    const EXP_COLOR_PALETTE = [
      'bg-indigo-50/80',
      'bg-amber-50/80',
      'bg-emerald-50/80',
      'bg-purple-50/80',
      'bg-rose-50/80',
      'bg-sky-50/80',
      'bg-teal-50/80'
    ];

    function getExpColor(expKey, map) {
      if (!expKey) return 'hover:bg-slate-50';
      if (!map[expKey]) {
        const idx = Object.keys(map).length % EXP_COLOR_PALETTE.length;
        map[expKey] = EXP_COLOR_PALETTE[idx];
      }
      return map[expKey];
    }

    function toggleAddForm() {
      document.getElementById('positionForm').classList.toggle('hidden');
    }

    async function loadCloudPositions() {
      try {
        const res = await fetch('/api/positions');
        cloudPositions = await res.json();
        renderPositionsAndPL();
      } catch (e) {
        document.getElementById('positionsBody').innerHTML = '<tr><td colspan="7" class="p-2 text-center text-slate-400">No active positions saved.</td></tr>';
      }
    }

    async function savePosition() {
      const action = document.getElementById('posAction').value;
      const type = document.getElementById('posType').value;
      const ticker = document.getElementById('posTicker').value.trim().toUpperCase();
      const strike = parseFloat(document.getElementById('posStrike').value);
      const prem = parseFloat(document.getElementById('posPrem').value);
      const qty = parseInt(document.getElementById('posQty').value) || 1;
      const exp = document.getElementById('posExp').value;

      if (!ticker || isNaN(strike) || isNaN(prem) || !exp) {
        alert('Please fill all fields properly.');
        return;
      }

      const res = await fetch('/api/positions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: Date.now(), action, type, ticker, strike, prem, qty, exp })
      });

      if (res.ok) {
        toggleAddForm();
        loadCloudPositions();
      }
    }

    async function deletePosition(id) {
      if (!confirm("Delete this position?")) return;
      const res = await fetch(`/api/positions/${id}`, { method: 'DELETE' });
      if (res.ok) loadCloudPositions();
    }

    // Normal CDF calculation for Delta
    function normCdf(x) {
      const a1 =  0.254829592, a2 = -0.284496736, a3 =  1.421413741;
      const a4 = -1.453152027, a5 =  1.061405429, p  =  0.3275911;
      const sign = x < 0 ? -1 : 1;
      x = Math.abs(x) / Math.sqrt(2.0);
      const t = 1.0 / (1.0 + p * x);
      const y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * Math.exp(-x * x);
      return 0.5 * (1.0 + sign * y);
    }

    function calcPutDelta(S, K, T, r, sigma) {
      if (T <= 0 || sigma <= 0 || S <= 0 || K <= 0) return -0.5;
      const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * Math.sqrt(T));
      return normCdf(d1) - 1.0;
    }

    function calcCallDelta(S, K, T, r, sigma) {
      if (T <= 0 || sigma <= 0 || S <= 0 || K <= 0) return 0.5;
      const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * Math.sqrt(T));
      return normCdf(d1);
    }

    function countBusinessDays(startDate, endDate) {
      let count = 0;
      let cur = new Date(startDate);
      cur.setDate(cur.getDate() + 1);
      while (cur <= endDate) {
        const day = cur.getDay();
        if (day !== 0 && day !== 6) count++;
        cur.setDate(cur.getDate() + 1);
      }
      return Math.max(count, 1);
    }

    // Direct client-side fetch bypasses Render IP rate-limiting
    async function fetchTickerOptions(ticker) {
      const targetUrl = `https://query2.finance.yahoo.com/v7/finance/options/${ticker}`;
      const proxyUrl = `https://api.allorigins.win/raw?url=${encodeURIComponent(targetUrl)}`;

      let res;
      try {
        res = await fetch(targetUrl);
        if (!res.ok) throw new Error();
      } catch (e) {
        res = await fetch(proxyUrl);
      }
      return await res.json();
    }

    async function fetchData() {
      const btn = document.getElementById('refreshBtn');
      const status = document.getElementById('status');
      btn.disabled = true;
      status.innerText = "Fetching live market data...";

      const tickerInput = document.getElementById('tickers').value;
      const tickers = tickerInput.split(',').map(t => t.trim().toUpperCase()).filter(Boolean);
      const deltaTarget = parseFloat(document.getElementById('delta').value) || 0.15;
      const today = new Date();

      const market = {};
      const puts = { '7': {}, '14': {}, '21': {}, '30': {} };
      const calls = { '7': {}, '14': {}, '21': {}, '30': {} };

      try {
        for (const ticker of tickers) {
          const raw = await fetchTickerOptions(ticker);
          const resultData = raw.optionChain?.result?.[0];
          if (!resultData) continue;

          const spot = resultData.quote?.regularMarketPrice || 0;
          const expTimestamps = resultData.expirationDates || [];
          market[ticker] = { spot };

          const targetToTs = {};
          for (const tgt of TARGET_DAYS) {
            let closest = null;
            let minDiff = Infinity;
            for (const ts of expTimestamps) {
              const expDate = new Date(ts * 1000);
              if (expDate <= today) continue;
              const diff = Math.abs((expDate - today) / (1000 * 60 * 60 * 24) - tgt);
              if (diff < minDiff) {
                minDiff = diff;
                closest = ts;
              }
            }
            if (closest) targetToTs[tgt] = closest;
          }

          const optionsList = resultData.options?.[0] || {};
          const currentPuts = optionsList.puts || [];
          const currentCalls = optionsList.calls || [];

          for (const tgt of TARGET_DAYS) {
            const ts = targetToTs[tgt];
            if (!ts) continue;

            const expDate = new Date(ts * 1000);
            const bDays = countBusinessDays(today, expDate);
            const T = bDays / 252.0;
            const expMonth = expDate.toLocaleString('en-US', { month: 'short' });
            const expDay = String(expDate.getDate()).padStart(2, '0');
            const expStacked = `${expMonth} ${expDay}<br><span class='text-[9px] text-slate-400 font-mono'>(${bDays}d)</span>`;
            const rawExp = expDate.toISOString().split('T')[0];

            // Best Put
            let bestPut = null, minPDiff = Infinity;
            for (const p of currentPuts) {
              const K = p.strike, iv = p.impliedVolatility || 0.5;
              const d = calcPutDelta(spot, K, T, 0.05, iv);
              const diff = Math.abs(d - (-deltaTarget));
              if (diff < minPDiff) {
                minPDiff = diff;
                bestPut = p;
              }
            }

            if (bestPut) {
              const prem = bestPut.bid > 0 ? bestPut.bid : (bestPut.lastPrice || 0);
              const yieldPct = bestPut.strike > 0 ? (prem / bestPut.strike) * 100 : 0;
              const ann = (yieldPct * 252) / bDays;
              const pctDiff = ((bestPut.strike - spot) / spot) * 100;
              puts[String(tgt)][ticker] = {
                raw_exp: rawExp,
                exp: expStacked,
                strike: bestPut.strike.toFixed(2),
                pct_diff: (pctDiff >= 0 ? '+' : '') + pctDiff.toFixed(1) + '%',
                iv: ((bestPut.impliedVolatility || 0) * 100).toFixed(1),
                prem: prem.toFixed(2),
                ann: ann.toFixed(1)
              };
            }

            // Best Call
            let bestCall = null, minCDiff = Infinity;
            for (const c of currentCalls) {
              const K = c.strike, iv = c.impliedVolatility || 0.5;
              const d = calcCallDelta(spot, K, T, 0.05, iv);
              const diff = Math.abs(d - deltaTarget);
              if (diff < minCDiff) {
                minCDiff = diff;
                bestCall = c;
              }
            }

            if (bestCall) {
              const prem = bestCall.bid > 0 ? bestCall.bid : (bestCall.lastPrice || 0);
              const yieldPct = spot > 0 ? (prem / spot) * 100 : 0;
              const ann = (yieldPct * 252) / bDays;
              const pctDiff = ((bestCall.strike - spot) / spot) * 100;
              calls[String(tgt)][ticker] = {
                raw_exp: rawExp,
                exp: expStacked,
                strike: bestCall.strike.toFixed(2),
                pct_diff: (pctDiff >= 0 ? '+' : '') + pctDiff.toFixed(1) + '%',
                iv: ((bestCall.impliedVolatility || 0) * 100).toFixed(1),
                prem: prem.toFixed(2),
                ann: ann.toFixed(1)
              };
            }
          }
        }

        globalData = { market, tickers, targets: TARGET_DAYS, puts, calls };
        renderPills(tickers);
        renderBothTables();
        renderPositionsAndPL();
        status.innerText = "Updated: " + new Date().toLocaleTimeString();
      } catch (err) {
        console.error(err);
        status.innerText = "Error loading market data.";
      } finally {
        btn.disabled = false;
      }
    }

    function renderPills(tickers) {
      const pillsContainer = document.getElementById('tickerPills');
      pillsContainer.innerHTML = '';
      const allBtn = document.createElement('button');
      allBtn.className = `px-2.5 py-1 rounded-full font-bold text-[11px] border ${selectedTicker === 'ALL' ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-slate-700 border-slate-300'}`;
      allBtn.innerText = 'All Side-by-Side';
      allBtn.onclick = () => { selectedTicker = 'ALL'; renderPills(tickers); renderBothTables(); };
      pillsContainer.appendChild(allBtn);

      tickers.forEach(t => {
        const btn = document.createElement('button');
        btn.className = `px-2.5 py-1 rounded-full font-bold text-[11px] border ${selectedTicker === t ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-slate-700 border-slate-300'}`;
        btn.innerText = t;
        btn.onclick = () => { selectedTicker = t; renderPills(tickers); renderBothTables(); };
        pillsContainer.appendChild(btn);
      });
    }

    function renderBothTables() {
      if (!globalData) return;
      const displayTickers = selectedTicker === 'ALL' ? globalData.tickers : [selectedTicker];
      renderTable('putsBody', globalData.puts, displayTickers, globalData.targets);
      renderTable('callsBody', globalData.calls, displayTickers, globalData.targets);
    }

    function renderTable(elementId, results, tickers, targets) {
      const tbody = document.getElementById(elementId);
      tbody.innerHTML = '';

      const tickerColors = [
        { header: 'bg-slate-700 text-white', sub: 'bg-slate-100 text-slate-700' },
        { header: 'bg-indigo-900 text-white', sub: 'bg-indigo-50 text-indigo-950' }
      ];

      let headHtml = `<tr class="border-b text-[11px]"><th class="p-2 border-r-2 border-r-slate-400 bg-slate-200">Target</th>`;
      tickers.forEach((t, i) => { 
        const c = tickerColors[i % tickerColors.length];
        headHtml += `<th class="p-2 border-r-2 border-r-slate-400 text-center tracking-wider font-extrabold ${c.header}" colspan="5">${t}</th>`; 
      });
      headHtml += `</tr><tr class="border-b text-[10px] font-semibold"><th class="p-1 border-r-2 border-r-slate-400 bg-slate-100"></th>`;
      tickers.forEach((_, i) => { 
        const c = tickerColors[i % tickerColors.length];
        headHtml += `
          <th class="p-1.5 border-r text-center whitespace-nowrap ${c.sub}">Exp</th>
          <th class="p-1.5 border-r whitespace-nowrap ${c.sub}">Strike (% Spot)</th>
          <th class="p-1.5 border-r whitespace-nowrap ${c.sub}">IV %</th>
          <th class="p-1.5 border-r whitespace-nowrap ${c.sub}">Prem</th>
          <th class="p-1.5 border-r-2 border-r-slate-400 whitespace-nowrap ${c.sub}">Ann %</th>
        `; 
      });
      headHtml += `</tr>`;
      tbody.innerHTML += headHtml;

      const tableColorMap = {};
      targets.forEach(tgt => {
        const targetKey = String(tgt);
        const targetDict = results[tgt] || results[targetKey] || {};

        let rowExpKey = '';
        for (const t of tickers) {
          if (targetDict[t]?.raw_exp) {
            rowExpKey = targetDict[t].raw_exp;
            break;
          }
        }

        const rowBg = getExpColor(rowExpKey, tableColorMap);
        const badge = (tgt === 21 || tgt === 30) ? '★ ' : '';

        let rowHtml = `<tr class="border-b ${rowBg}"><td class="p-2 border-r-2 border-r-slate-400 whitespace-nowrap font-bold text-slate-700">${badge}${tgt}d</td>`;
        tickers.forEach(t => {
          const item = targetDict[t];
          if (item) {
            rowHtml += `
              <td class="p-1 border-r text-center leading-tight text-[10px] text-slate-700">${item.exp}</td>
              <td class="p-1.5 border-r whitespace-nowrap font-semibold">$${item.strike} <span class="text-[9px]">(${item.pct_diff})</span></td>
              <td class="p-1.5 border-r whitespace-nowrap text-slate-500 font-mono">${item.iv}%</td>
              <td class="p-1.5 border-r whitespace-nowrap font-bold">$${item.prem}</td>
              <td class="p-1.5 border-r-2 border-r-slate-400 whitespace-nowrap text-emerald-700 font-bold">${item.ann}%</td>
            `;
          } else {
            rowHtml += `<td class="p-1.5 border-r-2 border-r-slate-400 text-center text-slate-400" colspan="5">-</td>`;
          }
        });
        rowHtml += `</tr>`;
        tbody.innerHTML += rowHtml;
      });
    }

    function renderPositionsAndPL() {
      const tbody = document.getElementById('positionsBody');
      const now = new Date();
      const currentYear = now.getFullYear();
      const currentMonth = now.getMonth();

      let lastMonthYear = currentYear;
      let lastMonth = currentMonth - 1;
      if (lastMonth < 0) {
        lastMonth = 11;
        lastMonthYear--;
      }

      const filteredPositions = [];
      cloudPositions.forEach(p => {
        const parts = p.exp.split('-');
        const pYear = parseInt(parts[0], 10);
        const pMonth = parseInt(parts[1], 10) - 1;
        if (pYear > currentYear || (pYear === currentYear && pMonth >= currentMonth)) {
          filteredPositions.push(p);
        }
      });

      filteredPositions.sort((a, b) => {
        const dateDiff = new Date(a.exp) - new Date(b.exp);
        if (dateDiff !== 0) return dateDiff;
        return a.ticker.localeCompare(b.ticker);
      });

      if (filteredPositions.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="p-2 text-center text-slate-400">No active positions for this month.</td></tr>';
        return;
      }

      tbody.innerHTML = '';
      const todayStr = now.toISOString().split('T')[0];
      let totalRealized = 0, thisMonthRealized = 0, lastMonthRealized = 0, thisMonthUnrealized = 0, totalUnrealized = 0;
      const posColorMap = {};

      filteredPositions.forEach(p => {
        const parts = p.exp.split('-');
        const pYear = parseInt(parts[0], 10);
        const pMonth = parseInt(parts[1], 10) - 1;
        const isExpired = p.exp < todayStr;
        const isThisMonth = (pYear === currentYear && pMonth === currentMonth);
        const spot = globalData?.market?.[p.ticker]?.spot || null;

        let pl = 0;
        let statusHtml = '';

        if (isExpired) {
          if (p.action === 'SELL') {
            if ((p.type === 'PUT' && (!spot || spot >= p.strike)) || (p.type === 'CALL' && (!spot || spot <= p.strike))) {
              pl = p.prem * 100 * p.qty;
              statusHtml = '<span class="px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800 font-bold">Expired (Win)</span>';
            } else {
              const intrinsic = p.type === 'PUT' ? Math.max(p.strike - spot, 0) : Math.max(spot - p.strike, 0);
              pl = (p.prem - intrinsic) * 100 * p.qty;
              statusHtml = '<span class="px-1.5 py-0.5 rounded bg-rose-100 text-rose-800 font-bold">Assigned</span>';
            }
          } else {
            const intrinsic = p.type === 'CALL' ? Math.max((spot || 0) - p.strike, 0) : Math.max(p.strike - (spot || 0), 0);
            pl = (intrinsic - p.prem) * 100 * p.qty;
            statusHtml = '<span class="px-1.5 py-0.5 rounded bg-slate-200 text-slate-700 font-bold">Closed</span>';
          }

          totalRealized += pl;
          if (isThisMonth) thisMonthRealized += pl;
          else if (pYear === lastMonthYear && pMonth === lastMonth) lastMonthRealized += pl;
        } else {
          pl = p.action === 'SELL' ? (p.prem * 100 * p.qty) : 0;
          statusHtml = '<span class="px-1.5 py-0.5 rounded bg-blue-100 text-blue-800 font-bold">Active</span>';
          totalUnrealized += pl;
          if (isThisMonth) thisMonthUnrealized += pl;
        }

        const plColor = pl >= 0 ? 'text-emerald-700' : 'text-rose-700';
        const plPrefix = pl >= 0 ? '+$' : '-$';
        const plDisplay = `${plPrefix}${Math.abs(pl).toFixed(2)}`;

        const actionBadge = p.action === 'SELL'
          ? '<span class="px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800 font-bold">SELL</span>'
          : '<span class="px-1.5 py-0.5 rounded bg-blue-100 text-blue-800 font-bold">BUY</span>';

        const rowBg = getExpColor(p.exp, posColorMap);
        const tr = document.createElement('tr');
        tr.className = `border-b ${rowBg}`;
        tr.innerHTML = `
          <td class="p-1.5 border-r whitespace-nowrap">${actionBadge}</td>
          <td class="p-1.5 border-r whitespace-nowrap font-bold">${p.ticker} $${p.strike} ${p.type} (x${p.qty})</td>
          <td class="p-1.5 border-r whitespace-nowrap text-slate-700 font-mono font-bold">${p.exp}</td>
          <td class="p-1.5 border-r whitespace-nowrap font-mono">$${p.prem.toFixed(2)}</td>
          <td class="p-1.5 border-r whitespace-nowrap font-mono font-bold ${plColor}">${plDisplay}</td>
          <td class="p-1.5 border-r whitespace-nowrap">${statusHtml}</td>
          <td class="p-1.5 text-center">
            <button onclick="deletePosition(${p.id})" class="text-rose-600 hover:text-rose-800 font-bold">✕</button>
          </td>
        `;
        tbody.appendChild(tr);
      });

      const fmt = (val) => `${val >= 0 ? '+$' : '-$'}${Math.abs(val).toFixed(2)}`;
      document.getElementById('totalRealized').innerText = fmt(totalRealized);
      document.getElementById('lastMonthRealized').innerText = fmt(lastMonthRealized);
      document.getElementById('thisMonthRealized').innerText = fmt(thisMonthRealized);
      document.getElementById('thisMonthUnrealized').innerText = fmt(thisMonthUnrealized);
      document.getElementById('totalUnrealized').innerText = fmt(totalUnrealized);
    }

    loadCloudPositions();
    fetchData();
  </script>
</body>
</html>
"""

@app.get("/api/positions")
def read_positions():
    positions, _ = get_positions_from_github()
    return positions

@app.post("/api/positions")
def create_position(pos: PositionModel):
    positions, _ = get_positions_from_github()
    positions.append(pos.model_dump())
    success = save_positions_to_github(positions)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save to GitHub")
    return {"status": "success"}

@app.delete("/api/positions/{pos_id}")
def remove_position(pos_id: int):
    positions, _ = get_positions_from_github()
    positions = [p for p in positions if p.get("id") != pos_id]
    success = save_positions_to_github(positions)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete from GitHub")
    return {"status": "success"}

@app.get("/", response_class=HTMLResponse)
def render_index():
    return HTMLResponse(content=HTML_CONTENT)
