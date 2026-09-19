from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import yfinance as yf
import math
import datetime
import os
import json
import base64
import requests

app = FastAPI()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")
GITHUB_FILE_PATH = os.getenv("GITHUB_FILE_PATH", "positions.json")

def norm_cdf(x):
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

def calc_put_delta(S, K, T, r, sigma):
    if T <= 0 or S <= 0 or K <= 0: return 0.0
    if not sigma or math.isnan(sigma) or sigma <= 0.001: sigma = 0.45
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        return norm_cdf(d1) - 1.0
    except Exception:
        return 0.0

def calc_call_delta(S, K, T, r, sigma):
    if T <= 0 or S <= 0 or K <= 0: return 0.0
    if not sigma or math.isnan(sigma) or sigma <= 0.001: sigma = 0.45
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        return norm_cdf(d1)
    except Exception:
        return 0.0

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
    trade_date: str = ""

HTML_CONTENT = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>Options Tracker (Cloud Sync)</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-100 text-slate-800 p-2.5 sm:p-4 font-sans text-xs">

  <!-- Diagnostic Alert Banner -->
  <div id="diagBanner" class="hidden bg-amber-50 border border-amber-300 rounded-xl p-3 mb-3 text-amber-900">
    <div class="font-bold flex items-center gap-1.5 text-xs mb-1">
      <span>⚠️</span> System Notice: Option Chain Data Incomplete
    </div>
    <ul id="diagList" class="list-disc list-inside space-y-0.5 text-[11px] text-amber-800"></ul>
  </div>

  <!-- 1. Performance & Active Positions -->
  <div class="bg-white p-3.5 rounded-xl shadow-sm mb-3 border border-slate-200">
    <div class="flex flex-wrap items-center justify-between gap-2 mb-2">
      <div class="flex items-center gap-2">
        <h2 class="text-xs font-bold text-slate-800 flex items-center gap-1">
          <span>💼</span> Performance &amp; Active Positions
        </h2>
        <label class="flex items-center gap-1 text-[10px] text-slate-600 cursor-pointer bg-slate-100 px-2 py-0.5 rounded border border-slate-200 select-none">
          <input id="hideExpiredToggle" type="checkbox" onchange="toggleHideExpired(this.checked)" class="rounded text-blue-600">
          <span>Hide Expired</span>
        </label>
      </div>
      <button onclick="toggleAddForm()" id="toggleFormBtn" class="bg-slate-800 text-white text-[10px] font-bold px-2.5 py-1 rounded-md">
        + Add Position
      </button>
    </div>

    <!-- P/L Metrics Cards -->
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
        <div class="text-[10px] font-bold text-slate-500">Current Unrealized</div>
        <div id="thisMonthCurrentUnrealized" class="text-xs font-extrabold font-mono text-slate-700">$0.00</div>
      </div>
      <div class="bg-slate-50 border border-slate-200 rounded-lg p-2 text-center col-span-2 sm:col-span-1">
        <div class="text-[10px] font-bold text-slate-500">Max Unrealized</div>
        <div id="totalMaxUnrealized" class="text-xs font-extrabold font-mono text-slate-700">$0.00</div>
      </div>
    </div>

    <!-- Open Contracts & CSP Capital Summary -->
    <div class="bg-slate-50 border border-slate-200 rounded-lg p-2.5 mb-3">
      <div class="text-[11px] font-bold text-slate-600 mb-1.5 flex flex-wrap items-center justify-between gap-2">
        <span>📊 Open Contracts &amp; CSP Capital Requirement</span>
        <div class="flex items-center gap-3 text-[10px] font-semibold">
          <span id="totalOpenQty" class="text-slate-600">Total Open: 0 contracts</span>
          <span id="totalCspCapital" class="text-emerald-700 bg-emerald-100 px-2 py-0.5 rounded font-mono font-bold">Total CSP Req: $0.00</span>
        </div>
      </div>
      <div id="openSummaryCards" class="flex flex-wrap gap-2">
        <span class="text-slate-400 text-[10px]">No active open contracts.</span>
      </div>
    </div>

    <!-- Add Position Form -->
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

      <div class="grid grid-cols-2 gap-2">
        <div>
          <label class="text-[10px] font-bold text-slate-500">Trade Day (Buy/Sell Day)</label>
          <input id="posTradeDate" type="date" class="w-full border rounded p-1.5 text-xs bg-white">
        </div>
        <div>
          <label class="text-[10px] font-bold text-slate-500">Expiration Date</label>
          <input id="posExp" type="date" class="w-full border rounded p-1.5 text-xs bg-white">
        </div>
      </div>

      <div class="flex gap-2 pt-1">
        <button onclick="savePosition()" class="bg-emerald-600 active:bg-emerald-700 text-white font-bold py-1.5 px-3 rounded text-xs flex-1">Save to Cloud</button>
        <button onclick="toggleAddForm()" class="bg-slate-300 text-slate-700 font-bold py-1.5 px-3 rounded text-xs">Cancel</button>
      </div>
    </div>

    <!-- Contract Positions Table -->
    <div class="overflow-x-auto border border-slate-200 rounded-lg">
      <table class="w-full text-left text-[10px]">
        <thead class="bg-slate-100 border-b border-slate-200 text-slate-600 font-bold">
          <tr>
            <th class="p-1.5 border-r">Pos</th>
            <th class="p-1.5 border-r">Contract</th>
            <th class="p-1.5 border-r">Trade Day ✎</th>
            <th class="p-1.5 border-r">Exp ▲</th>
            <th class="p-1.5 border-r">Entry</th>
            <th class="p-1.5 border-r">Live Mark</th>
            <th class="p-1.5 border-r font-extrabold text-blue-900 bg-blue-50/70">Current P/L ($)</th>
            <th class="p-1.5 border-r">Max P/L ($)</th>
            <th class="p-1.5 border-r">Status / Alert</th>
            <th class="p-1.5 text-center">Delete</th>
          </tr>
        </thead>
        <tbody id="positionsBody">
          <tr><td colspan="10" class="p-2 text-center text-slate-400">Loading positions...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- 2. Tickers & Delta Box -->
  <div class="bg-white p-3.5 rounded-xl shadow-sm mb-3 border border-slate-200">
    <div class="grid grid-cols-2 gap-2 mb-2">
      <div>
        <label class="text-[11px] font-bold text-slate-500 uppercase tracking-wide">Tickers</label>
        <input id="tickers" type="text" value="IREN, RKLB" class="w-full border rounded p-2 text-sm uppercase font-semibold">
      </div>
      <div>
        <label class="text-[11px] font-bold text-slate-500 uppercase tracking-wide">Delta</label>
        <input id="delta" type="number" step="0.01" value="0.2" class="w-full border rounded p-2 text-sm font-semibold">
      </div>
    </div>
    <button onclick="fetchData()" id="refreshBtn" class="bg-blue-600 active:bg-blue-700 text-white font-bold py-2 px-4 rounded-lg text-sm w-full mt-1 flex items-center justify-center gap-2">
      <span id="btnSpinner" class="hidden animate-spin h-3.5 w-3.5 border-2 border-white border-t-transparent rounded-full"></span>
      <span id="btnText">Refresh Data</span>
    </button>
    <div id="status" class="text-[11px] text-slate-500 mt-1.5 text-right font-medium">Ready</div>
  </div>

  <!-- 3. Key Technical Levels -->
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
            <th class="p-2 border-r font-bold text-emerald-800 bg-emerald-50/50">Support Floor (Min)</th>
            <th class="p-2 border-r font-medium text-slate-600">S1 Pivot</th>
            <th class="p-2 border-r font-medium text-slate-600">30d Low</th>
            <th class="p-2 border-r font-bold text-rose-800 bg-rose-50/50">Resistance Ceiling (Max)</th>
            <th class="p-2 border-r font-medium text-slate-600">R1 Pivot</th>
            <th class="p-2 font-medium text-slate-600">30d High</th>
          </tr>
        </thead>
        <tbody id="levelsBody">
          <tr><td colspan="8" class="p-3 text-center text-slate-400">Ready to load quotes...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- 4. Pill Switcher -->
  <div class="flex items-center gap-1.5 mb-3 overflow-x-auto py-1">
    <span class="text-[11px] font-bold text-slate-500 mr-1">View:</span>
    <div id="tickerPills" class="flex gap-1.5"></div>
  </div>

  <!-- 5. Cash-Secured Puts -->
  <div class="mb-4">
    <h2 class="text-xs font-bold text-sky-900 bg-sky-100 p-2.5 rounded-t-lg border-t border-x border-sky-200">
      📉 Cash-Secured Puts (Green = Strike &lt; Support/Floor)
    </h2>
    <div class="overflow-x-auto bg-white border border-slate-200 rounded-b-lg shadow-sm">
      <table class="w-full text-left" id="putsTable">
        <tbody id="putsBody">
          <tr><td class="p-4 text-center text-slate-400">Click 'Refresh Data' to load options.</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- 6. Covered Calls -->
  <div class="mb-6">
    <h2 class="text-xs font-bold text-amber-900 bg-amber-100 p-2.5 rounded-t-lg border-t border-x border-amber-200">
      📈 Covered Calls (Green = Strike &gt; Resistance/Ceiling)
    </h2>
    <div class="overflow-x-auto bg-white border border-slate-200 rounded-b-lg shadow-sm">
      <table class="w-full text-left" id="callsTable">
        <tbody id="callsBody">
          <tr><td class="p-4 text-center text-slate-400">Click 'Refresh Data' to load options.</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <script>
    let globalData = null;
    let selectedTicker = 'ALL';
    let cloudPositions = [];
    let progressTimer = null;
    let elapsedSeconds = 0;
    let hideExpired = localStorage.getItem('hideExpiredContracts') === 'true';

    const EXP_COLOR_PALETTE = [
      'bg-indigo-50/80',
      'bg-amber-50/80',
      'bg-emerald-50/80',
      'bg-purple-50/80',
      'bg-rose-50/80',
      'bg-sky-50/80',
      'bg-teal-50/80'
    ];

    function toggleHideExpired(checked) {
      hideExpired = checked;
      localStorage.setItem('hideExpiredContracts', checked);
      renderPositionsAndPL();
    }

    function getExpColor(expKey, map) {
      if (!expKey) return 'hover:bg-slate-50';
      if (!map[expKey]) {
        const idx = Object.keys(map).length % EXP_COLOR_PALETTE.length;
        map[expKey] = EXP_COLOR_PALETTE[idx];
      }
      return map[expKey];
    }

    function toggleAddForm() {
      const f = document.getElementById('positionForm');
      f.classList.toggle('hidden');
      if (!f.classList.contains('hidden') && !document.getElementById('posTradeDate').value) {
        document.getElementById('posTradeDate').value = new Date().toISOString().split('T')[0];
      }
    }

    async function loadCloudPositions() {
      try {
        const res = await fetch('/api/positions');
        cloudPositions = await res.json();
        renderPositionsAndPL();
      } catch (e) {
        document.getElementById('positionsBody').innerHTML = '<tr><td colspan="10" class="p-2 text-center text-slate-400">No active positions saved.</td></tr>';
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
      const trade_date = document.getElementById('posTradeDate').value || new Date().toISOString().split('T')[0];

      if (!ticker || isNaN(strike) || isNaN(prem) || !exp) {
        alert('Please fill all fields properly.');
        return;
      }

      const res = await fetch('/api/positions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: Date.now(), action, type, ticker, strike, prem, qty, exp, trade_date })
      });

      if (res.ok) {
        toggleAddForm();
        await loadCloudPositions();
        fetchData();
      } else {
        alert('Could not save to GitHub. Check environment variables.');
      }
    }

    async function updatePositionField(id, field, value) {
      const targetPos = cloudPositions.find(p => p.id === id);
      if (!targetPos) return;

      if (field === 'strike' || field === 'prem') {
        targetPos[field] = parseFloat(value) || 0;
      } else if (field === 'qty') {
        targetPos[field] = parseInt(value) || 1;
      } else {
        targetPos[field] = value;
      }

      const res = await fetch(`/api/positions/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(targetPos)
      });

      if (res.ok) {
        renderPositionsAndPL();
      } else {
        alert('Failed to update field on GitHub.');
      }
    }

    async function deletePosition(id) {
      if (!confirm("Delete this position from GitHub?")) return;
      const res = await fetch(`/api/positions/${id}`, { method: 'DELETE' });
      if (res.ok) loadCloudPositions();
    }

    function renderPositionsAndPL() {
      document.getElementById('hideExpiredToggle').checked = hideExpired;
      const tbody = document.getElementById('positionsBody');
      const now = new Date();
      const currentYear = now.getFullYear();
      const currentMonth = now.getMonth();
      const todayStr = now.toISOString().split('T')[0];

      let lastMonthYear = currentYear;
      let lastMonth = currentMonth - 1;
      if (lastMonth < 0) {
        lastMonth = 11;
        lastMonthYear--;
      }

      const currentMonthPositions = [];
      cloudPositions.forEach(p => {
        const parts = p.exp.split('-');
        const pYear = parseInt(parts[0], 10);
        const pMonth = parseInt(parts[1], 10) - 1;
        if (pYear > currentYear || (pYear === currentYear && pMonth >= currentMonth)) {
          currentMonthPositions.push(p);
        }
      });

      currentMonthPositions.sort((a, b) => {
        const dateDiff = new Date(a.exp) - new Date(b.exp);
        if (dateDiff !== 0) return dateDiff;
        return a.ticker.localeCompare(b.ticker);
      });

      const openStats = {};
      let totalOpenCount = 0;
      let totalCspCapital = 0;

      cloudPositions.forEach(p => {
        const isExpired = p.exp < todayStr;
        if (!isExpired) {
          totalOpenCount += p.qty;
          if (!openStats[p.ticker]) {
            openStats[p.ticker] = { total: 0, puts: 0, calls: 0, cspCapital: 0 };
          }
          openStats[p.ticker].total += p.qty;
          if (p.type === 'PUT') {
            openStats[p.ticker].puts += p.qty;
            const cap = p.strike * 100 * p.qty;
            openStats[p.ticker].cspCapital += cap;
            totalCspCapital += cap;
          } else if (p.type === 'CALL') {
            openStats[p.ticker].calls += p.qty;
          }
        }
      });

      const summaryContainer = document.getElementById('openSummaryCards');
      document.getElementById('totalOpenQty').innerText = `Total Open: ${totalOpenCount} contracts`;
      document.getElementById('totalCspCapital').innerText = `Total CSP Req: $${totalCspCapital.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

      const openTickers = Object.keys(openStats);
      if (openTickers.length === 0) {
        summaryContainer.innerHTML = '<span class="text-slate-400 text-[10px]">No active open contracts.</span>';
      } else {
        summaryContainer.innerHTML = '';
        openTickers.forEach(t => {
          const s = openStats[t];
          const card = document.createElement('div');
          card.className = "bg-white border border-slate-200 rounded px-2.5 py-1 text-[10px] flex items-center gap-2 shadow-xs";
          
          const cspCapStr = s.cspCapital > 0 
            ? `<span class="text-emerald-700 font-bold bg-emerald-50 px-1 rounded border border-emerald-200">CSP Req: $${s.cspCapital.toLocaleString('en-US', { maximumFractionDigits: 0 })}</span>`
            : '';

          card.innerHTML = `
            <span class="font-bold text-slate-800">${t}:</span>
            <span class="font-extrabold text-blue-700">${s.total}</span>
            <span class="text-[9px] text-slate-400 font-mono">(${s.puts}P / ${s.calls}C)</span>
            ${cspCapStr}
          `;
          summaryContainer.appendChild(card);
        });
      }

      const visiblePositions = hideExpired 
        ? currentMonthPositions.filter(p => p.exp >= todayStr)
        : currentMonthPositions;

      if (visiblePositions.length === 0) {
        tbody.innerHTML = `<tr><td colspan="10" class="p-2 text-center text-slate-400">${hideExpired ? 'No active open positions remaining.' : 'No active positions for this month.'}</td></tr>`;
      } else {
        tbody.innerHTML = '';
      }

      let totalRealized = 0, thisMonthRealized = 0, lastMonthRealized = 0, totalMaxUnrealized = 0, currentUnrealized = 0;
      const posColorMap = {};

      currentMonthPositions.forEach(p => {
        const parts = p.exp.split('-');
        const pYear = parseInt(parts[0], 10);
        const pMonth = parseInt(parts[1], 10) - 1;
        const isExpired = p.exp < todayStr;
        const isThisMonth = (pYear === currentYear && pMonth === currentMonth);
        const spot = (globalData && globalData.all_spots && globalData.all_spots[p.ticker] !== undefined) 
          ? globalData.all_spots[p.ticker] 
          : ((globalData && globalData.market && globalData.market[p.ticker]) ? globalData.market[p.ticker].spot : null);

        const strikeFormatted = parseFloat(p.strike).toFixed(2);
        const contractKey = `${p.ticker.toUpperCase()}_${p.exp}_${strikeFormatted}_${p.type.toUpperCase()}`;

        const liveMark = (globalData && globalData.live_positions && globalData.live_positions[contractKey] !== undefined)
          ? globalData.live_positions[contractKey]
          : null;

        const expDate = new Date(p.exp);
        const diffTime = expDate - new Date(todayStr);
        const dte = Math.ceil(diffTime / (1000 * 60 * 60 * 24));

        let maxPl = 0;
        let currentPl = 0;
        let statusHtml = '';

        if (isExpired) {
          if (p.action === 'SELL') {
            if (spot !== null && spot !== undefined) {
              const isWin = (p.type === 'PUT' && spot >= p.strike) || (p.type === 'CALL' && spot <= p.strike);
              if (isWin) {
                maxPl = p.prem * 100 * p.qty;
                statusHtml = '<span class="px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800 font-bold">Expired (Win)</span>';
              } else {
                const intrinsic = p.type === 'PUT' ? Math.max(p.strike - spot, 0) : Math.max(spot - p.strike, 0);
                maxPl = (p.prem - intrinsic) * 100 * p.qty;
                statusHtml = '<span class="px-1.5 py-0.5 rounded bg-rose-100 text-rose-800 font-bold">Assigned</span>';
              }
            } else {
              maxPl = p.prem * 100 * p.qty;
              statusHtml = '<span class="px-1.5 py-0.5 rounded bg-slate-200 text-slate-700 font-bold">Expired</span>';
            }
          } else {
            const intrinsic = p.type === 'CALL' ? Math.max((spot || 0) - p.strike, 0) : Math.max(p.strike - (spot || 0), 0);
            maxPl = (intrinsic - p.prem) * 100 * p.qty;
            statusHtml = '<span class="px-1.5 py-0.5 rounded bg-slate-200 text-slate-700 font-bold">Closed</span>';
          }

          currentPl = maxPl;
          totalRealized += maxPl;
          if (isThisMonth) thisMonthRealized += maxPl;
          else if (pYear === lastMonthYear && pMonth === lastMonth) lastMonthRealized += maxPl;
        } else {
          maxPl = (p.action === 'SELL') ? (p.prem * 100 * p.qty) : (-p.prem * 100 * p.qty);
          totalMaxUnrealized += maxPl;

          if (liveMark !== null) {
            if (p.action === 'SELL') {
              currentPl = (p.prem - liveMark) * 100 * p.qty;
            } else {
              currentPl = (liveMark - p.prem) * 100 * p.qty;
            }
            currentUnrealized += currentPl;
          }

          if (p.action === 'SELL' && spot !== null && spot !== undefined) {
            const pctFromStrike = ((spot - p.strike) / p.strike) * 100;

            if (p.type === 'PUT') {
              if (spot <= p.strike || (dte <= 5 && pctFromStrike <= 1.5)) {
                statusHtml = '<span class="px-1.5 py-0.5 rounded bg-rose-600 text-white font-extrabold animate-pulse shadow-sm whitespace-nowrap">ROLL / ASSIGN NOW</span>';
              } else if (pctFromStrike <= 3.0 || (dte <= 10 && currentPl < 0)) {
                statusHtml = '<span class="px-1.5 py-0.5 rounded bg-amber-500 text-slate-950 font-extrabold whitespace-nowrap shadow-sm">ROLL SOON</span>';
              } else {
                statusHtml = '<span class="px-1.5 py-0.5 rounded bg-blue-100 text-blue-800 font-bold whitespace-nowrap">Active</span>';
              }
            } else if (p.type === 'CALL') {
              if (spot >= p.strike) {
                statusHtml = '<span class="px-1.5 py-0.5 rounded bg-purple-700 text-white font-bold whitespace-nowrap shadow-sm">MAX PROFIT / ASSIGN</span>';
              } else if (pctFromStrike >= -2.0) {
                statusHtml = '<span class="px-1.5 py-0.5 rounded bg-amber-500 text-slate-950 font-bold whitespace-nowrap shadow-sm">TESTED</span>';
              } else {
                statusHtml = '<span class="px-1.5 py-0.5 rounded bg-blue-100 text-blue-800 font-bold whitespace-nowrap">Active</span>';
              }
            }
          } else {
            statusHtml = '<span class="px-1.5 py-0.5 rounded bg-blue-100 text-blue-800 font-bold whitespace-nowrap">Active</span>';
          }
        }

        if (!hideExpired || !isExpired) {
          const maxPlColor = maxPl >= 0 ? 'text-emerald-700' : 'text-rose-700';
          const maxPlPrefix = maxPl >= 0 ? '+$' : '-$';
          const maxPlDisplay = `${maxPlPrefix}${Math.abs(maxPl).toFixed(2)}`;

          const curPlColor = currentPl >= 0 ? 'text-emerald-700' : 'text-rose-700';
          const curPlPrefix = currentPl >= 0 ? '+$' : '-$';

          let curPlDisplay = '<span class="text-slate-400 font-normal">Syncing...</span>';
          if (isExpired || liveMark !== null) {
            let pctSpan = '';
            if (maxPl !== 0) {
              const pctOfMax = (currentPl / Math.abs(maxPl)) * 100;
              const pctColor = pctOfMax >= 0 ? 'text-emerald-600' : 'text-rose-600';
              pctSpan = `<span class="block text-[9px] font-medium ${pctColor}">(${pctOfMax >= 0 ? '+' : ''}${pctOfMax.toFixed(1)}% max)</span>`;
            }
            curPlDisplay = `<div>${curPlPrefix}${Math.abs(currentPl).toFixed(2)}${pctSpan}</div>`;
          }

          const markDisplay = (liveMark !== null)
            ? `$${liveMark.toFixed(2)}`
            : (isExpired ? '<span class="text-slate-400">$0.00</span>' : '<span class="text-slate-400 font-normal">-</span>');

          const actionBadge = p.action === 'SELL'
            ? '<span class="px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800 font-bold">SELL</span>'
            : '<span class="px-1.5 py-0.5 rounded bg-blue-100 text-blue-800 font-bold">BUY</span>';

          const rowBg = getExpColor(p.exp, posColorMap);
          const curTradeDate = p.trade_date || '';

          const tr = document.createElement('tr');
          tr.className = `border-b ${rowBg}`;
          tr.innerHTML = `
            <td class="p-1.5 border-r whitespace-nowrap">${actionBadge}</td>
            <td class="p-1.5 border-r whitespace-nowrap font-bold">${p.ticker} $${p.strike} ${p.type} (x${p.qty})</td>
            <td class="p-1 border-r whitespace-nowrap">
              <input type="date" value="${curTradeDate}"
                onchange="updatePositionField(${p.id}, 'trade_date', this.value)"
                class="border rounded px-1 py-0.5 bg-white font-mono text-[9px] text-slate-700">
            </td>
            <td class="p-1.5 border-r whitespace-nowrap text-slate-700 font-mono font-bold">${p.exp}</td>
            <td class="p-1.5 border-r whitespace-nowrap font-mono">$${p.prem.toFixed(2)}</td>
            <td class="p-1.5 border-r whitespace-nowrap font-mono font-medium text-slate-700">${markDisplay}</td>
            <td class="p-1.5 border-r whitespace-nowrap font-mono font-extrabold bg-blue-50/40 ${curPlColor}">${curPlDisplay}</td>
            <td class="p-1.5 border-r whitespace-nowrap font-mono font-bold ${maxPlColor}">${maxPlDisplay}</td>
            <td class="p-1.5 border-r whitespace-nowrap">${statusHtml}</td>
            <td class="p-1.5 text-center">
              <button onclick="deletePosition(${p.id})" class="text-rose-600 hover:text-rose-800 font-bold">✕</button>
            </td>
          `;
          tbody.appendChild(tr);
        }
      });

      const fmt = (val) => `${val >= 0 ? '+$' : '-$'}${Math.abs(val).toFixed(2)}`;
      document.getElementById('totalRealized').innerText = fmt(totalRealized);
      document.getElementById('lastMonthRealized').innerText = fmt(lastMonthRealized);
      document.getElementById('thisMonthRealized').innerText = fmt(thisMonthRealized);
      document.getElementById('thisMonthCurrentUnrealized').innerText = fmt(currentUnrealized);
      document.getElementById('totalMaxUnrealized').innerText = fmt(totalMaxUnrealized);
    }

    function renderLoadingSkeleton() {
      const loaderHtml = `
        <tr>
          <td class="p-4 text-center text-slate-500 animate-pulse bg-slate-50">
            <div class="inline-flex items-center gap-2 font-semibold">
              <span class="animate-spin h-3.5 w-3.5 border-2 border-blue-600 border-t-transparent rounded-full"></span>
              <span>Fetching options chain from Yahoo Finance...</span>
            </div>
            <div class="text-[10px] text-slate-400 mt-1">Downloading real-time bid/ask chains...</div>
          </td>
        </tr>
      `;
      document.getElementById('putsBody').innerHTML = loaderHtml;
      document.getElementById('callsBody').innerHTML = loaderHtml;
      document.getElementById('levelsBody').innerHTML = `
        <tr><td colspan="8" class="p-3 text-center text-slate-500 animate-pulse">Calculating spot &amp; pivot levels...</td></tr>
      `;
      document.getElementById('diagBanner').classList.add('hidden');
    }

    async function fetchData() {
      const btn = document.getElementById('refreshBtn');
      const spinner = document.getElementById('btnSpinner');
      const btnText = document.getElementById('btnText');
      const status = document.getElementById('status');

      btn.disabled = true;
      spinner.classList.remove('hidden');
      btnText.innerText = "Loading...";

      renderLoadingSkeleton();

      elapsedSeconds = 0;
      clearInterval(progressTimer);
      progressTimer = setInterval(() => {
        elapsedSeconds++;
        status.innerText = `⏳ Contacting Yahoo Finance (${elapsedSeconds}s)...`;
      }, 1000);

      const tickers = document.getElementById('tickers').value;
      const delta = document.getElementById('delta').value;

      const contractTickers = Array.from(new Set(cloudPositions.map(p => (p.ticker || '').trim().toUpperCase()))).filter(Boolean);

      try {
        const res = await fetch(`/api/data?tickers=${encodeURIComponent(tickers)}&contract_tickers=${encodeURIComponent(contractTickers.join(','))}&delta=${delta}`);
        clearInterval(progressTimer);

        if (!res.ok) throw new Error("API error " + res.status);
        globalData = await res.json();

        const diagBanner = document.getElementById('diagBanner');
        const diagList = document.getElementById('diagList');
        diagList.innerHTML = '';
        if (globalData.diagnostics && Object.keys(globalData.diagnostics).length > 0) {
          diagBanner.classList.remove('hidden');
          Object.entries(globalData.diagnostics).forEach(([tkr, msg]) => {
            const li = document.createElement('li');
            li.innerHTML = `<span class="font-bold">${tkr}:</span> ${msg}`;
            diagList.appendChild(li);
          });
        } else {
          diagBanner.classList.add('hidden');
        }

        renderLevelsTable(globalData.market);
        renderPills(globalData.tickers);
        renderBothTables();
        renderPositionsAndPL();

        status.innerHTML = `<span class="text-emerald-600 font-bold">✓ Updated</span> at ${new Date().toLocaleTimeString()} (${elapsedSeconds}s)`;
      } catch (err) {
        clearInterval(progressTimer);
        status.innerHTML = `<span class="text-rose-600 font-bold">✕ Yahoo Finance did not respond.</span> Please retry.`;
        document.getElementById('putsBody').innerHTML = `<tr><td class="p-3 text-center text-rose-500 font-semibold">Failed to load Put chain: Network or rate limit issue.</td></tr>`;
        document.getElementById('callsBody').innerHTML = `<tr><td class="p-3 text-center text-rose-500 font-semibold">Failed to load Call chain: Network or rate limit issue.</td></tr>`;
      } finally {
        btn.disabled = false;
        spinner.classList.add('hidden');
        btnText.innerText = "Refresh Data";
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
        tbody.innerHTML = `<tr><td colspan="8" class="p-3 text-center text-rose-500 font-semibold">No ticker quotes returned. Check the Diagnostic Notice above.</td></tr>`;
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
      renderTable('putsBody', globalData.puts, displayTickers, globalData.targets, 'Put');
      renderTable('callsBody', globalData.calls, displayTickers, globalData.targets, 'Call');
    }

    function renderTable(elementId, results, tickers, targets, tableType) {
      const tbody = document.getElementById(elementId);
      tbody.innerHTML = '';

      if (!tickers || tickers.length === 0) {
        tbody.innerHTML = `<tr><td class="p-4 text-center text-slate-400">No active tickers found in the Tickers box.</td></tr>`;
        return;
      }

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
      let totalContractsPopulated = 0;

      targets.forEach(tgt => {
        const isSweetSpot = (tgt === 21 || tgt === 30);
        const badge = isSweetSpot ? '★ ' : '';
        const targetDict = (results && (results[tgt] || results[String(tgt)])) || {};

        let rowExpKey = '';
        for (const t of tickers) {
          if (targetDict[t] && targetDict[t].raw_exp) {
            rowExpKey = targetDict[t].raw_exp;
            break;
          }
        }

        const rowBg = getExpColor(rowExpKey, tableColorMap);
        let rowHtml = `<tr class="border-b ${rowBg}"><td class="p-2 border-r-2 border-r-slate-400 whitespace-nowrap font-bold text-slate-700 bg-slate-50">${badge}${tgt}d</td>`;

        tickers.forEach(t => {
          const item = targetDict[t];
          if (item) {
            totalContractsPopulated++;
            const strikeBg = item.is_safe ? 'bg-green-200 text-green-900 font-bold' : '';
            rowHtml += `
              <td class="p-1 border-r text-center leading-tight text-[10px] text-slate-700">${item.exp}</td>
              <td class="p-1.5 border-r whitespace-nowrap ${strikeBg}">$${item.strike} <span class="text-[9px]">(${item.pct_diff})</span></td>
              <td class="p-1.5 border-r whitespace-nowrap text-slate-500 font-mono">${item.iv}%</td>
              <td class="p-1.5 border-r whitespace-nowrap font-bold">$${item.prem}</td>
              <td class="p-1.5 border-r-2 border-r-slate-400 whitespace-nowrap text-emerald-700 font-bold">${item.ann}%</td>
            `;
          } else {
            const reason = (globalData.diagnostics && globalData.diagnostics[`${t}_${tgt}d_${tableType}`]) 
              ? globalData.diagnostics[`${t}_${tgt}d_${tableType}`] 
              : 'No strike match';
            rowHtml += `<td class="p-1.5 border-r-2 border-r-slate-400 text-center text-slate-400 text-[9px]" colspan="5">${reason}</td>`;
          }
        });
        rowHtml += `</tr>`;
        tbody.innerHTML += rowHtml;
      });

      if (totalContractsPopulated === 0) {
        tbody.innerHTML += `
          <tr>
            <td colspan="${1 + tickers.length * 5}" class="p-2.5 bg-amber-50 text-amber-800 text-center text-[10px]">
              No ${tableType} contracts matched the target delta or business-day windows. Refer to the diagnostic banner above for details.
            </td>
          </tr>
        `;
      }
    }

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

@app.put("/api/positions/{pos_id}")
def update_position(pos_id: int, updated: PositionModel):
    positions, _ = get_positions_from_github()
    found = False
    for i, p in enumerate(positions):
        if p.get("id") == pos_id:
            positions[i] = updated.model_dump()
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail="Position not found")
    success = save_positions_to_github(positions)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update GitHub")
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
def get_options_data(tickers: str = "IREN,RKLB", contract_tickers: str = "", delta: float = 0.2):
    positions, _ = get_positions_from_github()
    
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

    market_data = {}
    all_spots = {}
    results_puts = {str(t): {} for t in target_periods}
    results_calls = {str(t): {} for t in target_periods}
    live_positions = {}
    diagnostics = {}

    for ticker in combined_ticker_list:
        is_primary = ticker in primary_tickers
        try:
            tkr = yf.Ticker(ticker)
            spot_price = 0.0

            try:
                if hasattr(tkr, 'fast_info'):
                    spot_price = float(tkr.fast_info.get('last_price') or tkr.fast_info.get('lastPrice') or 0.0)
            except Exception:
                pass

            if not spot_price or spot_price <= 0:
                hist_1d = tkr.history(period="5d")
                if not hist_1d.empty:
                    spot_price = float(hist_1d['Close'].iloc[-1])

            if not spot_price or spot_price <= 0:
                if is_primary:
                    diagnostics[ticker] = "Could not fetch spot price from Yahoo Finance"
                continue

            all_spots[ticker] = round(spot_price, 2)
            expirations = list(tkr.options) if tkr.options else []
            if not expirations and is_primary:
                diagnostics[ticker] = f"No option expiration dates returned for spot ${spot_price:.2f}"
                continue

            df_hist = tkr.history(period="30d")
        except Exception as e:
            if is_primary:
                diagnostics[ticker] = f"Error reading Yahoo Finance: {str(e)}"
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
                    
                    contract_key = f"{ticker}_{p['exp']}_{k_target:.2f}_{p['type'].upper()}"
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
                            "is_safe": k_val < market_data[ticker]["support"]
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
                            "is_safe": k_val > market_data[ticker]["resistance"]
                        }

    primary_market_data = {t: market_data[t] for t in primary_tickers if t in market_data}

    return {
        "market": primary_market_data,
        "all_spots": all_spots,
        "puts": results_puts,
        "calls": results_calls,
        "tickers": primary_tickers,
        "targets": target_periods,
        "live_positions": live_positions,
        "diagnostics": diagnostics
    }

@app.get("/", response_class=HTMLResponse)
def render_index():
    return HTMLResponse(content=HTML_CONTENT)
