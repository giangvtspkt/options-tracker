from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import yfinance as yf
import numpy as np
import math
import datetime
import os
import json
import base64
import requests
import tempfile

app = FastAPI()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")
GITHUB_FILE_PATH = os.getenv("GITHUB_FILE_PATH", "positions.json")
CACHE_FILE_PATH = os.path.join(tempfile.gettempdir(), "options_cache_data.json")

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

def calc_iv_percentile(current_iv, hist_vols):
    if not hist_vols or len(hist_vols) == 0 or current_iv <= 0:
        return None
    count_below = sum(1 for v in hist_vols if v < current_iv)
    return round((count_below / len(hist_vols)) * 100.0, 1)

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

def load_cached_data():
    if os.path.exists(CACHE_FILE_PATH):
        try:
            with open(CACHE_FILE_PATH, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_cached_data(cache):
    try:
        with open(CACHE_FILE_PATH, "w") as f:
            json.dump(cache, f, indent=2)
    except Exception:
        pass

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
    broker: str = "moomoo"

    def to_dict(self):
        return self.model_dump() if hasattr(self, "model_dump") else self.dict()

HTML_CONTENT = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>Options Tracker (Cloud Sync &amp; Fallback Cache)</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-100 text-slate-800 p-2.5 sm:p-4 font-sans text-xs">

  <!-- Diagnostic Alert Banner -->
  <div id="diagBanner" class="hidden bg-amber-50 border border-amber-300 rounded-xl p-3 mb-3 text-amber-900">
    <div class="font-bold flex items-center justify-between text-xs mb-1">
      <span class="flex items-center gap-1.5"><span>⚠️</span> <span id="diagTitle">Notice</span></span>
      <span id="cacheTimestampBadge" class="text-[9px] font-mono bg-amber-200/70 px-1.5 py-0.5 rounded text-amber-900 hidden"></span>
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
        <button onclick="toggleAlertSettings()" class="bg-slate-100 hover:bg-slate-200 text-slate-700 text-[10px] font-semibold px-2 py-0.5 rounded border border-slate-200 flex items-center gap-1">
          <span>⚙</span> Alert Criteria
        </button>
      </div>
      <button onclick="toggleAddForm()" id="toggleFormBtn" class="bg-slate-800 text-white text-[10px] font-bold px-2.5 py-1 rounded-md">
        + Add Position
      </button>
    </div>

    <!-- Collapsible Alert Criteria Configuration Panel -->
    <div id="alertSettingsPanel" class="hidden bg-slate-50 border border-slate-200 rounded-lg p-2.5 mb-3">
      <div class="flex items-center justify-between mb-2">
        <span class="font-bold text-[11px] text-slate-700">⚙ Custom Rolling &amp; IV Percentile Alert Thresholds</span>
        <button onclick="resetAlertCriteria()" class="text-[10px] bg-rose-50 hover:bg-rose-100 text-rose-700 font-bold px-2 py-0.5 rounded border border-rose-200">
          ↺ Reset to Defaults
        </button>
      </div>
      <div class="grid grid-cols-2 sm:grid-cols-3 gap-2.5 text-[10px]">
        <div>
          <label class="font-semibold text-slate-600 block mb-0.5">CSP Warn Buffer (%)</label>
          <input id="critPutWarnPct" type="number" step="0.5" class="w-full border rounded p-1 text-xs bg-white" onchange="saveAlertCriteria()">
          <span class="text-[9px] text-slate-400">Spot &le; Strike + X%</span>
        </div>
        <div>
          <label class="font-semibold text-slate-600 block mb-0.5">CSP Warn DTE (&le; days)</label>
          <input id="critPutWarnDte" type="number" step="1" class="w-full border rounded p-1 text-xs bg-white" onchange="saveAlertCriteria()">
          <span class="text-[9px] text-slate-400">If P&L is negative</span>
        </div>
        <div>
          <label class="font-semibold text-slate-600 block mb-0.5">CSP Critical DTE (&le; days)</label>
          <input id="critPutCritDte" type="number" step="1" class="w-full border rounded p-1 text-xs bg-white" onchange="saveAlertCriteria()">
          <span class="text-[9px] text-slate-400">Near strike deadline</span>
        </div>
        <div>
          <label class="font-semibold text-slate-600 block mb-0.5">CC Tested Buffer (%)</label>
          <input id="critCallWarnPct" type="number" step="0.5" class="w-full border rounded p-1 text-xs bg-white" onchange="saveAlertCriteria()">
          <span class="text-[9px] text-slate-400">Spot &ge; Strike - X%</span>
        </div>
        <div class="bg-amber-50/70 p-1.5 rounded border border-amber-200">
          <label class="font-bold text-amber-900 block mb-0.5">🔥 Put High IV (%ile)</label>
          <input id="critPutHighIvPctile" type="number" step="5" class="w-full border rounded p-1 text-xs bg-white font-bold" onchange="saveAlertCriteria()">
          <span class="text-[9px] text-amber-700">Triggers if Put IVP &ge; threshold (Default: 85%)</span>
        </div>
        <div class="bg-amber-50/70 p-1.5 rounded border border-amber-200">
          <label class="font-bold text-amber-900 block mb-0.5">🔥 Call High IV (%ile)</label>
          <input id="critCallHighIvPctile" type="number" step="5" class="w-full border rounded p-1 text-xs bg-white font-bold" onchange="saveAlertCriteria()">
          <span class="text-[9px] text-amber-700">Triggers if Call IVP &ge; threshold (Default: 80%)</span>
        </div>
      </div>
    </div>

    <!-- P/L Metrics Cards (6-Column Grid) -->
    <div class="grid grid-cols-2 sm:grid-cols-6 gap-2 mb-3">
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
        <div class="text-[10px] font-bold text-blue-900">This Mo Unrealized</div>
        <div id="thisMonthCurrentUnrealized" class="text-xs font-extrabold font-mono text-slate-700">$0.00</div>
      </div>
      <div class="bg-slate-50 border border-slate-200 rounded-lg p-2 text-center">
        <div class="text-[10px] font-bold text-slate-500">Total Unrealized</div>
        <div id="totalCurrentUnrealized" class="text-xs font-extrabold font-mono text-slate-700">$0.00</div>
      </div>
      <div class="bg-slate-50 border border-slate-200 rounded-lg p-2 text-center">
        <div class="text-[10px] font-bold text-slate-500">Max Unrealized</div>
        <div id="totalMaxUnrealized" class="text-xs font-extrabold font-mono text-slate-700">$0.00</div>
      </div>
    </div>

    <!-- Open Contracts & CSP Capital Summary -->
    <div class="bg-slate-50 border border-slate-200 rounded-lg p-2.5 mb-3">
      <div class="flex flex-wrap items-center justify-between gap-2 mb-2">
        <div class="flex items-center gap-2">
          <span class="text-[11px] font-bold text-slate-700">📊 Open Contracts &amp; CSP Capital Requirement</span>
          <span id="totalOpenQty" class="text-[10px] font-semibold text-slate-500 bg-slate-200/70 px-1.5 py-0.5 rounded">Total Open: 0</span>
        </div>
        <div class="flex flex-wrap items-center gap-1.5 text-[10px] font-mono">
          <span id="moomooCspCapital" class="text-orange-800 bg-orange-100 border border-orange-200 px-2 py-0.5 rounded font-bold">Moomoo: $0</span>
          <span id="ibkrCspCapital" class="text-blue-800 bg-blue-100 border border-blue-200 px-2 py-0.5 rounded font-bold">IBKR: $0</span>
          <span id="totalCspCapital" class="text-emerald-800 bg-emerald-100 border border-emerald-300 px-2 py-0.5 rounded font-extrabold">Total CSP: $0.00</span>
        </div>
      </div>
      <div id="openSummaryCards" class="flex flex-wrap gap-2">
        <span class="text-slate-400 text-[10px]">No active open contracts.</span>
      </div>
    </div>

    <!-- Add Position Form -->
    <div id="positionForm" class="hidden bg-slate-50 p-2.5 rounded-lg border border-slate-200 mb-3 space-y-2">
      <div class="grid grid-cols-4 gap-2">
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
        <div>
          <label class="text-[10px] font-bold text-slate-500">Broker</label>
          <select id="posBroker" class="w-full border rounded p-1.5 text-xs bg-white font-semibold">
            <option value="moomoo">MOOMOO</option>
            <option value="ibkr">IBKR</option>
          </select>
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
            <th class="p-1.5 border-r">Broker ✎</th>
            <th class="p-1.5 border-r">Pos</th>
            <th class="p-1.5 border-r">Contract</th>
            <th class="p-1.5 border-r">Trade Day ✎</th>
            <th class="p-1.5 border-r">Exp ✎</th>
            <th class="p-1.5 border-r">Entry</th>
            <th class="p-1.5 border-r">Live Mark</th>
            <th class="p-1.5 border-r font-extrabold text-blue-900 bg-blue-50/70">Current P/L ($)</th>
            <th class="p-1.5 border-r">Max P/L ($)</th>
            <th class="p-1.5 border-r">Status / Alert</th>
            <th class="p-1.5 text-center">Delete</th>
          </tr>
        </thead>
        <tbody id="positionsBody">
          <tr><td colspan="11" class="p-2 text-center text-slate-400">Loading positions...</td></tr>
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
    <button onclick="fetchData(false)" id="refreshBtn" class="bg-blue-600 active:bg-blue-700 text-white font-bold py-2 px-4 rounded-lg text-sm w-full mt-1 flex items-center justify-center gap-2">
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
    <h2 class="text-xs font-bold text-sky-900 bg-sky-100 p-2.5 rounded-t-lg border-t border-x border-sky-200 flex items-center justify-between">
      <span>📉 Cash-Secured Puts (Green = Strike &lt; Support/Floor)</span>
      <span id="putHighIvNotice" class="text-[10px] text-rose-800 font-bold hidden bg-rose-200/80 px-2 py-0.5 rounded animate-pulse">🔥 High IVP Put Alert Active</span>
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
    <h2 class="text-xs font-bold text-amber-900 bg-amber-100 p-2.5 rounded-t-lg border-t border-x border-amber-200 flex items-center justify-between">
      <span>📈 Covered Calls (Green = Strike &gt; Resistance/Ceiling)</span>
      <span id="callHighIvNotice" class="text-[10px] text-rose-800 font-bold hidden bg-rose-200/80 px-2 py-0.5 rounded animate-pulse">🔥 High IVP Call Alert Active</span>
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
    let hasLoadedOnce = false;

    const DEFAULT_ALERT_CRITERIA = {
      putWarnPct: 3.0,
      putWarnDte: 10,
      putCritDte: 5,
      callWarnPct: 2.0,
      putHighIvPctile: 85.0,
      callHighIvPctile: 80.0
    };

    let alertCriteria = { ...DEFAULT_ALERT_CRITERIA };

    function loadAlertCriteria() {
      const saved = localStorage.getItem('alertCriteria');
      if (saved) {
        try {
          alertCriteria = { ...DEFAULT_ALERT_CRITERIA, ...JSON.parse(saved) };
        } catch(e) {
          alertCriteria = { ...DEFAULT_ALERT_CRITERIA };
        }
      }
      document.getElementById('critPutWarnPct').value = alertCriteria.putWarnPct;
      document.getElementById('critPutWarnDte').value = alertCriteria.putWarnDte;
      document.getElementById('critPutCritDte').value = alertCriteria.putCritDte;
      document.getElementById('critCallWarnPct').value = alertCriteria.callWarnPct;
      document.getElementById('critPutHighIvPctile').value = alertCriteria.putHighIvPctile;
      document.getElementById('critCallHighIvPctile').value = alertCriteria.callHighIvPctile;
    }

    function saveAlertCriteria() {
      alertCriteria.putWarnPct = parseFloat(document.getElementById('critPutWarnPct').value) || DEFAULT_ALERT_CRITERIA.putWarnPct;
      alertCriteria.putWarnDte = parseInt(document.getElementById('critPutWarnDte').value) || DEFAULT_ALERT_CRITERIA.putWarnDte;
      alertCriteria.putCritDte = parseInt(document.getElementById('critPutCritDte').value) || DEFAULT_ALERT_CRITERIA.putCritDte;
      alertCriteria.callWarnPct = parseFloat(document.getElementById('critCallWarnPct').value) || DEFAULT_ALERT_CRITERIA.callWarnPct;
      alertCriteria.putHighIvPctile = parseFloat(document.getElementById('critPutHighIvPctile').value) || DEFAULT_ALERT_CRITERIA.putHighIvPctile;
      alertCriteria.callHighIvPctile = parseFloat(document.getElementById('critCallHighIvPctile').value) || DEFAULT_ALERT_CRITERIA.callHighIvPctile;
      localStorage.setItem('alertCriteria', JSON.stringify(alertCriteria));
      renderPositionsAndPL();
      renderBothTables();
    }

    function resetAlertCriteria() {
      alertCriteria = { ...DEFAULT_ALERT_CRITERIA };
      localStorage.removeItem('alertCriteria');
      loadAlertCriteria();
      renderPositionsAndPL();
      renderBothTables();
    }

    function toggleAlertSettings() {
      document.getElementById('alertSettingsPanel').classList.toggle('hidden');
    }

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
        document.getElementById('positionsBody').innerHTML = '<tr><td colspan="11" class="p-2 text-center text-slate-400">No active positions saved.</td></tr>';
      }
    }

    async function savePosition() {
      const action = document.getElementById('posAction').value;
      const type = document.getElementById('posType').value;
      const ticker = document.getElementById('posTicker').value.trim().toUpperCase();
      const broker = document.getElementById('posBroker').value;
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
        body: JSON.stringify({ id: Date.now(), action, type, ticker, strike, prem, qty, exp, trade_date, broker })
      });

      if (res.ok) {
        toggleAddForm();
        await loadCloudPositions();
        fetchData(false);
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
      if (!targetPos.broker) targetPos.broker = 'moomoo';

      const res = await fetch(`/api/positions/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(targetPos)
      });

      if (res.ok) {
        // Do NOT aggressively call renderPositionsAndPL() here to prevent focus loss
        if (field === 'exp' || field === 'ticker' || field === 'strike') {
          fetchData(true);
        }
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

      let totalRealized = 0, thisMonthRealized = 0, lastMonthRealized = 0;
      let totalMaxUnrealized = 0, totalCurrentUnrealized = 0, thisMonthCurrentUnrealized = 0;

      const openStats = {};
      let totalOpenCount = 0;
      let totalCspCapital = 0;
      let moomooCspCapital = 0;
      let ibkrCspCapital = 0;

      cloudPositions.forEach(p => {
        const parts = (p.exp || '').split('-');
        const pYear = parseInt(parts[0], 10);
        const pMonth = parseInt(parts[1], 10) - 1;
        const isExpired = p.exp < todayStr;
        const isThisMonth = (pYear === currentYear && pMonth === currentMonth);
        const isLastMonth = (pYear === lastMonthYear && pMonth === lastMonth);

        const spot = (globalData && globalData.all_spots && globalData.all_spots[p.ticker] !== undefined) 
          ? globalData.all_spots[p.ticker] 
          : ((globalData && globalData.market && globalData.market[p.ticker]) ? globalData.market[p.ticker].spot : null);

        const strikeFormatted = parseFloat(p.strike).toFixed(2);
        const contractKey = `${p.ticker.trim().toUpperCase()}_${p.exp.trim()}_${strikeFormatted}_${p.type.trim().toUpperCase()}`;
        const liveMark = (globalData && globalData.live_positions && globalData.live_positions[contractKey] !== undefined)
          ? globalData.live_positions[contractKey]
          : null;

        if (isExpired) {
          let closedPl = 0;
          if (p.action === 'SELL') {
            if (spot !== null && spot !== undefined) {
              const isWin = (p.type === 'PUT' && spot >= p.strike) || (p.type === 'CALL' && spot <= p.strike);
              if (isWin) {
                closedPl = p.prem * 100 * p.qty;
              } else {
                const intrinsic = p.type === 'PUT' ? Math.max(p.strike - spot, 0) : Math.max(spot - p.strike, 0);
                closedPl = (p.prem - intrinsic) * 100 * p.qty;
              }
            } else {
              closedPl = p.prem * 100 * p.qty;
            }
          } else {
            const intrinsic = p.type === 'CALL' ? Math.max((spot || 0) - p.strike, 0) : Math.max(p.strike - (spot || 0), 0);
            closedPl = (intrinsic - p.prem) * 100 * p.qty;
          }

          totalRealized += closedPl;
          if (isThisMonth) thisMonthRealized += closedPl;
          if (isLastMonth) lastMonthRealized += closedPl;
        } else {
          totalOpenCount += p.qty;
          const broker = (p.broker || 'moomoo').toLowerCase();

          if (!openStats[p.ticker]) {
            openStats[p.ticker] = { total: 0, puts: 0, calls: 0, cspCapital: 0, moomooCsp: 0, ibkrCsp: 0 };
          }
          openStats[p.ticker].total += p.qty;

          if (p.type === 'PUT') {
            openStats[p.ticker].puts += p.qty;
            const cap = p.strike * 100 * p.qty;
            openStats[p.ticker].cspCapital += cap;
            totalCspCapital += cap;

            if (broker === 'moomoo') {
              moomooCspCapital += cap;
              openStats[p.ticker].moomooCsp += cap;
            } else if (broker === 'ibkr') {
              ibkrCspCapital += cap;
              openStats[p.ticker].ibkrCsp += cap;
            }
          } else if (p.type === 'CALL') {
            openStats[p.ticker].calls += p.qty;
          }

          const maxPl = (p.action === 'SELL') ? (p.prem * 100 * p.qty) : (-p.prem * 100 * p.qty);
          totalMaxUnrealized += maxPl;

          if (isThisMonth) {
            thisMonthCurrentUnrealized += maxPl;
          }

          if (liveMark !== null) {
            const curPl = (p.action === 'SELL') 
              ? (p.prem - liveMark) * 100 * p.qty 
              : (liveMark - p.prem) * 100 * p.qty;

            totalCurrentUnrealized += curPl;
          }
        }
      });

      const summaryContainer = document.getElementById('openSummaryCards');
      document.getElementById('totalOpenQty').innerText = `Total Open: ${totalOpenCount} contracts`;
      
      const fmtCurrency = (val) => `$${val.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
      document.getElementById('moomooCspCapital').innerText = `Moomoo CSP: ${fmtCurrency(moomooCspCapital)}`;
      document.getElementById('ibkrCspCapital').innerText = `IBKR CSP: ${fmtCurrency(ibkrCspCapital)}`;
      document.getElementById('totalCspCapital').innerText = `Total CSP: ${fmtCurrency(totalCspCapital)}`;

      const openTickers = Object.keys(openStats);
      if (openTickers.length === 0) {
        summaryContainer.innerHTML = '<span class="text-slate-400 text-[10px]">No active open contracts.</span>';
      } else {
        summaryContainer.innerHTML = '';
        openTickers.forEach(t => {
          const s = openStats[t];
          const card = document.createElement('div');
          card.className = "bg-white border border-slate-200 rounded px-2.5 py-1 text-[10px] flex items-center gap-2 shadow-xs";
          
          let cspBadges = '';
          if (s.cspCapital > 0) {
            const parts = [];
            if (s.moomooCsp > 0) parts.push(`<span class="text-orange-700 font-bold">Moo: ${fmtCurrency(s.moomooCsp)}</span>`);
            if (s.ibkrCsp > 0) parts.push(`<span class="text-blue-700 font-bold">IB: ${fmtCurrency(s.ibkrCsp)}</span>`);
            cspBadges = `<div class="bg-emerald-50 border border-emerald-200 px-1.5 py-0.5 rounded flex items-center gap-1 font-mono">${parts.join(' | ')}</div>`;
          }

          card.innerHTML = `
            <span class="font-bold text-slate-800">${t}:</span>
            <span class="font-extrabold text-blue-700">${s.total}</span>
            <span class="text-[9px] text-slate-400 font-mono">(${s.puts}P / ${s.calls}C)</span>
            ${cspBadges}
          `;
          summaryContainer.appendChild(card);
        });
      }

      const visiblePositions = cloudPositions
        .filter(p => !hideExpired || p.exp >= todayStr)
        .sort((a, b) => {
          const dateDiff = new Date(a.exp) - new Date(b.exp);
          if (dateDiff !== 0) return dateDiff;
          return a.ticker.localeCompare(b.ticker);
        });

      if (visiblePositions.length === 0) {
        tbody.innerHTML = `<tr><td colspan="11" class="p-2 text-center text-slate-400">${hideExpired ? 'No active open positions remaining.' : 'No active positions saved.'}</td></tr>`;
      } else {
        tbody.innerHTML = '';
      }

      const posColorMap = {};

      visiblePositions.forEach(p => {
        const isExpired = p.exp < todayStr;
        const spot = (globalData && globalData.all_spots && globalData.all_spots[p.ticker] !== undefined) 
          ? globalData.all_spots[p.ticker] 
          : ((globalData && globalData.market && globalData.market[p.ticker]) ? globalData.market[p.ticker].spot : null);

        const strikeFormatted = parseFloat(p.strike).toFixed(2);
        const contractKey = `${p.ticker.trim().toUpperCase()}_${p.exp.trim()}_${strikeFormatted}_${p.type.trim().toUpperCase()}`;

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
        } else {
          maxPl = (p.action === 'SELL') ? (p.prem * 100 * p.qty) : (-p.prem * 100 * p.qty);

          if (liveMark !== null) {
            if (p.action === 'SELL') {
              currentPl = (p.prem - liveMark) * 100 * p.qty;
            } else {
              currentPl = (liveMark - p.prem) * 100 * p.qty;
            }
          }

          if (p.action === 'SELL' && spot !== null && spot !== undefined) {
            const pctFromStrike = ((spot - p.strike) / p.strike) * 100;

            if (p.type === 'PUT') {
              if (spot <= p.strike || (dte <= alertCriteria.putCritDte && pctFromStrike <= 1.5)) {
                statusHtml = '<span class="px-1.5 py-0.5 rounded bg-rose-600 text-white font-extrabold animate-pulse shadow-sm whitespace-nowrap">ROLL / ASSIGN NOW</span>';
              } else if (pctFromStrike <= alertCriteria.putWarnPct || (dte <= alertCriteria.putWarnDte && currentPl < 0)) {
                statusHtml = '<span class="px-1.5 py-0.5 rounded bg-amber-500 text-slate-950 font-extrabold whitespace-nowrap shadow-sm">ROLL SOON</span>';
              } else {
                statusHtml = '<span class="px-1.5 py-0.5 rounded bg-blue-100 text-blue-800 font-bold whitespace-nowrap">Active</span>';
              }
            } else if (p.type === 'CALL') {
              if (spot >= p.strike) {
                statusHtml = '<span class="px-1.5 py-0.5 rounded bg-purple-700 text-white font-bold whitespace-nowrap shadow-sm">MAX PROFIT / ASSIGN</span>';
              } else if (pctFromStrike >= -alertCriteria.callWarnPct) {
                statusHtml = '<span class="px-1.5 py-0.5 rounded bg-amber-500 text-slate-950 font-bold whitespace-nowrap shadow-sm">TESTED</span>';
              } else {
                statusHtml = '<span class="px-1.5 py-0.5 rounded bg-blue-100 text-blue-800 font-bold whitespace-nowrap">Active</span>';
              }
            }
          } else {
            statusHtml = '<span class="px-1.5 py-0.5 rounded bg-blue-100 text-blue-800 font-bold whitespace-nowrap">Active</span>';
          }
        }

        const maxPlColor = maxPl >= 0 ? 'text-emerald-700' : 'text-rose-700';
        const maxPlPrefix = maxPl >= 0 ? '+$' : '-$';
        const maxPlDisplay = `${maxPlPrefix}${Math.abs(maxPl).toFixed(2)}`;

        const curPlColor = currentPl >= 0 ? 'text-emerald-700' : 'text-rose-700';
        const curPlPrefix = currentPl >= 0 ? '+$' : '-$';

        let curPlDisplay = '<span class="text-slate-400 font-normal">Pending Quote</span>';
        if (isExpired || liveMark !== null) {
          let pctSpan = '';
          if (maxPl !== 0) {
            const pctOfMax = (currentPl / Math.abs(maxPl)) * 100;
            const pctColor = pctOfMax >= 0 ? 'text-emerald-600' : 'text-rose-600';
            pctSpan = `<span class="block text-[9px] font-medium ${pctColor}">(${pctOfMax >= 0 ? '+' : ''}${pctOfMax.toFixed(1)}% max)</span>`;
          }
          curPlDisplay = `<div>${curPlPrefix}${Math.abs(currentPl).toFixed(2)}${pctSpan}</div>`;
        }

        let markDisplay = '<span class="text-slate-400 font-normal">-</span>';
        if (isExpired) {
          markDisplay = '<span class="text-slate-400 font-mono">$0.00</span>';
        } else if (liveMark !== null) {
          let spotSubtext = '';
          if (spot !== null && spot !== undefined && p.strike > 0) {
            const diffPct = ((spot - p.strike) / p.strike) * 100;
            const isPut = (p.type === 'PUT');
            const isSafe = isPut ? (spot >= p.strike) : (spot <= p.strike);
            const spotColor = isSafe ? 'text-emerald-600' : 'text-rose-600 font-bold';
            const sign = diffPct > 0 ? '+' : '';
            spotSubtext = `<span class="block text-[8.5px] font-mono leading-tight ${spotColor}">$${spot.toFixed(2)} (${sign}${diffPct.toFixed(1)}%)</span>`;
          }
          markDisplay = `<div><span class="font-mono font-medium text-slate-800">$${liveMark.toFixed(2)}</span>${spotSubtext}</div>`;
        } else {
          markDisplay = '<span class="text-amber-500 font-normal">No Quote</span>';
        }

        const actionBadge = p.action === 'SELL'
          ? '<span class="px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800 font-bold">SELL</span>'
          : '<span class="px-1.5 py-0.5 rounded bg-blue-100 text-blue-800 font-bold">BUY</span>';

        const rowBg = getExpColor(p.exp, posColorMap);
        const curTradeDate = p.trade_date || '';
        const curExpDate = p.exp || '';
        const curBroker = (p.broker || 'moomoo').toLowerCase();

        const tr = document.createElement('tr');
        tr.className = `border-b ${rowBg}`;
        tr.innerHTML = `
          <td class="p-1 border-r whitespace-nowrap">
            <select onchange="updatePositionField(${p.id}, 'broker', this.value)"
              class="border rounded px-1 py-0.5 bg-white font-bold text-[9px] ${curBroker === 'moomoo' ? 'text-orange-600 border-orange-200' : 'text-blue-700 border-blue-200'}">
              <option value="moomoo" ${curBroker === 'moomoo' ? 'selected' : ''}>MOOMOO</option>
              <option value="ibkr" ${curBroker === 'ibkr' ? 'selected' : ''}>IBKR</option>
            </select>
          </td>
          <td class="p-1.5 border-r whitespace-nowrap">${actionBadge}</td>
          <td class="p-1.5 border-r whitespace-nowrap font-bold">${p.ticker} $${p.strike} ${p.type} (x${p.qty})</td>
          <td class="p-1 border-r whitespace-nowrap">
            <input type="date" value="${curTradeDate}"
              onchange="updatePositionField(${p.id}, 'trade_date', this.value)"
              class="border rounded px-1 py-0.5 bg-white font-mono text-[9px] text-slate-700">
          </td>
          <td class="p-1 border-r whitespace-nowrap">
            <input type="date" value="${curExpDate}"
              onchange="updatePositionField(${p.id}, 'exp', this.value)"
              class="border rounded px-1 py-0.5 bg-white font-mono text-[9px] font-bold text-slate-700">
          </td>
          <td class="p-1.5 border-r whitespace-nowrap font-mono">$${p.prem.toFixed(2)}</td>
          <td class="p-1.5 border-r whitespace-nowrap">${markDisplay}</td>
          <td class="p-1.5 border-r whitespace-nowrap font-mono font-extrabold bg-blue-50/40 ${curPlColor}">${curPlDisplay}</td>
          <td class="p-1.5 border-r whitespace-nowrap font-mono font-bold ${maxPlColor}">${maxPlDisplay}</td>
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
      document.getElementById('thisMonthCurrentUnrealized').innerText = fmt(thisMonthCurrentUnrealized);
      document.getElementById('totalCurrentUnrealized').innerText = fmt(totalCurrentUnrealized);
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
    }

    function applyDataPayload(data) {
      globalData = data;
      renderLevelsTable(globalData.market);
      renderPills(globalData.tickers);
      renderBothTables();
      renderPositionsAndPL();

      const diagBanner = document.getElementById('diagBanner');
      const diagList = document.getElementById('diagList');
      const diagTitle = document.getElementById('diagTitle');
      const cacheBadge = document.getElementById('cacheTimestampBadge');
      diagList.innerHTML = '';

      if (globalData.is_cached) {
        diagBanner.classList.remove('hidden');
        diagTitle.innerText = "Yahoo Finance Unreachable: Displaying Cached Data";
        cacheBadge.innerText = `Cached: ${globalData.cached_at || 'Recent'}`;
        cacheBadge.classList.remove('hidden');
        const li = document.createElement('li');
        li.innerText = "Real-time updates are temporarily unavailable. Market quotes and live marks reflect the last successful fetch.";
        diagList.appendChild(li);
      } else if (globalData.diagnostics && Object.keys(globalData.diagnostics).length > 0) {
        diagBanner.classList.remove('hidden');
        diagTitle.innerText = "Notice: Option Chain Incomplete";
        cacheBadge.classList.add('hidden');
        Object.entries(globalData.diagnostics).forEach(([tkr, msg]) => {
          const li = document.createElement('li');
          li.innerHTML = `<span class="font-bold">${tkr}:</span> ${msg}`;
          diagList.appendChild(li);
        });
      } else {
        diagBanner.classList.add('hidden');
      }
    }

    async function fetchData(isSilent = false) {
      const btn = document.getElementById('refreshBtn');
      const spinner = document.getElementById('btnSpinner');
      const btnText = document.getElementById('btnText');
      const status = document.getElementById('status');

      const showSkeleton = !hasLoadedOnce || !isSilent;
      if (showSkeleton) {
        btn.disabled = true;
        spinner.classList.remove('hidden');
        btnText.innerText = "Loading...";
        renderLoadingSkeleton();
      } else {
        status.innerHTML = `<span class="text-blue-600 font-medium animate-pulse">Syncing in background...</span>`;
      }

      elapsedSeconds = 0;
      if (progressTimer) clearInterval(progressTimer);
      
      if (showSkeleton) {
        progressTimer = setInterval(() => {
          elapsedSeconds++;
          status.innerText = `⏳ Contacting Yahoo Finance (${elapsedSeconds}s)...`;
        }, 1000);
      }

      const tickers = document.getElementById('tickers').value;
      const delta = document.getElementById('delta').value;
      const contractTickers = Array.from(new Set(cloudPositions.map(p => (p.ticker || '').trim().toUpperCase()))).filter(Boolean);

      try {
        const res = await fetch(`/api/data?tickers=${encodeURIComponent(tickers)}&contract_tickers=${encodeURIComponent(contractTickers.join(','))}&delta=${delta}`);
        if (progressTimer) clearInterval(progressTimer);

        if (!res.ok) throw new Error("API error " + res.status);
        const data = await res.json();

        localStorage.setItem('cached_options_payload', JSON.stringify(data));
        applyDataPayload(data);
        hasLoadedOnce = true;

        if (data.is_cached) {
          status.innerHTML = `<span class="text-amber-600 font-bold">⚠️ Using Cached Data</span>`;
        } else {
          status.innerHTML = `<span class="text-emerald-600 font-bold">✓ Live Updated</span> at ${new Date().toLocaleTimeString()}`;
        }
      } catch (err) {
        if (progressTimer) clearInterval(progressTimer);
        const localSaved = localStorage.getItem('cached_options_payload');
        if (localSaved) {
          try {
            const fallbackData = JSON.parse(localSaved);
            fallbackData.is_cached = true;
            fallbackData.cached_at = "Browser Local Backup";
            applyDataPayload(fallbackData);
            hasLoadedOnce = true;
            status.innerHTML = `<span class="text-amber-600 font-bold">⚠️ Using Browser Backup Cache</span>`;
            return;
          } catch(e) {}
        }

        status.innerHTML = `<span class="text-rose-600 font-bold">✕ Yahoo Finance Unreachable.</span> Please retry.`;
        if (showSkeleton) {
          document.getElementById('putsBody').innerHTML = `<tr><td class="p-3 text-center text-rose-500 font-semibold">Failed to load Put chain: Network or rate limit issue.</td></tr>`;
          document.getElementById('callsBody').innerHTML = `<tr><td class="p-3 text-center text-rose-500 font-semibold">Failed to load Call chain: Network or rate limit issue.</td></tr>`;
        }
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
        tbody.innerHTML = `<tr><td colspan="8" class="p-3 text-center text-rose-500 font-semibold">No ticker quotes returned.</td></tr>`;
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
      renderTable('putsBody', globalData.puts, displayTickers, globalData.targets, 'Put', alertCriteria.putHighIvPctile, 'putHighIvNotice');
      renderTable('callsBody', globalData.calls, displayTickers, globalData.targets, 'Call', alertCriteria.callHighIvPctile, 'callHighIvNotice');
    }

    function renderTable(elementId, results, tickers, targets, tableType, highIvPctileThreshold, headerNoticeId) {
      const headerNotice = document.getElementById(headerNoticeId);
      if (headerNotice) headerNotice.classList.add('hidden'); // Clear banner state first

      const tbody = document.getElementById(elementId);
      tbody.innerHTML = '';

      if (!tickers || tickers.length === 0) {
        tbody.innerHTML = `<tr><td class="p-4 text-center text-slate-400">No active tickers found in the Tickers box.</td></tr>`;
        return;
      }

      let hasHighIvInTable = false;

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
          <th class="p-1.5 border-r whitespace-nowrap ${c.sub}">IV (%ile)</th>
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
            
            const ivp = item.iv_pctile;
            const hasIvp = (ivp !== undefined && ivp !== null);
            const isHighIv = hasIvp && (ivp >= highIvPctileThreshold);
            if (isHighIv) hasHighIvInTable = true;

            const pctileText = hasIvp ? `(${Math.round(ivp)}%)` : '';
            const ivDisplay = isHighIv
              ? `<span class="bg-rose-100 text-rose-800 font-extrabold px-1 py-0.5 rounded border border-rose-300 animate-pulse whitespace-nowrap" title="High IV Percentile (&ge; ${highIvPctileThreshold}%)">🔥 ${item.iv}% ${pctileText}</span>`
              : `<span class="text-slate-600">${item.iv}% <span class="text-[9px] text-slate-400">${pctileText}</span></span>`;

            rowHtml += `
              <td class="p-1 border-r text-center leading-tight text-[10px] text-slate-700">${item.exp}</td>
              <td class="p-1.5 border-r whitespace-nowrap ${strikeBg}">$${item.strike} <span class="text-[9px]">(${item.pct_diff})</span></td>
              <td class="p-1.5 border-r whitespace-nowrap font-mono text-center">${ivDisplay}</td>
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

      if (headerNotice) {
        if (hasHighIvInTable) {
          headerNotice.innerText = `🔥 High IVP ${tableType} Alert (&ge; ${highIvPctileThreshold}%ile)`;
          headerNotice.classList.remove('hidden');
        }
      }

      if (totalContractsPopulated === 0) {
        tbody.innerHTML += `
          <tr>
            <td colspan="${1 + tickers.length * 5}" class="p-2.5 bg-amber-50 text-amber-800 text-center text-[10px]">
              No ${tableType} contracts matched the target delta or business-day windows.
            </td>
          </tr>
        `;
      }
    }

    (async () => {
      loadAlertCriteria();
      await loadCloudPositions();
      await fetchData(false);
      setInterval(() => fetchData(true), 60000);
    })();
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
    positions.append(pos.to_dict())
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
            positions[i] = updated.to_dict()
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
        hist_vols = []

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
            
            df_hist_1y = tkr.history(period="1y")
            if df_hist_1y is not None and len(df_hist_1y) >= 30:
                df_hist = df_hist_1y.tail(30)
                df_clean = df_hist_1y['Close'].dropna()
                df_clean = df_clean[df_clean > 0]
                returns = np.log(df_clean / df_clean.shift(1)).dropna()
                if len(returns) >= 20:
                    rolling_vol = returns.rolling(window=20).std() * math.sqrt(252)
                    hist_vols = [float(v) for v in rolling_vol.dropna().tolist() if not math.isnan(v) and v > 0]
            else:
                df_hist = tkr.history(period="30d")

            if spot_price > 0:
                successful_fetches += 1
        except Exception as e:
            if is_primary:
                diagnostics[ticker] = f"Error reaching Yahoo Finance: {str(e)}"

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
            
            # Safe Fallback to prevent math.isnan crashing JSON stringify in Javascript
            if math.isnan(prev_high) or math.isnan(prev_low) or math.isnan(prev_close):
                s1, r1 = spot_price * 0.95, spot_price * 1.05
                rolling_support, rolling_resistance = spot_price * 0.90, spot_price * 1.10
            else:
                pivot = (prev_high + prev_low + prev_close) / 3.0
                s1 = (2 * pivot) - prev_high
                r1 = (2 * pivot) - prev_low
                rolling_support = float(df_hist['Low'].min())
                rolling_resistance = float(df_hist['High'].max())
                
                # Double check bounds for safety
                if math.isnan(rolling_support): rolling_support = spot_price * 0.90
                if math.isnan(rolling_resistance): rolling_resistance = spot_price * 1.10
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
            p_tkr = str(p.get("ticker", "")).strip().upper()
            if p_tkr == ticker and p.get("exp") in expirations:
                needed_exps.add(p["exp"])

        loaded_chains = {}
        for exp in needed_exps:
            try:
                loaded_chains[exp] = tkr.option_chain(exp)
            except Exception:
                continue

        for p in positions:
            p_tkr = str(p.get("ticker", "")).strip().upper()
            p_exp = str(p.get("exp", "")).strip()
            p_type = str(p.get("type", "")).strip().upper()
            k_target = float(p.get("strike", 0))

            if p_tkr == ticker and p_exp in loaded_chains:
                chain = loaded_chains[p_exp]
                df_opts = chain.puts if p_type == "PUT" else chain.calls
                
                if df_opts is not None and not df_opts.empty:
                    diffs = (df_opts["strike"] - k_target).abs()
                    min_idx = diffs.idxmin()
                    if diffs.loc[min_idx] <= 0.5:
                        row_data = df_opts.loc[min_idx]
                        ask = float(row_data.get("ask", 0) or 0)
                        bid = float(row_data.get("bid", 0) or 0)
                        last_p = float(row_data.get("lastPrice", 0) or 0)
                        mark = ask if ask > 0 else (last_p if last_p > 0 else (bid if bid > 0 else 0.01))
                        
                        contract_key = f"{p_tkr}_{p_exp}_{k_target:.2f}_{p_type}"
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
                            # Safe retrieval to prevent math.isnan crashing JSON stringify in Javascript
                            iv_raw = row.get('impliedVolatility', 0.45)
                            iv_val = 0.45 if iv_raw is None or math.isnan(float(iv_raw)) else float(iv_raw)
                            
                            d = calc_put_delta(spot_price, K, T, r, sigma=iv_val)
                            if abs(d - (-delta)) < min_p_diff:
                                min_p_diff = abs(d - (-delta))
                                best_put = (row, K, iv_val)
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
                        iv_pctile = calc_iv_percentile(iv_val, hist_vols)

                        results_puts[str(target)][ticker] = {
                            "raw_exp": exp,
                            "exp": exp_stacked,
                            "strike": round(k_val, 2),
                            "pct_diff": f"{pct_diff:+.1f}%",
                            "iv": round((iv_val or 0.45) * 100, 1),
                            "iv_pctile": iv_pctile,
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
                            # Safe retrieval to prevent math.isnan crashing JSON stringify in Javascript
                            iv_raw = row.get('impliedVolatility', 0.45)
                            iv_val = 0.45 if iv_raw is None or math.isnan(float(iv_raw)) else float(iv_raw)
                            
                            d = calc_call_delta(spot_price, K, T, r, sigma=iv_val)
                            if abs(d - delta) < min_c_diff:
                                min_c_diff = abs(d - delta)
                                best_call = (row, K, iv_val)
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
                        iv_pctile = calc_iv_percentile(iv_val, hist_vols)

                        results_calls[str(target)][ticker] = {
                            "raw_exp": exp,
                            "exp": exp_stacked,
                            "strike": round(k_val, 2),
                            "pct_diff": f"{pct_diff:+.1f}%",
                            "iv": round((iv_val or 0.45) * 100, 1),
                            "iv_pctile": iv_pctile,
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

@app.get("/", response_class=HTMLResponse)
def render_index():
    return HTMLResponse(content=HTML_CONTENT)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
