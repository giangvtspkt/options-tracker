from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import yfinance as yf
import math
import datetime
import time
import os
import json
import base64
import requests

app = FastAPI()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")
GITHUB_FILE_PATH = os.getenv("GITHUB_FILE_PATH", "positions.json")

# In-memory fast cache (3 minutes)
DATA_CACHE = {}
CACHE_TTL = 180

# Configure custom headers so Yahoo Finance doesn't throttle Render
YF_SESSION = requests.Session()
YF_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
})

def norm_cdf(x):
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

def calc_put_delta(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0: return -0.5
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        return norm_cdf(d1) - 1.0
    except Exception:
        return -0.5

def calc_call_delta(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0: return 0.5
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        return norm_cdf(d1)
    except Exception:
        return 0.5

def count_business_days(start_date, end_date):
    days = 0
    curr = start_date + datetime.timedelta(days=1)
    while curr <= end_date:
        if curr.weekday() < 5:
            days += 1
        curr += datetime.timedelta(days=1)
    return max(days, 1)

def get_positions_from_github():
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return [], None
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    try:
        r = requests.get(url, headers=headers, timeout=3)
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
        res = requests.put(url, headers=headers, json=payload, timeout=3)
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

  <div class="mb-3">
    <h2 class="text-xs font-bold text-blue-950 bg-blue-100/80 p-2.5 rounded-t-lg border-t border-x border-blue-200 flex items-center justify-between">
      <span>📊 Spot &amp; Key Technical Levels</span>
      <span class="text-[10px] font-normal text-blue-800">Support (Floor) | Resistance (Ceiling)</span>
    </h2>
    <div class="overflow-x-auto bg-white border border-slate-200 rounded-b-lg shadow-sm">
      <table class="w-full text-left" id="levelsTable">
        <thead class="bg-slate-50 border-b border-slate-200 text-[11px] text-slate-600">
          <tr>
            <th class="p-2 border-r font-bold">Ticker</th>
            <th class="p-2 border-r font-bold">Spot Price</th>
            <th class="p-2 border-r font-bold text-emerald-800 bg-emerald-50/50">Support Floor</th>
            <th class="p-2 border-r font-medium text-slate-600">S1 Pivot</th>
            <th class="p-2 border-r font-medium text-slate-600">30d Low</th>
            <th class="p-2 border-r font-bold text-rose-800 bg-rose-50/50">Resistance Ceiling</th>
            <th class="p-2 border-r font-medium text-slate-600">R1 Pivot</th>
            <th class="p-2 font-medium text-slate-600">30d High</th>
          </tr>
        </thead>
        <tbody id="levelsBody">
          <tr><td colspan="8" class="p-3 text-center text-slate-400">Loading market levels...</td></tr>
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
      📉 Cash-Secured Puts (Green = Strike &lt; Support/Floor)
    </h2>
    <div class="overflow-x-auto bg-white border border-slate-200 rounded-b-lg shadow-sm">
      <table class="w-full text-left" id="putsTable">
        <tbody id="putsBody">
          <tr><td class="p-4 text-center text-slate-400">Fetching options data...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <div class="mb-6">
    <h2 class="text-xs font-bold text-amber-900 bg-amber-100 p-2.5 rounded-t-lg border-t border-x border-amber-200">
      📈 Covered Calls (Green = Strike &gt; Resistance/Ceiling)
    </h2>
    <div class="overflow-x-auto bg-white border border-slate-200 rounded-b-lg shadow-sm">
      <table class="w-full text-left" id="callsTable">
        <tbody id="callsBody">
          <tr><td class="p-4 text-center text-slate-400">Fetching options data...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <script>
    let globalData = null;
    let selectedTicker = 'ALL';
    let cloudPositions = [];

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
        document.getElementById('totalRealized').innerText = "$0.00";
        document.getElementById('lastMonthRealized').innerText = "$0.00";
        document.getElementById('thisMonthRealized').innerText = "$0.00";
        document.getElementById('thisMonthUnrealized').innerText = "$0.00";
        document.getElementById('totalUnrealized').innerText = "$0.00";
        return;
      }

      tbody.innerHTML = '';
      const todayStr = now.toISOString().split('T')[0];

      let totalRealized = 0;
      let thisMonthRealized = 0;
      let lastMonthRealized = 0;
      let thisMonthUnrealized = 0;
      let totalUnrealized = 0;

      const posColorMap = {};

      filteredPositions.forEach(p => {
        const parts = p.exp.split('-');
        const pYear = parseInt(parts[0], 10);
        const pMonth = parseInt(parts[1], 10) - 1;
        const isExpired = p.exp < todayStr;
        const isThisMonth = (pYear === currentYear && pMonth === currentMonth);

        const spot = (globalData && globalData.market && globalData.market[p.ticker]) 
          ? globalData.market[p.ticker].spot 
          : null;

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
          if (isThisMonth) {
            thisMonthRealized += pl;
          } else if (pYear === lastMonthYear && pMonth === lastMonth) {
            lastMonthRealized += pl;
          }
        } else {
          if (p.action === 'SELL') {
            pl = p.prem * 100 * p.qty;
          } else {
            pl = 0;
          }
          statusHtml = '<span class="px-1.5 py-0.5 rounded bg-blue-100 text-blue-800 font-bold">Active</span>';
          totalUnrealized += pl;
          if (isThisMonth) {
            thisMonthUnrealized += pl;
          }
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
      const cls = (val) => `text-xs font-extrabold font-mono ${val >= 0 ? 'text-emerald-700' : 'text-rose-700'}`;

      const totEl = document.getElementById('totalRealized');
      totEl.innerText = fmt(totalRealized);
      totEl.className = cls(totalRealized);

      const lastEl = document.getElementById('lastMonthRealized');
      lastEl.innerText = fmt(lastMonthRealized);
      lastEl.className = cls(lastMonthRealized);

      const thisEl = document.getElementById('thisMonthRealized');
      thisEl.innerText = fmt(thisMonthRealized);
      thisEl.className = cls(thisMonthRealized);

      const thisUnEl = document.getElementById('thisMonthUnrealized');
      thisUnEl.innerText = fmt(thisMonthUnrealized);
      thisUnEl.className = cls(thisMonthUnrealized);

      const totUnEl = document.getElementById('totalUnrealized');
      totUnEl.innerText = fmt(totalUnrealized);
      totUnEl.className = cls(totalUnrealized);
    }

    async function fetchData() {
      const btn = document.getElementById('refreshBtn');
      const status = document.getElementById('status');
      btn.disabled = true;
      status.innerText = "Fetching live quotes...";

      const tickers = document.getElementById('tickers').value;
      const delta = document.getElementById('delta').value;

      try {
        const res = await fetch(`/api/data?tickers=${encodeURIComponent(tickers)}&delta=${delta}`);
        if (!res.ok) throw new Error("HTTP " + res.status);
        globalData = await res.json();

        renderLevelsTable(globalData.market);
        renderPills(globalData.tickers);
        renderBothTables();
        renderPositionsAndPL();

        status.innerText = "Updated: " + new Date().toLocaleTimeString();
      } catch (err) {
        status.innerText = "Error loading data.";
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

    function renderLevelsTable(market) {
      const tbody = document.getElementById('levelsBody');
      tbody.innerHTML = '';
      const entries = Object.entries(market || {});
      if (entries.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" class="p-3 text-center text-slate-400">No ticker data available.</td></tr>`;
        return;
      }
      entries.forEach(([ticker, m]) => {
        const tr = document.createElement('tr');
        tr.className = "border-b hover:bg-slate-50 text-[11px]";
        tr.innerHTML = `
          <td class="p-2 border-r font-bold text-slate-800 bg-slate-50">${ticker}</td>
          <td class="p-2 border-r font-bold text-blue-700 font-mono">$${m.spot}</td>
          <td class="p-2 border-r font-bold text-emerald-800 bg-emerald-50/70 font-mono">$${m.support}</td>
          <td class="p-2 border-r text-slate-600 font-mono">$${m.s1}</td>
          <td class="p-2 border-r text-slate-600 font-mono">$${m.floor}</td>
          <td class="p-2 border-r font-bold text-rose-800 bg-rose-50/70 font-mono">$${m.resistance}</td>
          <td class="p-2 border-r text-slate-600 font-mono">$${m.r1}</td>
          <td class="p-2 text-slate-600 font-mono">$${m.ceiling}</td>
        `;
        tbody.appendChild(tr);
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
        { header: 'bg-indigo-900 text-white', sub: 'bg-indigo-50 text-indigo-950' },
        { header: 'bg-teal-900 text-white', sub: 'bg-teal-50 text-teal-950' }
      ];

      let headHtml = `<tr class="border-b text-[11px]"><th class="p-2 border-r-2 border-r-slate-400 bg-slate-200">Target</th>`;
      tickers.forEach((t, i) => { 
        const c = tickerColors[i % tickerColors.length];
        headHtml += `<th class="p-2 border-r-2 border-r-slate-400 text-center tracking-wider font-extrabold ${c.header}" colspan="5">${t}</th>`; 
      });
      headHtml += `</tr>`;

      headHtml += `<tr class="border-b text-[10px] font-semibold"><th class="p-1 border-r-2 border-r-slate-400 bg-slate-100"></th>`;
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
          if (targetDict[t] && targetDict[t].raw_exp) {
            rowExpKey = targetDict[t].raw_exp;
            break;
          }
        }

        const rowBg = getExpColor(rowExpKey, tableColorMap);
        const badge = (tgt === 30 || tgt === 45) ? '★ ' : '';

        let rowHtml = `<tr class="border-b ${rowBg}"><td class="p-2 border-r-2 border-r-slate-400 whitespace-nowrap font-bold text-slate-700">${badge}${tgt}d</td>`;

        tickers.forEach(t => {
          const item = targetDict[t];
          if (item) {
            const strikeBg = item.is_safe ? 'bg-green-200 text-green-900 font-bold' : '';
            rowHtml += `
              <td class="p-1 border-r text-center leading-tight text-[10px] text-slate-700">${item.exp}</td>
              <td class="p-1.5 border-r whitespace-nowrap ${strikeBg}">$${item.strike} <span class="text-[9px]">(${item.pct_diff})</span></td>
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

    // Immediate local position loading (0ms)
    loadCloudPositions();
    fetchData();
    setInterval(fetchData, 60000);
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

@app.get("/api/data")
def get_options_data(tickers: str = "IREN,RKLB", delta: float = 0.15):
    cache_key = f"{tickers}_{delta}"
    now = time.time()
    
    if cache_key in DATA_CACHE and (now - DATA_CACHE[cache_key]["time"]) < CACHE_TTL:
        return DATA_CACHE[cache_key]["data"]

    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    target_periods = [7, 14, 30, 45, 60, 90]
    today = datetime.date.today()
    
    market_data = {}
    results_puts = {str(t): {} for t in target_periods}
    results_calls = {str(t): {} for t in target_periods}

    for ticker in ticker_list:
        try:
            tkr = yf.Ticker(ticker, session=YF_SESSION)
            spot_price = tkr.fast_info.get("lastPrice", 0)
            if not spot_price or spot_price <= 0:
                hist_1d = tkr.history(period="1d")
                if not hist_1d.empty:
                    spot_price = float(hist_1d['Close'].iloc[-1])
            
            expirations = list(tkr.options)
            df_hist = tkr.history(period="30d")
        except Exception:
            continue

        if not expirations or spot_price <= 0:
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

        # Match closest target periods to expiration dates
        target_to_exp = {}
        for target in target_periods:
            closest = None
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
                    closest = exp
            if closest:
                target_to_exp[target] = closest

        # Download option chain for UNIQUE dates only with browser user-agent
        unique_exps = set(target_to_exp.values())
        loaded_chains = {}
        for exp in unique_exps:
            try:
                loaded_chains[exp] = tkr.option_chain(exp)
            except Exception:
                continue

        # Calculate Greeks and strikes from cached chains
        for target, exp in target_to_exp.items():
            if exp not in loaded_chains:
                continue
            chain = loaded_chains[exp]
            exp_date = datetime.datetime.strptime(exp, "%Y-%m-%d").date()
            b_days = count_business_days(today, exp_date)
            T = max(b_days, 1) / 252.0
            r = 0.05

            exp_short = exp_date.strftime("%b %d")
            exp_stacked = f"{exp_short}<br><span class='text-[9px] text-slate-400 font-mono'>({b_days}d)</span>"

            # Puts
            puts = chain.puts
            if puts is not None and not puts.empty:
                best_put = None
                min_p_diff = float("inf")
                for _, row in puts.iterrows():
                    try:
                        K = float(row['strike'])
                        iv = float(row['impliedVolatility']) if ('impliedVolatility' in row and not math.isnan(row['impliedVolatility'])) else 0.5
                        d = calc_put_delta(spot_price, K, T, r, sigma=iv)
                        diff = abs(d - (-delta))
                        if diff < min_p_diff:
                            min_p_diff = diff
                            best_put = (row, K, iv)
                    except Exception:
                        continue

                if best_put is not None:
                    row, k_val, iv_val = best_put
                    bid_val = float(row.get('bid', 0)) if not math.isnan(row.get('bid', 0)) else 0
                    last_val = float(row.get('lastPrice', 0)) if not math.isnan(row.get('lastPrice', 0)) else 0
                    prem = bid_val if bid_val > 0 else last_val
                    yield_pct = (prem / k_val * 100) if k_val > 0 else 0
                    ann_pct = yield_pct * 252 / b_days
                    pct_diff = ((k_val - spot_price) / spot_price) * 100
                    results_puts[str(target)][ticker] = {
                        "raw_exp": exp,
                        "exp": exp_stacked,
                        "strike": round(k_val, 2),
                        "pct_diff": f"{pct_diff:+.1f}%",
                        "iv": round(iv_val * 100, 1),
                        "prem": round(prem, 2),
                        "ann": round(ann_pct, 1),
                        "is_safe": k_val < market_data[ticker]["support"]
                    }

            # Calls
            calls = chain.calls
            if calls is not None and not calls.empty:
                best_call = None
                min_c_diff = float("inf")
                for _, row in calls.iterrows():
                    try:
                        K = float(row['strike'])
                        iv = float(row['impliedVolatility']) if ('impliedVolatility' in row and not math.isnan(row['impliedVolatility'])) else 0.5
                        d = calc_call_delta(spot_price, K, T, r, sigma=iv)
                        diff = abs(d - delta)
                        if diff < min_c_diff:
                            min_c_diff = diff
                            best_call = (row, K, iv)
                    except Exception:
                        continue

                if best_call is not None:
                    row, k_val, iv_val = best_call
                    bid_val = float(row.get('bid', 0)) if not math.isnan(row.get('bid', 0)) else 0
                    last_val = float(row.get('lastPrice', 0)) if not math.isnan(row.get('lastPrice', 0)) else 0
                    prem = bid_val if bid_val > 0 else last_val
                    yield_pct = (prem / spot_price * 100) if spot_price > 0 else 0
                    ann_pct = yield_pct * 252 / b_days
                    pct_diff = ((k_val - spot_price) / spot_price) * 100
                    results_calls[str(target)][ticker] = {
                        "raw_exp": exp,
                        "exp": exp_stacked,
                        "strike": round(k_val, 2),
                        "pct_diff": f"{pct_diff:+.1f}%",
                        "iv": round(iv_val * 100, 1),
                        "prem": round(prem, 2),
                        "ann": round(ann_pct, 1),
                        "is_safe": k_val > market_data[ticker]["resistance"]
                    }

    result = {
        "market": market_data,
        "puts": results_puts,
        "calls": results_calls,
        "tickers": list(market_data.keys()),
        "targets": target_periods
    }
    DATA_CACHE[cache_key] = {"time": now, "data": result}
    return result

@app.get("/", response_class=HTMLResponse)
def render_index():
    return HTMLResponse(content=HTML_CONTENT)
