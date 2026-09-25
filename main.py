from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import yfinance as yf
import pandas as pd
from types import SimpleNamespace
import numpy as np
import math
import datetime
import os
import json
import base64
import requests
import tempfile

app = FastAPI()

# --- Github Storage Config ---
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")
GITHUB_FILE_PATH = os.getenv("GITHUB_FILE_PATH", "positions.json")

# --- Twilio Config ---
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "+17372508034")
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "")
TWILIO_SMS_FROM = os.getenv("TWILIO_SMS_FROM", TWILIO_FROM_NUMBER)

# --- Market Data Provider (marketdata.app) Config ---
MARKETDATA_API_TOKEN = os.getenv("MARKETDATA_API_TOKEN", "")
MD_BASE = "https://api.marketdata.app/v1"
MD_MODE = os.getenv("MD_MODE", "cached")
MD_TIMEOUT = int(os.getenv("MD_TIMEOUT", "15"))

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

def get_safe_mid_price(row_data):
    try:
        ask_raw = row_data.get("ask", 0)
        bid_raw = row_data.get("bid", 0)
        last_raw = row_data.get("lastPrice", 0)

        ask = float(ask_raw) if ask_raw is not None and not math.isnan(float(ask_raw)) else 0.0
        bid = float(bid_raw) if bid_raw is not None and not math.isnan(float(bid_raw)) else 0.0
        last_p = float(last_raw) if last_raw is not None and not math.isnan(float(last_raw)) else 0.0

        if bid > 0 and ask > 0: return (bid + ask) / 2.0
        if bid > 0: return bid
        if ask > 0: return ask
        if last_p > 0: return last_p
        return 0.01 
    except Exception:
        return 0.01

def get_positions_from_github():
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return [], None
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
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
    if not GITHUB_TOKEN or not GITHUB_REPO: return False
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    _, sha = get_positions_from_github()
    content_str = json.dumps(positions, indent=2)
    encoded = base64.b64encode(content_str.encode('utf-8')).decode('utf-8')
    payload = {"message": "Update positions storage", "content": encoded}
    if sha: payload["sha"] = sha
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
        except Exception: pass
    return {}

def save_cached_data(cache):
    try:
        with open(CACHE_FILE_PATH, "w") as f:
            json.dump(cache, f, indent=2)
    except Exception: pass

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

class AlertPayload(BaseModel):
    message: str
    to_number: str

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
    <ul id="diagList" class="list-disc list-inside space-y-0.5 text-[11px] text-amber-800 mt-1"></ul>
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
        </div>
        <div>
          <label class="font-semibold text-slate-600 block mb-0.5">CSP Warn DTE (&le; days)</label>
          <input id="critPutWarnDte" type="number" step="1" class="w-full border rounded p-1 text-xs bg-white" onchange="saveAlertCriteria()">
        </div>
        <div>
          <label class="font-semibold text-slate-600 block mb-0.5">CSP Critical DTE (&le; days)</label>
          <input id="critPutCritDte" type="number" step="1" class="w-full border rounded p-1 text-xs bg-white" onchange="saveAlertCriteria()">
        </div>
        <div>
          <label class="font-semibold text-slate-600 block mb-0.5">CC Tested Buffer (%)</label>
          <input id="critCallWarnPct" type="number" step="0.5" class="w-full border rounded p-1 text-xs bg-white" onchange="saveAlertCriteria()">
        </div>
        <div class="bg-amber-50/70 p-1.5 rounded border border-amber-200">
          <label class="font-bold text-amber-900 block mb-0.5">🔥 Put High Vol (%ile)</label>
          <input id="critPutHighIvPctile" type="number" step="5" class="w-full border rounded p-1 text-xs bg-white font-bold" onchange="saveAlertCriteria()">
        </div>
        <div class="bg-amber-50/70 p-1.5 rounded border border-amber-200">
          <label class="font-bold text-amber-900 block mb-0.5">🔥 Call High Vol (%ile)</label>
          <input id="critCallHighIvPctile" type="number" step="5" class="w-full border rounded p-1 text-xs bg-white font-bold" onchange="saveAlertCriteria()">
        </div>
        <!-- Alert Recipient Settings -->
        <div class="col-span-2 sm:col-span-3 mt-1 pt-2 border-t border-slate-200 flex flex-col sm:flex-row sm:items-center gap-2">
          <label class="font-bold text-emerald-800 flex items-center gap-1.5 whitespace-nowrap bg-emerald-50 px-2 py-1 rounded border border-emerald-200">
            <input type="checkbox" id="critWaEnabled" onchange="saveAlertCriteria()">
            <span>Enable Twilio Alerts</span>
          </label>
          <input id="critWaNumber" type="text" placeholder="Format: +1234567890 (SMS) or whatsapp:+1234567890" class="w-full sm:w-80 border rounded p-1 text-xs bg-white font-mono" onchange="saveAlertCriteria()" onblur="saveAlertCriteria()">
          <span class="text-[9px] text-slate-400">1-hour cooldown per ticker to prevent spam.</span>
        </div>
      </div>
    </div>

    <!-- P/L Metrics Cards -->
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
      <div id="openSummaryCards" class="flex flex-wrap gap-2"></div>
    </div>

    <!-- Add Position Form -->
    <div id="positionForm" class="hidden bg-slate-50 p-2.5 rounded-lg border border-slate-200 mb-3 space-y-2">
      <div class="grid grid-cols-4 gap-2">
        <div>
          <label class="text-[10px] font-bold text-slate-500">Action</label>
          <select id="posAction" class="w-full border rounded p-1.5 text-xs bg-white"><option value="SELL">Sell (Write)</option><option value="BUY">Buy (Long)</option></select>
        </div>
        <div>
          <label class="text-[10px] font-bold text-slate-500">Type</label>
          <select id="posType" class="w-full border rounded p-1.5 text-xs bg-white"><option value="PUT">PUT (CSP)</option><option value="CALL">CALL (CC)</option></select>
        </div>
        <div>
          <label class="text-[10px] font-bold text-slate-500">Ticker</label>
          <input id="posTicker" type="text" placeholder="IREN" class="w-full border rounded p-1.5 text-xs uppercase font-semibold">
        </div>
        <div>
          <label class="text-[10px] font-bold text-slate-500">Broker</label>
          <select id="posBroker" class="w-full border rounded p-1.5 text-xs bg-white font-semibold"><option value="moomoo">MOOMOO</option><option value="ibkr">IBKR</option></select>
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
          <label class="text-[10px] font-bold text-slate-500">Trade Day</label>
          <input id="posTradeDate" type="date" class="w-full border rounded p-1.5 text-xs bg-white">
        </div>
        <div>
          <label class="text-[10px] font-bold text-slate-500">Exp Date</label>
          <input id="posExp" type="date" class="w-full border rounded p-1.5 text-xs bg-white">
        </div>
      </div>
      <div class="flex gap-2 pt-1">
        <button onclick="savePosition()" class="bg-emerald-600 active:bg-emerald-700 text-white font-bold py-1.5 px-3 rounded text-xs flex-1">Save</button>
        <button onclick="toggleAddForm()" class="bg-slate-300 text-slate-700 font-bold py-1.5 px-3 rounded text-xs">Cancel</button>
      </div>
    </div>

    <!-- Contract Positions Table -->
    <div class="overflow-x-auto border border-slate-200 rounded-lg">
      <table class="w-full text-left text-[10px]">
        <thead class="bg-slate-100 border-b border-slate-200 text-slate-600 font-bold">
          <tr>
            <th class="p-1.5 border-r">Broker</th>
            <th class="p-1.5 border-r">Pos</th>
            <th class="p-1.5 border-r p-0">
               <select id="posTickerFilter" onchange="renderPositionsAndPL()" class="w-full bg-transparent font-bold text-slate-600 outline-none cursor-pointer px-1 py-1">
                 <option value="ALL">Contract (All)</option>
               </select>
            </th>
            <th class="p-1.5 border-r">Trade Day</th>
            <th class="p-1.5 border-r">Exp</th>
            <th class="p-1.5 border-r">Entry</th>
            <th class="p-1.5 border-r">Live Mark</th>
            <th class="p-1.5 border-r font-extrabold text-blue-900 bg-blue-50/70">Cur P/L ($)</th>
            <th class="p-1.5 border-r">Max P/L ($)</th>
            <th class="p-1.5 border-r">Status</th>
            <th class="p-1.5 text-center">Del</th>
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
    <div class="grid grid-cols-2 sm:grid-cols-3 gap-2 mb-2">
      <div>
        <label class="text-[11px] font-bold text-slate-500 uppercase tracking-wide">Tickers</label>
        <input id="tickers" type="text" value="IREN, RKLB" class="w-full border rounded p-2 text-sm uppercase font-semibold">
      </div>
      <div>
        <label class="text-[11px] font-bold text-slate-500 uppercase tracking-wide">Delta</label>
        <input id="delta" type="number" step="0.01" value="0.2" class="w-full border rounded p-2 text-sm font-semibold">
      </div>
      <div class="col-span-2 sm:col-span-1">
        <label class="text-[11px] font-bold text-slate-500 uppercase tracking-wide">Data Source</label>
        <select id="providerSelect" class="w-full border rounded p-2 text-sm font-semibold bg-white text-slate-700">
          <option value="marketdata">MarketData.app (Fast)</option>
          <option value="yfinance">Yahoo Finance (Free)</option>
        </select>
      </div>
    </div>
    <button onclick="fetchData(false)" id="refreshBtn" class="bg-blue-600 active:bg-blue-700 text-white font-bold py-2 px-4 rounded-lg text-sm w-full mt-1 flex items-center justify-center gap-2">
      <span id="btnSpinner" class="hidden animate-spin h-3.5 w-3.5 border-2 border-white border-t-transparent rounded-full"></span>
      <span id="btnText">Refresh Data</span>
    </button>
    <div id="status" class="text-[11px] text-slate-500 mt-1.5 text-right font-medium">Ready <span id="providerBadge" class="ml-1 px-1.5 py-0.5 rounded bg-slate-200 text-slate-600 font-bold"></span></div>
  </div>

  <!-- 3. Key Technical Levels -->
  <div class="mb-3">
    <h2 class="text-xs font-bold text-blue-950 bg-blue-100/80 p-2.5 rounded-t-lg border-t border-x border-blue-200 flex items-center justify-between">
      <span>📊 Spot &amp; Key Technical Levels</span>
    </h2>
    <div class="overflow-x-auto bg-white border border-slate-200 rounded-b-lg shadow-sm">
      <table class="w-full text-left" id="levelsTable">
        <thead class="bg-slate-50 border-b border-slate-200 text-[11px] text-slate-600">
          <tr>
            <th class="p-2 border-r font-bold">Ticker</th>
            <th class="p-2 border-r font-bold">Spot</th>
            <th class="p-2 border-r font-bold text-emerald-800 bg-emerald-50/50">Support Floor</th>
            <th class="p-2 border-r font-medium text-slate-600">S1 Pivot</th>
            <th class="p-2 border-r font-medium text-slate-600">30d Low</th>
            <th class="p-2 border-r font-bold text-rose-800 bg-rose-50/50">Resistance Ceiling</th>
            <th class="p-2 border-r font-medium text-slate-600">R1 Pivot</th>
            <th class="p-2 font-medium text-slate-600">30d High</th>
          </tr>
        </thead>
        <tbody id="levelsBody"><tr><td colspan="8" class="p-3 text-center text-slate-400">Loading quotes...</td></tr></tbody>
      </table>
    </div>
  </div>

  <div class="flex items-center gap-1.5 mb-3 overflow-x-auto py-1">
    <span class="text-[11px] font-bold text-slate-500 mr-1">View:</span>
    <div id="tickerPills" class="flex gap-1.5"></div>
  </div>

  <!-- 5. Cash-Secured Puts -->
  <div class="mb-4">
    <h2 class="text-xs font-bold text-sky-900 bg-sky-100 p-2.5 rounded-t-lg border-t border-x border-sky-200 flex items-center justify-between">
      <span>📉 Cash-Secured Puts (Green = Strike &lt; Support/Floor)</span>
      <span id="putHighIvNotice" class="text-[10px] text-rose-800 font-bold hidden bg-rose-200/80 px-2 py-0.5 rounded animate-pulse">🔥 High Volatility Put Alert</span>
    </h2>
    <div class="overflow-x-auto bg-white border border-slate-200 rounded-b-lg shadow-sm">
      <table class="w-full text-left" id="putsTable"><tbody id="putsBody"></tbody></table>
    </div>
  </div>

  <!-- 6. Covered Calls -->
  <div class="mb-6">
    <h2 class="text-xs font-bold text-amber-900 bg-amber-100 p-2.5 rounded-t-lg border-t border-x border-amber-200 flex items-center justify-between">
      <span>📈 Covered Calls (Green = Strike &gt; Resistance/Ceiling)</span>
      <span id="callHighIvNotice" class="text-[10px] text-rose-800 font-bold hidden bg-rose-200/80 px-2 py-0.5 rounded animate-pulse">🔥 High Volatility Call Alert</span>
    </h2>
    <div class="overflow-x-auto bg-white border border-slate-200 rounded-b-lg shadow-sm">
      <table class="w-full text-left" id="callsTable"><tbody id="callsBody"></tbody></table>
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
      putWarnPct: 3.0, putWarnDte: 10, putCritDte: 5,
      callWarnPct: 2.0, putHighIvPctile: 85.0, callHighIvPctile: 80.0,
      waEnabled: false, waNumber: ""
    };

    let alertCriteria = { ...DEFAULT_ALERT_CRITERIA };

    function loadAlertCriteria() {
      const saved = localStorage.getItem('alertCriteria');
      if (saved) {
        try { alertCriteria = { ...DEFAULT_ALERT_CRITERIA, ...JSON.parse(saved) }; } catch(e) {}
      }
      document.getElementById('critPutWarnPct').value = alertCriteria.putWarnPct;
      document.getElementById('critPutWarnDte').value = alertCriteria.putWarnDte;
      document.getElementById('critPutCritDte').value = alertCriteria.putCritDte;
      document.getElementById('critCallWarnPct').value = alertCriteria.callWarnPct;
      document.getElementById('critPutHighIvPctile').value = alertCriteria.putHighIvPctile;
      document.getElementById('critCallHighIvPctile').value = alertCriteria.callHighIvPctile;
      document.getElementById('critWaEnabled').checked = alertCriteria.waEnabled;
      document.getElementById('critWaNumber').value = alertCriteria.waNumber;
    }

    function saveAlertCriteria() {
      alertCriteria.putWarnPct = parseFloat(document.getElementById('critPutWarnPct').value) || 3.0;
      alertCriteria.putWarnDte = parseInt(document.getElementById('critPutWarnDte').value) || 10;
      alertCriteria.putCritDte = parseInt(document.getElementById('critPutCritDte').value) || 5;
      alertCriteria.callWarnPct = parseFloat(document.getElementById('critCallWarnPct').value) || 2.0;
      alertCriteria.putHighIvPctile = parseFloat(document.getElementById('critPutHighIvPctile').value) || 85.0;
      alertCriteria.callHighIvPctile = parseFloat(document.getElementById('critCallHighIvPctile').value) || 80.0;
      alertCriteria.waEnabled = document.getElementById('critWaEnabled').checked;
      
      alertCriteria.waNumber = document.getElementById('critWaNumber').value.trim();
      
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

    function toggleAlertSettings() { document.getElementById('alertSettingsPanel').classList.toggle('hidden'); }

    const EXP_COLOR_PALETTE = ['bg-indigo-50/80', 'bg-amber-50/80', 'bg-emerald-50/80', 'bg-purple-50/80', 'bg-rose-50/80', 'bg-sky-50/80', 'bg-teal-50/80'];

    function toggleHideExpired(checked) {
      hideExpired = checked;
      localStorage.setItem('hideExpiredContracts', checked);
      renderPositionsAndPL();
    }

    function getExpColor(expKey, map) {
      if (!expKey) return 'hover:bg-slate-50';
      if (!map[expKey]) map[expKey] = EXP_COLOR_PALETTE[Object.keys(map).length % EXP_COLOR_PALETTE.length];
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
      } catch (e) {}
    }

    async function savePosition() {
      const p = {
        id: Date.now(),
        action: document.getElementById('posAction').value,
        type: document.getElementById('posType').value,
        ticker: document.getElementById('posTicker').value.trim().toUpperCase(),
        broker: document.getElementById('posBroker').value,
        strike: parseFloat(document.getElementById('posStrike').value),
        prem: parseFloat(document.getElementById('posPrem').value),
        qty: parseInt(document.getElementById('posQty').value) || 1,
        exp: document.getElementById('posExp').value,
        trade_date: document.getElementById('posTradeDate').value || new Date().toISOString().split('T')[0]
      };
      if (!p.ticker || isNaN(p.strike) || isNaN(p.prem) || !p.exp) return alert('Fill fields properly.');
      const res = await fetch('/api/positions', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(p) });
      if (res.ok) { toggleAddForm(); await loadCloudPositions(); fetchData(false); }
    }

    async function updatePositionField(id, field, value) {
      const pos = cloudPositions.find(p => p.id === id);
      if (!pos) return;
      if (field === 'strike' || field === 'prem') pos[field] = parseFloat(value) || 0;
      else if (field === 'qty') pos[field] = parseInt(value) || 1;
      else pos[field] = value;
      if (!pos.broker) pos.broker = 'moomoo';

      const res = await fetch(`/api/positions/${id}`, { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(pos) });
      if (res.ok && (field === 'exp' || field === 'ticker' || field === 'strike')) fetchData(true);
    }

    async function deletePosition(id) {
      if (!confirm("Delete?")) return;
      if ((await fetch(`/api/positions/${id}`, { method: 'DELETE' })).ok) loadCloudPositions();
    }

    function renderPositionsAndPL() {
      document.getElementById('hideExpiredToggle').checked = hideExpired;
      
      const filterSelect = document.getElementById('posTickerFilter');
      const currentFilter = filterSelect ? filterSelect.value : 'ALL';
      const uniqueTickers = Array.from(new Set(cloudPositions.map(p => p.ticker))).sort();
      if (filterSelect) {
        filterSelect.innerHTML = `<option value="ALL">Contract (All)</option>` + uniqueTickers.map(t => `<option value="${t}">${t}</option>`).join('');
        filterSelect.value = uniqueTickers.includes(currentFilter) ? currentFilter : 'ALL';
      }
      const activeFilter = filterSelect ? filterSelect.value : 'ALL';
      
      const tbody = document.getElementById('positionsBody');
      const now = new Date();
      const currentYear = now.getFullYear();
      const currentMonth = now.getMonth();
      const todayStr = now.toISOString().split('T')[0];
      let lastMonthYear = currentYear, lastMonth = currentMonth - 1;
      if (lastMonth < 0) { lastMonth = 11; lastMonthYear--; }

      let totalRealized = 0, thisMonthRealized = 0, lastMonthRealized = 0;
      let totalMaxUnrealized = 0, totalCurrentUnrealized = 0, thisMonthCurrentUnrealized = 0;
      const openStats = {};
      let totalOpenCount = 0, totalCspCapital = 0, moomooCspCapital = 0, ibkrCspCapital = 0;

      cloudPositions.forEach(p => {
        const parts = (p.exp || '').split('-');
        const isExpired = p.exp < todayStr;
        const isThisMonth = (parseInt(parts[0], 10) === currentYear && parseInt(parts[1], 10) - 1 === currentMonth);
        const isLastMonth = (parseInt(parts[0], 10) === lastMonthYear && parseInt(parts[1], 10) - 1 === lastMonth);

        const spot = (globalData && globalData.all_spots && globalData.all_spots[p.ticker] !== undefined) ? globalData.all_spots[p.ticker] : null;
        const liveMark = (globalData && globalData.live_positions && globalData.live_positions[`${p.ticker.trim().toUpperCase()}_${p.exp.trim()}_${parseFloat(p.strike).toFixed(2)}_${p.type.trim().toUpperCase()}`]) || null;

        if (isExpired) {
          let closedPl = 0;
          if (p.action === 'SELL') {
            if (p.type === 'CALL') {
              closedPl = p.prem * 100 * p.qty;
            } else {
              if (spot !== null) {
                const isWin = spot >= p.strike;
                closedPl = isWin ? p.prem * 100 * p.qty : (p.prem - Math.max(p.strike - spot, 0)) * 100 * p.qty;
              } else closedPl = p.prem * 100 * p.qty;
            }
          } else {
            closedPl = ((p.type === 'CALL' ? Math.max((spot || 0) - p.strike, 0) : Math.max(p.strike - (spot || 0), 0)) - p.prem) * 100 * p.qty;
          }
          totalRealized += closedPl;
          if (isThisMonth) thisMonthRealized += closedPl;
          if (isLastMonth) lastMonthRealized += closedPl;
        } else {
          totalOpenCount += p.qty;
          const broker = (p.broker || 'moomoo').toLowerCase();
          if (!openStats[p.ticker]) openStats[p.ticker] = { total: 0, puts: 0, calls: 0, cspCapital: 0, moomooCsp: 0, ibkrCsp: 0 };
          openStats[p.ticker].total += p.qty;

          if (p.type === 'PUT') {
            openStats[p.ticker].puts += p.qty;
            const cap = p.strike * 100 * p.qty;
            openStats[p.ticker].cspCapital += cap;
            totalCspCapital += cap;
            if (broker === 'moomoo') { moomooCspCapital += cap; openStats[p.ticker].moomooCsp += cap; }
            else if (broker === 'ibkr') { ibkrCspCapital += cap; openStats[p.ticker].ibkrCsp += cap; }
          } else if (p.type === 'CALL') openStats[p.ticker].calls += p.qty;

          const maxPl = (p.action === 'SELL') ? (p.prem * 100 * p.qty) : (-p.prem * 100 * p.qty);
          totalMaxUnrealized += maxPl;
          if (isThisMonth) thisMonthCurrentUnrealized += maxPl;

          if (liveMark !== null) {
            totalCurrentUnrealized += (p.action === 'SELL') ? (p.prem - liveMark) * 100 * p.qty : (liveMark - p.prem) * 100 * p.qty;
          }
        }
      });

      const summaryContainer = document.getElementById('openSummaryCards');
      document.getElementById('totalOpenQty').innerText = `Total Open: ${totalOpenCount}`;
      const fmtCurrency = (val) => `$${val.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
      document.getElementById('moomooCspCapital').innerText = `Moomoo CSP: ${fmtCurrency(moomooCspCapital)}`;
      document.getElementById('ibkrCspCapital').innerText = `IBKR CSP: ${fmtCurrency(ibkrCspCapital)}`;
      document.getElementById('totalCspCapital').innerText = `Total CSP: ${fmtCurrency(totalCspCapital)}`;

      const openTickers = Object.keys(openStats);
      if (openTickers.length === 0) summaryContainer.innerHTML = '<span class="text-slate-400 text-[10px]">No active open contracts.</span>';
      else {
        summaryContainer.innerHTML = '';
        openTickers.forEach(t => {
          const s = openStats[t], card = document.createElement('div');
          card.className = "bg-white border border-slate-200 rounded px-2.5 py-1 text-[10px] flex items-center gap-2 shadow-xs";
          let cspBadges = '';
          if (s.cspCapital > 0) {
            const parts = [];
            if (s.moomooCsp > 0) parts.push(`<span class="text-orange-700 font-bold">Moo: ${fmtCurrency(s.moomooCsp)}</span>`);
            if (s.ibkrCsp > 0) parts.push(`<span class="text-blue-700 font-bold">IB: ${fmtCurrency(s.ibkrCsp)}</span>`);
            cspBadges = `<div class="bg-emerald-50 border border-emerald-200 px-1.5 py-0.5 rounded flex items-center gap-1 font-mono">${parts.join(' | ')}</div>`;
          }
          card.innerHTML = `<span class="font-bold text-slate-800">${t}:</span><span class="font-extrabold text-blue-700">${s.total}</span><span class="text-[9px] text-slate-400 font-mono">(${s.puts}P / ${s.calls}C)</span>${cspBadges}`;
          summaryContainer.appendChild(card);
        });
      }

      let visiblePositions = cloudPositions.filter(p => !hideExpired || p.exp >= todayStr);
      if (activeFilter !== 'ALL') {
        visiblePositions = visiblePositions.filter(p => p.ticker === activeFilter);
      }
      visiblePositions.sort((a, b) => new Date(a.exp) - new Date(b.exp) || a.ticker.localeCompare(b.ticker));
      
      if (visiblePositions.length === 0) tbody.innerHTML = `<tr><td colspan="11" class="p-2 text-center text-slate-400">No active positions found.</td></tr>`;
      else tbody.innerHTML = '';

      const posColorMap = {};
      visiblePositions.forEach(p => {
        const isExpired = p.exp < todayStr;
        const spot = (globalData && globalData.all_spots && globalData.all_spots[p.ticker] !== undefined) ? globalData.all_spots[p.ticker] : null;
        const liveMark = (globalData && globalData.live_positions && globalData.live_positions[`${p.ticker.trim().toUpperCase()}_${p.exp.trim()}_${parseFloat(p.strike).toFixed(2)}_${p.type.trim().toUpperCase()}`]) || null;
        const dte = Math.ceil((new Date(p.exp) - new Date(todayStr)) / 86400000);

        let maxPl = 0, currentPl = 0, statusHtml = '<span class="px-1.5 py-0.5 rounded bg-blue-100 text-blue-800 font-bold">Active</span>';
        let alertMsg = null;

        if (isExpired) {
          if (p.action === 'SELL') {
            if (p.type === 'CALL') {
              maxPl = p.prem * 100 * p.qty;
              currentPl = maxPl;
              const isAssigned = (spot !== null && spot >= p.strike);
              statusHtml = isAssigned 
                ? '<span class="px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800 font-bold whitespace-nowrap">Assigned (Win)</span>'
                : '<span class="px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800 font-bold whitespace-nowrap">Expired (Win)</span>';
            } else {
              if (spot !== null) {
                const isWin = spot >= p.strike;
                if (isWin) { 
                  maxPl = p.prem * 100 * p.qty; 
                  statusHtml = '<span class="px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800 font-bold whitespace-nowrap">Expired (Win)</span>'; 
                } else {
                  maxPl = (p.prem - Math.max(p.strike - spot, 0)) * 100 * p.qty;
                  statusHtml = '<span class="px-1.5 py-0.5 rounded bg-rose-100 text-rose-800 font-bold whitespace-nowrap">Assigned</span>';
                }
              } else { 
                maxPl = p.prem * 100 * p.qty; 
                statusHtml = '<span class="px-1.5 py-0.5 rounded bg-slate-200 text-slate-700 font-bold whitespace-nowrap">Expired</span>'; 
              }
              currentPl = maxPl;
            }
          } else {
            maxPl = ((p.type === 'CALL' ? Math.max((spot || 0) - p.strike, 0) : Math.max(p.strike - (spot || 0), 0)) - p.prem) * 100 * p.qty;
            statusHtml = '<span class="px-1.5 py-0.5 rounded bg-slate-200 text-slate-700 font-bold whitespace-nowrap">Closed</span>';
            currentPl = maxPl;
          }
        } else {
          maxPl = (p.action === 'SELL') ? (p.prem * 100 * p.qty) : (-p.prem * 100 * p.qty);
          if (liveMark !== null) currentPl = (p.action === 'SELL') ? (p.prem - liveMark) * 100 * p.qty : (liveMark - p.prem) * 100 * p.qty;

          if (p.action === 'SELL' && spot !== null) {
            const pctFromStrike = ((spot - p.strike) / p.strike) * 100;
            if (p.type === 'PUT') {
              if (spot <= p.strike || (dte <= alertCriteria.putCritDte && pctFromStrike <= 1.5)) {
                statusHtml = '<span class="px-1.5 py-0.5 rounded bg-rose-600 text-white font-extrabold animate-pulse whitespace-nowrap">ROLL / ASSIGN NOW</span>';
                alertMsg = `🚨 CRITICAL: ${p.ticker} $${p.strike} Put needs attention (ROLL / ASSIGN NOW). Spot is $${spot.toFixed(2)} (${pctFromStrike > 0 ? '+' : ''}${pctFromStrike.toFixed(1)}%).`;
              } else if (pctFromStrike <= alertCriteria.putWarnPct) {
                statusHtml = '<span class="px-1.5 py-0.5 rounded bg-amber-500 text-slate-950 font-extrabold whitespace-nowrap">ROLL SOON</span>';
                alertMsg = `⚠️ WARNING: ${p.ticker} $${p.strike} Put is tested (ROLL SOON). Spot is $${spot.toFixed(2)} (${pctFromStrike > 0 ? '+' : ''}${pctFromStrike.toFixed(1)}%). DTE: ${dte}d.`;
              }
            } else if (p.type === 'CALL') {
              if (spot >= p.strike) {
                statusHtml = '<span class="px-1.5 py-0.5 rounded bg-emerald-600 text-white font-bold whitespace-nowrap">MAX PROFIT / ASSIGN</span>';
              } else if (pctFromStrike >= -alertCriteria.callWarnPct) {
                statusHtml = '<span class="px-1.5 py-0.5 rounded bg-amber-500 text-slate-950 font-bold whitespace-nowrap">TESTED</span>';
                alertMsg = `⚠️ WARNING: ${p.ticker} $${p.strike} Call is tested. Spot is $${spot.toFixed(2)}.`;
              }
            }
          }
        }

        if (alertMsg && alertCriteria.waEnabled && alertCriteria.waNumber) {
          const now = Date.now();
          const posCooldownKey = `wa_pos_alert_${p.id}`;
          const lastPosAlert = parseInt(localStorage.getItem(posCooldownKey) || '0', 10);

          if (now - lastPosAlert > 3600000) { 
            localStorage.setItem(posCooldownKey, now.toString());
            fetch('/api/whatsapp', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                to_number: alertCriteria.waNumber,
                message: alertMsg
              })
            }).catch(e => console.error("Position Alert Error:", e));
          }
        }

        const maxPlColor = maxPl >= 0 ? 'text-emerald-700' : 'text-rose-700';
        const curPlColor = currentPl >= 0 ? 'text-emerald-700' : 'text-rose-700';
        let curPlDisplay = '<span class="text-slate-400 font-normal">Pending</span>';
        if (isExpired || liveMark !== null) {
          let pctSpan = '';
          if (maxPl !== 0) {
            const pctOfMax = (currentPl / Math.abs(maxPl)) * 100;
            let pctColor = 'text-rose-700';
            if (pctOfMax > 50) pctColor = 'text-emerald-700';
            else if (pctOfMax >= 0) pctColor = 'text-amber-600';
            
            pctSpan = `<span class="block text-[9px] font-bold ${pctColor}">(${pctOfMax >= 0 ? '+' : ''}${pctOfMax.toFixed(1)}% max)</span>`;
          }
          curPlDisplay = `<div>${currentPl >= 0 ? '+$' : '-$'}${Math.abs(currentPl).toFixed(2)}${pctSpan}</div>`;
        }

        let markDisplay = '<span class="text-amber-500 font-normal">No Quote</span>';
        if (isExpired) markDisplay = '<span class="text-slate-400 font-mono">$0.00</span>';
        else if (liveMark !== null) {
          let spotSubtext = '';
          if (spot !== null && p.strike > 0) {
            const diffPct = ((spot - p.strike) / p.strike) * 100;
            let spotColor = '';
            
            if (p.type === 'PUT') {
                if (diffPct > 7) spotColor = 'text-emerald-700 font-bold';
                else if (diffPct >= 1) spotColor = 'text-amber-600 font-bold';
                else spotColor = 'text-rose-700 font-extrabold';
            } else {
                if (diffPct < -7) spotColor = 'text-emerald-700 font-bold';
                else if (diffPct <= -1) spotColor = 'text-amber-600 font-bold';
                else spotColor = 'text-rose-700 font-extrabold';
            }
            
            spotSubtext = `<span class="block text-[8.5px] font-mono leading-tight ${spotColor}">$${spot.toFixed(2)} (${diffPct > 0 ? '+' : ''}${diffPct.toFixed(1)}%)</span>`;
          }
          markDisplay = `<div><span class="font-mono font-medium text-slate-800">$${liveMark.toFixed(2)}</span>${spotSubtext}</div>`;
        }

        const actionBadge = p.action === 'SELL' ? '<span class="px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800 font-bold">SELL</span>' : '<span class="px-1.5 py-0.5 rounded bg-blue-100 text-blue-800 font-bold">BUY</span>';
        const curBroker = (p.broker || 'moomoo').toLowerCase();

        const tr = document.createElement('tr');
        tr.className = `border-b ${getExpColor(p.exp, posColorMap)}`;
        tr.innerHTML = `
          <td class="p-1 border-r whitespace-nowrap"><select onchange="updatePositionField(${p.id}, 'broker', this.value)" class="border rounded px-1 py-0.5 bg-white font-bold text-[9px] ${curBroker === 'moomoo' ? 'text-orange-600 border-orange-200' : 'text-blue-700 border-blue-200'}"><option value="moomoo" ${curBroker === 'moomoo' ? 'selected' : ''}>MOOMOO</option><option value="ibkr" ${curBroker === 'ibkr' ? 'selected' : ''}>IBKR</option></select></td>
          <td class="p-1.5 border-r whitespace-nowrap">${actionBadge}</td>
          <td class="p-1.5 border-r whitespace-nowrap font-bold">${p.ticker} $${p.strike} ${p.type} (x${p.qty})</td>
          <td class="p-1 border-r whitespace-nowrap"><input type="date" value="${p.trade_date || ''}" onchange="updatePositionField(${p.id}, 'trade_date', this.value)" class="border rounded px-1 py-0.5 bg-white font-mono text-[9px] text-slate-700"></td>
          <td class="p-1 border-r whitespace-nowrap"><input type="date" value="${p.exp || ''}" onchange="updatePositionField(${p.id}, 'exp', this.value)" class="border rounded px-1 py-0.5 bg-white font-mono text-[9px] font-bold text-slate-700"></td>
          <td class="p-1.5 border-r whitespace-nowrap font-mono">$${p.prem.toFixed(2)}</td>
          <td class="p-1.5 border-r whitespace-nowrap">${markDisplay}</td>
          <td class="p-1.5 border-r whitespace-nowrap font-mono font-extrabold bg-blue-50/40 ${curPlColor}">${curPlDisplay}</td>
          <td class="p-1.5 border-r whitespace-nowrap font-mono font-bold ${maxPlColor}">${maxPl >= 0 ? '+$' : '-$'}${Math.abs(maxPl).toFixed(2)}</td>
          <td class="p-1.5 border-r whitespace-nowrap">${statusHtml}</td>
          <td class="p-1.5 text-center"><button onclick="deletePosition(${p.id})" class="text-rose-600 hover:text-rose-800 font-bold">✕</button></td>
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

    function applyDataPayload(data) {
      globalData = data;
      const pb = document.getElementById('providerBadge');
      if (pb) pb.innerText = data.provider === 'marketdata' ? 'marketdata.app' : 'yfinance';
      renderLevelsTable(globalData.market);
      renderPills(globalData.tickers);
      renderBothTables();
      renderPositionsAndPL();

      const diagBanner = document.getElementById('diagBanner');
      const diagList = document.getElementById('diagList');
      diagList.innerHTML = '';

      let hasErrors = globalData.diagnostics && Object.keys(globalData.diagnostics).length > 0;
      
      if (globalData.is_cached || hasErrors) {
        diagBanner.classList.remove('hidden');
        document.getElementById('diagTitle').innerText = globalData.is_cached ? "Data Unreachable: Using Backup Cache" : "Notice: Option Chain Errors";
        
        if (globalData.is_cached) {
            const li = document.createElement('li'); 
            li.innerHTML = `<span class="font-bold text-rose-700">Both MarketData & Yahoo Finance APIs rejected the request.</span> Showing last cached data.`; 
            diagList.appendChild(li);
        }

        if (hasErrors) {
            Object.entries(globalData.diagnostics).forEach(([tkr, msg]) => {
              const li = document.createElement('li'); li.innerHTML = `<span class="font-bold">${tkr}:</span> ${msg}`; diagList.appendChild(li);
            });
        }
      } else { 
        diagBanner.classList.add('hidden'); 
      }
    }

    async function fetchData(isSilent = false) {
      const btn = document.getElementById('refreshBtn'), spinner = document.getElementById('btnSpinner'), status = document.getElementById('status');
      if (!hasLoadedOnce || !isSilent) { btn.disabled = true; spinner.classList.remove('hidden'); document.getElementById('btnText').innerText = "Loading..."; }
      
      const tickersInput = document.getElementById('tickers').value;
      const deltaInput = document.getElementById('delta').value;
      const providerInput = document.getElementById('providerSelect').value;
      
      const tickers = tickersInput || "IREN,RKLB";
      const delta = deltaInput || "0.2";
      
      localStorage.setItem('savedTickers', tickers);
      localStorage.setItem('savedDelta', delta);
      localStorage.setItem('savedProvider', providerInput);
      
      const contractTickers = Array.from(new Set(cloudPositions.map(p => (p.ticker || '').trim().toUpperCase()))).filter(Boolean);

      try {
        const res = await fetch(`/api/data?tickers=${encodeURIComponent(tickers)}&contract_tickers=${encodeURIComponent(contractTickers.join(','))}&delta=${delta}&provider=${providerInput}`);
        if (!res.ok) throw new Error("API error");
        const data = await res.json();
        localStorage.setItem('cached_options_payload', JSON.stringify(data));
        applyDataPayload(data);
        hasLoadedOnce = true;
        status.innerHTML = `<span class="text-emerald-600 font-bold">✓ Live Updated</span> at ${new Date().toLocaleTimeString()}`;
      } catch (err) {
        const localSaved = localStorage.getItem('cached_options_payload');
        if (localSaved) { applyDataPayload(JSON.parse(localSaved)); hasLoadedOnce = true; status.innerHTML = `<span class="text-amber-600 font-bold">⚠️ Using Backup Cache</span>`; return; }
        status.innerHTML = `<span class="text-rose-600 font-bold">✕ Unreachable.</span>`;
      } finally {
        btn.disabled = false; spinner.classList.add('hidden'); document.getElementById('btnText').innerText = "Refresh Data";
      }
    }

    function renderPills(tickers) {
      const pillsContainer = document.getElementById('tickerPills');
      pillsContainer.innerHTML = '';
      const createBtn = (lbl) => {
        const btn = document.createElement('button');
        btn.className = `px-2.5 py-1 rounded-full font-bold text-[11px] border ${selectedTicker === lbl ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-slate-700 border-slate-300'}`;
        btn.innerText = lbl;
        btn.onclick = () => { selectedTicker = lbl; renderPills(tickers); renderBothTables(); };
        return btn;
      };
      pillsContainer.appendChild(createBtn('ALL'));
      tickers.forEach(t => pillsContainer.appendChild(createBtn(t)));
    }

    function renderLevelsTable(market) {
      const tbody = document.getElementById('levelsBody');
      tbody.innerHTML = '';
      if (Object.keys(market || {}).length === 0) return tbody.innerHTML = `<tr><td colspan="8" class="p-3 text-center text-rose-500 font-semibold">No ticker quotes returned.</td></tr>`;
      Object.entries(market).forEach(([ticker, m]) => {
        const tr = document.createElement('tr'); tr.className = "border-b hover:bg-slate-50 text-[11px]";
        tr.innerHTML = `<td class="p-2 border-r font-bold text-slate-800 bg-slate-50">${ticker}</td><td class="p-2 border-r font-bold text-blue-700 font-mono">$${m.spot}</td><td class="p-2 border-r font-bold text-emerald-800 bg-emerald-50/70 font-mono">$${m.support}</td><td class="p-2 border-r text-slate-600 font-mono">$${m.s1}</td><td class="p-2 border-r text-slate-600 font-mono">$${m.floor}</td><td class="p-2 border-r font-bold text-rose-800 bg-rose-50/70 font-mono">$${m.resistance}</td><td class="p-2 border-r text-slate-600 font-mono">$${m.r1}</td><td class="p-2 text-slate-600 font-mono">$${m.ceiling}</td>`;
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
      if (headerNotice) headerNotice.classList.add('hidden');

      const tbody = document.getElementById(elementId);
      tbody.innerHTML = '';
      if (!tickers || tickers.length === 0) return tbody.innerHTML = `<tr><td class="p-4 text-center text-slate-400">No tickers.</td></tr>`;

      let hasHighIvInTable = false;
      const tickerColors = [{ header: 'bg-slate-700 text-white', sub: 'bg-slate-100 text-slate-700' }, { header: 'bg-indigo-900 text-white', sub: 'bg-indigo-50 text-indigo-950' }, { header: 'bg-teal-900 text-white', sub: 'bg-teal-50 text-teal-950' }];

      let headHtml = `<tr class="border-b text-[11px]"><th class="p-2 border-r-2 border-r-slate-400 bg-slate-200">Target</th>`;
      tickers.forEach((t, i) => { headHtml += `<th class="p-2 border-r-2 border-r-slate-400 text-center font-extrabold ${tickerColors[i % 3].header}" colspan="5">${t}</th>`; });
      headHtml += `</tr><tr class="border-b text-[10px] font-semibold"><th class="p-1 border-r-2 border-r-slate-400 bg-slate-100"></th>`;
      tickers.forEach((_, i) => { const c = tickerColors[i % 3].sub; headHtml += `<th class="p-1.5 border-r text-center ${c}">Exp</th><th class="p-1.5 border-r ${c}">Strike (% Spot)</th><th class="p-1.5 border-r ${c}">IV (%ile)</th><th class="p-1.5 border-r ${c}">Prem</th><th class="p-1.5 border-r-2 border-r-slate-400 ${c}">Ann %</th>`; });
      tbody.innerHTML += headHtml + `</tr>`;

      const tableColorMap = {};
      let totalContractsPopulated = 0;

      targets.forEach(tgt => {
        const targetDict = (results && (results[tgt] || results[String(tgt)])) || {};
        let rowExpKey = ''; for (const t of tickers) { if (targetDict[t] && targetDict[t].raw_exp) { rowExpKey = targetDict[t].raw_exp; break; } }
        let rowHtml = `<tr class="border-b ${getExpColor(rowExpKey, tableColorMap)}"><td class="p-2 border-r-2 border-r-slate-400 whitespace-nowrap font-bold text-slate-700 bg-slate-50">${(tgt===21||tgt===30)?'★ ':''}${tgt}d</td>`;

        tickers.forEach(t => {
          const item = targetDict[t];
          if (item) {
            totalContractsPopulated++;
            const isHighIv = (item.iv_pctile !== undefined && item.iv_pctile >= highIvPctileThreshold);
            if (isHighIv) hasHighIvInTable = true;
            
            const pctileText = (item.iv_pctile !== undefined && item.iv_pctile !== null) ? `(${Math.round(item.iv_pctile)}%)` : '';
            const ivDisplay = isHighIv
              ? `<span class="bg-rose-100 text-rose-800 font-extrabold px-1 py-0.5 rounded border border-rose-300 animate-pulse whitespace-nowrap">🔥 ${item.iv}% ${pctileText}</span>`
              : `<span class="text-slate-600">${item.iv}% <span class="text-[9px] text-slate-400">${pctileText}</span></span>`;

            rowHtml += `<td class="p-1 border-r text-center text-[10px] text-slate-700">${item.exp}</td><td class="p-1.5 border-r whitespace-nowrap ${item.is_safe ? 'bg-green-200 text-green-900 font-bold' : ''}">$${item.strike} <span class="text-[9px]">(${item.pct_diff})</span></td><td class="p-1.5 border-r whitespace-nowrap font-mono text-center">${ivDisplay}</td><td class="p-1.5 border-r whitespace-nowrap font-bold">$${item.prem}</td><td class="p-1.5 border-r-2 border-r-slate-400 whitespace-nowrap text-emerald-700 font-bold">${item.ann}%</td>`;
          } else rowHtml += `<td class="p-1.5 border-r-2 border-r-slate-400 text-center text-slate-400 text-[9px]" colspan="5">No strike match</td>`;
        });
        tbody.innerHTML += rowHtml + `</tr>`;
      });

      if (headerNotice && hasHighIvInTable) {
        headerNotice.innerText = `🔥 High Volatility ${tableType} Alert (\u2265 ${highIvPctileThreshold}%ile)`;
        headerNotice.classList.remove('hidden');
        
        if (alertCriteria.waEnabled && alertCriteria.waNumber) {
          const now = Date.now();
          tickers.forEach(t => {
            const matchingItem = targets
              .map(tgt => (results[tgt] || results[String(tgt)] || {})[t])
              .find(item => item && item.iv_pctile !== undefined && item.iv_pctile >= highIvPctileThreshold);

            if (matchingItem) {
              const cooldownKey = `wa_alert_${t}_${tableType}`;
              const lastAlertTime = parseInt(localStorage.getItem(cooldownKey) || '0', 10);
              
              if (now - lastAlertTime > 3600000) { 
                localStorage.setItem(cooldownKey, now.toString());
                const cleanExp = matchingItem.exp.replace(/<[^>]+>/g, ' ');
                
                fetch('/api/whatsapp', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({
                    to_number: alertCriteria.waNumber, 
                    message: `🔥 Options Alert: ${t} is in a High Volatility regime (${Math.round(matchingItem.iv_pctile)}%ile). Check ${tableType} for ${cleanExp} strike $${matchingItem.strike}.`
                  })
                }).catch(e => console.error("Alert API Error:", e));
              }
            }
          });
        }
      }

      if (totalContractsPopulated === 0) tbody.innerHTML += `<tr><td colspan="${1 + tickers.length * 5}" class="p-2.5 bg-amber-50 text-amber-800 text-center text-[10px]">No ${tableType} contracts matched the target delta.</td></tr>`;
    }

    (async () => {
      const savedTickers = localStorage.getItem('savedTickers');
      if (savedTickers) document.getElementById('tickers').value = savedTickers;
      const savedDelta = localStorage.getItem('savedDelta');
      if (savedDelta) document.getElementById('delta').value = savedDelta;
      const savedProvider = localStorage.getItem('savedProvider');
      if (savedProvider) document.getElementById('providerSelect').value = savedProvider;

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
    save_positions_to_github(positions)
    return {"status": "success"}

@app.put("/api/positions/{pos_id}")
def update_position(pos_id: int, updated: PositionModel):
    positions, _ = get_positions_from_github()
    for i, p in enumerate(positions):
        if p.get("id") == pos_id:
            positions[i] = updated.to_dict()
            break
    save_positions_to_github(positions)
    return {"status": "success"}

@app.delete("/api/positions/{pos_id}")
def remove_position(pos_id: int):
    positions, _ = get_positions_from_github()
    positions = [p for p in positions if p.get("id") != pos_id]
    save_positions_to_github(positions)
    return {"status": "success"}

# --- Twilio Alert Endpoint (SMS & WhatsApp Ready) ---
@app.post("/api/whatsapp")
def send_alert(payload: AlertPayload):
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    template_sid = os.environ.get("TWILIO_TEMPLATE_SID", "").strip()

    if not account_sid or not auth_token:
        print(f"⚠️ MOCK ALERT (Twilio credentials not set): {payload.message}")
        return {"status": "mock", "message": "Twilio credentials missing"}

    to_num = payload.to_number.strip()
    is_whatsapp = to_num.lower().startswith("whatsapp:")

    if is_whatsapp:
        wa_from = os.environ.get("TWILIO_WHATSAPP_FROM", "").strip()
        if not wa_from:
            err = "TWILIO_WHATSAPP_FROM is not set. Set it to your WhatsApp sender, e.g. whatsapp:+14155238886."
            print(f"❌ {err}")
            return {"status": "error", "detail": err}
        if not wa_from.lower().startswith("whatsapp:"):
            wa_from = f"whatsapp:{wa_from}"
        from_number = wa_from
        formatted_to = to_num
    else:
        sms_from = os.environ.get("TWILIO_SMS_FROM", os.environ.get("TWILIO_FROM_NUMBER", "")).strip()
        if not sms_from:
            err = "TWILIO_SMS_FROM / TWILIO_FROM_NUMBER is not set."
            print(f"❌ {err}")
            return {"status": "error", "detail": err}
        from_number = sms_from.replace("whatsapp:", "")
        formatted_to = to_num.replace("whatsapp:", "")

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    
    data = {
        "From": from_number,
        "To": formatted_to
    }

    if is_whatsapp and template_sid:
        data["ContentSid"] = template_sid
        data["ContentVariables"] = json.dumps({"1": payload.message})
    else:
        data["Body"] = payload.message

    try:
        response = requests.post(url, data=data, auth=(account_sid, auth_token), timeout=10)
        resp_json = response.json()
        
        if response.status_code in [200, 201]:
            channel = "WhatsApp" if is_whatsapp else "SMS"
            print(f"✅ Alert dispatched via {channel}! SID: {resp_json.get('sid')}")
            return {"status": "success", "sid": resp_json.get("sid")}
        else:
            error_msg = resp_json.get("message", "Unknown Twilio API Error")
            print(f"❌ Twilio Error ({response.status_code}): {error_msg}")
            return {"status": "error", "detail": error_msg}
    except Exception as e:
        error_msg = str(e)
        print(f"❌ Request Error: {error_msg}")
        return {"status": "error", "detail": error_msg}

# --- marketdata.app provider (Trader plan: real-time options, hosted REST) ---
def md_request(path, params=None):
    """GET a marketdata.app endpoint. Returns decoded JSON; raises on any error."""
    url = f"{MD_BASE}{path}"
    headers = {}
    if MARKETDATA_API_TOKEN and MARKETDATA_API_TOKEN.strip():
        headers["Authorization"] = f"Bearer {MARKETDATA_API_TOKEN.strip()}"
    r = requests.get(url, headers=headers, params=params or {}, timeout=MD_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if data.get("s") != "ok":
        raise RuntimeError(f"marketdata.app status={data.get('s')}")
    return data

def md_expirations(ticker):
    """Return ['YYYY-MM-DD', ...] expirations for ticker."""
    data = md_request(f"/options/expirations/{ticker}/")
    exps = []
    for e in data.get("expirations", []) or []:
        if isinstance(e, str) and "-" in e:
            exps.append(e.strip())
        else:
            try:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo("America/New_York")
                exps.append(datetime.datetime.fromtimestamp(int(e), tz=tz).strftime("%Y-%m-%d"))
            except Exception:
                try:
                    exps.append(datetime.datetime.fromtimestamp(int(e), tz=datetime.timezone.utc).strftime("%Y-%m-%d"))
                except Exception:
                    pass
                
    if not exps:
        raise ValueError("No valid expirations parsed from marketdata.")
    return exps

def md_spot(ticker):
    """Return last spot price for ticker, or 0.0."""
    data = md_request(f"/stocks/quotes/{ticker}/")
    last = (data.get("last") or [0])[0]
    try: return float(last)
    except Exception: return 0.0

def _md_chain_df(data):
    """Convert marketdata.app columnar chain JSON into a yfinance-shaped DataFrame."""
    cols = {k: v for k, v in data.items() if isinstance(v, list)}
    df = pd.DataFrame(cols)
    if df.empty: return df
    if "iv" in df.columns and "impliedVolatility" not in df.columns:
        df["impliedVolatility"] = df["iv"]
    if "last" in df.columns and "lastPrice" not in df.columns:
        df["lastPrice"] = df["last"]
    return df

def md_chain(ticker, expiration):
    """Return SimpleNamespace(calls=DataFrame, puts=DataFrame), like yf option_chain()."""
    base = {"expiration": expiration, "mode": MD_MODE}
    
    try:
        data = md_request(f"/options/chain/{ticker}/", base)
        df = _md_chain_df(data)
    except Exception:
        df = pd.DataFrame()
        
    if df.empty:
        raise ValueError(f"No chain data returned by marketdata for {ticker} at {expiration}")
        
    if "side" in df.columns:
        calls = df[df["side"] == "call"].reset_index(drop=True)
        puts = df[df["side"] == "put"].reset_index(drop=True)
    else:
        calls = df
        puts = df
        
    return SimpleNamespace(calls=calls, puts=puts)

def _native_delta(row, bs_delta):
    """Prefer the provider's native delta when present; else the Black-Scholes value."""
    try:
        d = row.get("delta")
        if d is not None:
            d_float = float(d)
            if not math.isnan(d_float):
                return d_float
    except Exception:
        pass
    return bs_delta

@app.get("/api/data")
def get_options_data(tickers: str = "IREN,RKLB", contract_tickers: str = "", delta: float = 0.2, provider: str = "marketdata"):
    positions, _ = get_positions_from_github()
    cache_store = load_cached_data()
    
    primary_tickers = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    combined_ticker_set = set(primary_tickers)
    for t in contract_tickers.split(","):
        if t.strip(): combined_ticker_set.add(t.strip().upper())
    for p in positions:
        if p.get("ticker"): combined_ticker_set.add(p["ticker"].strip().upper())

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

        tkr = yf.Ticker(ticker)
        
        # User dropdown overrides the default behavior
        use_md = (provider == "marketdata")
        
        # --- ISOLATED SPOT FETCH ---
        if use_md:
            try:
                spot_price = md_spot(ticker)
            except Exception as e:
                use_md = False
                if is_primary:
                    diagnostics[ticker] = f"MarketData API Spot Check Failed: {str(e)[:100]}"

        if spot_price is None or math.isnan(spot_price) or spot_price <= 0:
            try:
                if hasattr(tkr, 'fast_info'):
                    sp = tkr.fast_info.get('last_price') or tkr.fast_info.get('lastPrice')
                    if sp is not None:
                        spot_price = float(sp)
            except Exception: pass
            
            if spot_price is None or math.isnan(spot_price) or spot_price <= 0:
                try:
                    hist_1d = tkr.history(period="5d")
                    if not hist_1d.empty: spot_price = float(hist_1d['Close'].iloc[-1])
                except Exception: pass

        # --- ISOLATED EXPIRATIONS FETCH ---
        if use_md:
            try:
                expirations = md_expirations(ticker)
            except Exception as e:
                use_md = False
                if is_primary: diagnostics[ticker] = f"MarketData Expirations Failed: {str(e)[:100]}"
                
        if not expirations:
            try:
                expirations = list(tkr.options) if tkr.options else []
            except Exception as e:
                if is_primary: diagnostics[ticker] = f"Yahoo Finance Options Failed: {str(e)[:100]}"

        # --- ISOLATED HISTORICAL VOLATILITY FETCH ---
        try:
            df_hist_1y = tkr.history(period="1y")
            if df_hist_1y is not None and not df_hist_1y.empty and 'Close' in df_hist_1y.columns:
                df_hist = df_hist_1y.tail(30)
                df_clean = df_hist_1y['Close'].dropna()
                df_clean = df_clean[df_clean > 0]
                returns = np.log(df_clean / df_clean.shift(1)).dropna()
                if len(returns) >= 20:
                    rolling_vol = returns.rolling(window=20).std() * math.sqrt(252)
                    hist_vols = [float(v) for v in rolling_vol.dropna().tolist() if not math.isnan(v) and v > 0]
            else:
                df_hist = tkr.history(period="30d")
        except Exception:
            pass

        if spot_price is not None and not math.isnan(spot_price) and spot_price > 0: 
            successful_fetches += 1
        else:
            if is_primary and ticker not in all_spots: diagnostics[ticker] = "No spot price available from any provider."
            continue

        all_spots[ticker] = round(spot_price, 2)
        if not expirations and is_primary: continue

        ticker_hv_pctile = None
        if len(hist_vols) > 0:
            current_hv = hist_vols[-1]
            count_below = sum(1 for v in hist_vols if v < current_hv)
            ticker_hv_pctile = round((count_below / len(hist_vols)) * 100.0, 1)

        if df_hist is not None and not df_hist.empty and len(df_hist) >= 2:
            try:
                prev_high, prev_low, prev_close = float(df_hist['High'].iloc[-2]), float(df_hist['Low'].iloc[-2]), float(df_hist['Close'].iloc[-2])
                if math.isnan(prev_high) or math.isnan(prev_low) or math.isnan(prev_close):
                    s1, r1, rolling_support, rolling_resistance = spot_price * 0.95, spot_price * 1.05, spot_price * 0.90, spot_price * 1.10
                else:
                    pivot = (prev_high + prev_low + prev_close) / 3.0
                    s1, r1 = (2 * pivot) - prev_high, (2 * pivot) - prev_low
                    rolling_support, rolling_resistance = float(df_hist['Low'].min()), float(df_hist['High'].max())
                    if math.isnan(rolling_support): rolling_support = spot_price * 0.90
                    if math.isnan(rolling_resistance): rolling_resistance = spot_price * 1.10
            except Exception:
                s1, r1, rolling_support, rolling_resistance = spot_price * 0.95, spot_price * 1.05, spot_price * 0.90, spot_price * 1.10
        else:
            s1, r1, rolling_support, rolling_resistance = spot_price * 0.95, spot_price * 1.05, spot_price * 0.90, spot_price * 1.10

        if is_primary:
            market_data[ticker] = {
                "spot": round(spot_price, 2), "s1": round(s1, 2), "r1": round(r1, 2),
                "floor": round(rolling_support, 2), "ceiling": round(rolling_resistance, 2),
                "support": round(min(s1, rolling_support), 2), "resistance": round(max(r1, rolling_resistance), 2)
            }

        target_to_exp = {}
        if is_primary:
            for target in target_periods:
                closest_exp, min_diff = None, float("inf")
                for exp in expirations:
                    try: exp_date = datetime.datetime.strptime(exp, "%Y-%m-%d").date()
                    except Exception: continue
                    if exp_date <= today: continue
                    diff = abs((exp_date - today).days - target)
                    if diff < min_diff: min_diff = diff; closest_exp = exp
                if closest_exp: target_to_exp[target] = closest_exp

        needed_exps = set(target_to_exp.values())
        for p in positions:
            if p.get("ticker", "").strip().upper() == ticker and p.get("exp") in expirations:
                needed_exps.add(p["exp"])

        loaded_chains = {}
        for exp in needed_exps:
            try:
                if use_md:
                    try:
                        loaded_chains[exp] = md_chain(ticker, exp)
                        if is_primary and ticker in diagnostics:
                            del diagnostics[ticker]
                        continue
                    except Exception as e:
                        if is_primary: diagnostics[ticker] = f"MarketData API blocked/failed. Add API Token."
                
                loaded_chains[exp] = tkr.option_chain(exp)
                if is_primary and ticker in diagnostics:
                    del diagnostics[ticker]
            except Exception as e: 
                if is_primary: diagnostics[ticker] = "Both MarketData and Yahoo Finance failed to fetch options."
                continue

        for p in positions:
            try:
                p_tkr, p_exp, p_type, k_target = str(p.get("ticker", "")).strip().upper(), str(p.get("exp", "")).strip(), str(p.get("type", "")).strip().upper(), float(p.get("strike", 0))
                if p_tkr == ticker and p_exp in loaded_chains:
                    df_opts = loaded_chains[p_exp].puts if p_type == "PUT" else loaded_chains[p_exp].calls
                    if df_opts is not None and not df_opts.empty and "strike" in df_opts.columns:
                        diffs = (df_opts["strike"] - k_target).abs()
                        if not diffs.empty:
                            min_idx = diffs.idxmin()
                            if pd.notna(min_idx) and diffs.loc[min_idx] <= 0.5:
                                live_positions[f"{p_tkr}_{p_exp}_{k_target:.2f}_{p_type}"] = round(get_safe_mid_price(df_opts.loc[min_idx]), 2)
            except Exception:
                pass

        if is_primary:
            for target, exp in target_to_exp.items():
                if exp not in loaded_chains: continue
                chain = loaded_chains[exp]
                exp_date = datetime.datetime.strptime(exp, "%Y-%m-%d").date()
                actual_b_days = count_business_days(today, exp_date)
                T, r = max(actual_b_days, 1) / 252.0, 0.05
                exp_stacked = f"{exp_date.strftime('%b %d')}<br><span class='text-[9px] text-slate-400 font-mono'>({actual_b_days}d)</span>"

                if chain.puts is not None and not chain.puts.empty:
                    best_put, min_p_diff = None, float("inf")
                    for _, row in chain.puts.iterrows():
                        try:
                            K = float(row['strike'])
                            if math.isnan(K) or K <= 0: continue
                            
                            iv_raw = row.get('impliedVolatility')
                            try:
                                if iv_raw is None:
                                    iv_val = 0.0
                                else:
                                    iv_val = float(iv_raw)
                                    if math.isnan(iv_val): iv_val = 0.0
                            except (ValueError, TypeError):
                                iv_val = 0.0

                            d = _native_delta(row, calc_put_delta(spot_price, K, T, r, sigma=iv_val))
                            if abs(d - (-delta)) < min_p_diff:
                                min_p_diff = abs(d - (-delta)); best_put = (row, K, iv_val)
                        except Exception: continue

                    if best_put:
                        row, k_val, iv_val = best_put
                        prem = get_safe_mid_price(row)
                        yield_pct = (prem / k_val * 100) if k_val > 0 else 0
                        
                        results_puts[str(target)][ticker] = {
                            "raw_exp": exp, "exp": exp_stacked, "strike": round(k_val, 2),
                            "pct_diff": f"{((k_val - spot_price) / spot_price) * 100:+.1f}%",
                            "iv": round(iv_val * 100, 1), 
                            "iv_pctile": ticker_hv_pctile,
                            "prem": round(prem, 2), "ann": round(yield_pct * 252 / actual_b_days, 1),
                            "is_safe": k_val < market_data.get(ticker, {}).get("support", 0)
                        }

                if chain.calls is not None and not chain.calls.empty:
                    best_call, min_c_diff = None, float("inf")
                    for _, row in chain.calls.iterrows():
                        try:
                            K = float(row['strike'])
                            if math.isnan(K) or K <= 0: continue
                            
                            iv_raw = row.get('impliedVolatility')
                            try:
                                if iv_raw is None:
                                    iv_val = 0.0
                                else:
                                    iv_val = float(iv_raw)
                                    if math.isnan(iv_val): iv_val = 0.0
                            except (ValueError, TypeError):
                                iv_val = 0.0

                            d = _native_delta(row, calc_call_delta(spot_price, K, T, r, sigma=iv_val))
                            if abs(d - delta) < min_c_diff:
                                min_c_diff = abs(d - delta); best_call = (row, K, iv_val)
                        except Exception: continue

                    if best_call:
                        row, k_val, iv_val = best_call
                        prem = get_safe_mid_price(row)
                        yield_pct = (prem / spot_price * 100) if spot_price > 0 else 0
                        
                        results_calls[str(target)][ticker] = {
                            "raw_exp": exp, "exp": exp_stacked, "strike": round(k_val, 2),
                            "pct_diff": f"{((k_val - spot_price) / spot_price) * 100:+.1f}%",
                            "iv": round(iv_val * 100, 1), 
                            "iv_pctile": ticker_hv_pctile,
                            "prem": round(prem, 2), "ann": round(yield_pct * 252 / actual_b_days, 1),
                            "is_safe": k_val > market_data.get(ticker, {}).get("resistance", 0)
                        }

    is_cached_payload = (successful_fetches == 0 and bool(cache_store))
    payload = {
        "market": {t: market_data[t] for t in primary_tickers if t in market_data},
        "all_spots": all_spots, "puts": results_puts, "calls": results_calls,
        "tickers": primary_tickers, "targets": target_periods, "live_positions": live_positions,
        "diagnostics": diagnostics, "is_cached": is_cached_payload, "provider": provider,
        "cached_at": cache_store.get("cached_at") if is_cached_payload else datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    if successful_fetches > 0: save_cached_data(payload)
    return payload

@app.get("/", response_class=HTMLResponse)
def render_index(): return HTMLResponse(content=HTML_CONTENT)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
