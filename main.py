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
MD_MODE = os.getenv("MD_MODE", "")
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
        <button onclick="if(checkAuth()) toggleAlertSettings()" class="bg-slate-100 hover:bg-slate-200 text-slate-700 text-[10px] font-semibold px-2 py-0.5 rounded border border-slate-200 flex items-center gap-1">
          <span>⚙</span> Settings
        </button>
      </div>
      <button onclick="if(checkAuth()) toggleAddForm()" id="toggleFormBtn" class="bg-slate-800 text-white text-[10px] font-bold px-2.5 py-1 rounded-md">
        + Add Position
      </button>
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

    <!-- Collapsible Settings Panel -->
    <div id="alertSettingsPanel" class="hidden bg-slate-50 border border-slate-200 rounded-lg p-2.5 mb-3">
      <div class="flex items-center justify-between mb-2 border-b border-slate-200 pb-2">
        <span class="font-bold text-[11px] text-slate-700">⏱️ Auto-Refresh Trading Hours (Local Time)</span>
        <button onclick="resetAlertCriteria()" class="text-[10px] bg-rose-50 hover:bg-rose-100 text-rose-700 font-bold px-2 py-0.5 rounded border border-rose-200">
          ↺ Reset
        </button>
      </div>
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-2.5 text-[10px] mb-3">
        <div>
          <label class="font-semibold text-slate-600 block mb-0.5">Market Open Time</label>
          <input id="critRefreshStart" type="time" class="w-full border rounded p-1.5 text-xs bg-white font-mono" onchange="saveAlertCriteria()">
        </div>
        <div>
          <label class="font-semibold text-slate-600 block mb-0.5">Market Close Time</label>
          <input id="critRefreshEnd" type="time" class="w-full border rounded p-1.5 text-xs bg-white font-mono" onchange="saveAlertCriteria()">
        </div>
        <div class="col-span-2 text-slate-500 flex items-center px-1">
          Auto-refresh will only fire if your current clock falls between these two times.
        </div>
      </div>

      <div class="flex items-center justify-between mb-2 border-t border-slate-200 pt-2">
        <span class="font-bold text-[11px] text-slate-700">⚙ Custom Rolling &amp; IV Alerts</span>
      </div>
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-2.5 text-[10px]">
        <div>
          <label class="font-semibold text-slate-600 block mb-0.5">CSP Warn Buffer (%)</label>
          <input id="critPutWarnPct" type="number" step="0.5" class="w-full border rounded p-1.5 text-xs bg-white" onchange="saveAlertCriteria()">
        </div>
        <div>
          <label class="font-semibold text-slate-600 block mb-0.5">CSP Warn DTE (&le; days)</label>
          <input id="critPutWarnDte" type="number" step="1" class="w-full border rounded p-1.5 text-xs bg-white" onchange="saveAlertCriteria()">
        </div>
        <div>
          <label class="font-semibold text-slate-600 block mb-0.5">CSP Critical DTE (&le; days)</label>
          <input id="critPutCritDte" type="number" step="1" class="w-full border rounded p-1.5 text-xs bg-white" onchange="saveAlertCriteria()">
        </div>
        <div>
          <label class="font-semibold text-slate-600 block mb-0.5">CC Tested Buffer (%)</label>
          <input id="critCallWarnPct" type="number" step="0.5" class="w-full border rounded p-1.5 text-xs bg-white" onchange="saveAlertCriteria()">
        </div>
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-3 gap-2.5 text-[10px] mt-2.5">
        <div class="bg-amber-50/70 p-1.5 rounded border border-amber-200">
          <label class="font-bold text-amber-900 block mb-0.5">🔥 Put High Vol (%ile)</label>
          <input id="critPutHighIvPctile" type="number" step="5" class="w-full border rounded p-1.5 text-xs bg-white font-bold" onchange="saveAlertCriteria()">
        </div>
        <div class="bg-amber-50/70 p-1.5 rounded border border-amber-200">
          <label class="font-bold text-amber-900 block mb-0.5">🔥 Call High Vol (%ile)</label>
          <input id="critCallHighIvPctile" type="number" step="5" class="w-full border rounded p-1.5 text-xs bg-white font-bold" onchange="saveAlertCriteria()">
        </div>
        <div class="bg-amber-50/70 p-1.5 rounded border border-amber-200">
          <label class="font-bold text-amber-900 block mb-0.5">⏱️ IV History (Trading Days)</label>
          <input id="critIvHistoryDays" type="number" step="1" class="w-full border rounded p-1.5 text-xs bg-white font-bold" onchange="saveAlertCriteria()">
        </div>
      </div>
      
      <!-- Alert Recipient Settings -->
      <div class="mt-2.5 pt-2 border-t border-slate-200 flex flex-col sm:flex-row sm:items-center gap-2">
        <label class="font-bold text-emerald-800 flex items-center gap-1.5 whitespace-nowrap bg-emerald-50 px-2 py-1 rounded border border-emerald-200">
          <input type="checkbox" id="critWaEnabled" onchange="saveAlertCriteria()">
          <span>Enable Twilio Alerts</span>
        </label>
        <input id="critWaNumber" type="text" placeholder="Format: +1234567890 (SMS) or whatsapp:+1234567890" class="w-full sm:w-80 border rounded p-1.5 text-xs bg-white font-mono" onchange="saveAlertCriteria()" onblur="saveAlertCriteria()">
        <span class="text-[9px] text-slate-400">1-hour cooldown per ticker to prevent spam.</span>
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

    <!-- Ticker Performance (P/L) & Capital Summary -->
    <div class="bg-slate-50 border border-slate-200 rounded-lg p-2.5 mb-3">
      <div class="flex flex-wrap items-center justify-between gap-2 mb-2">
        <div class="flex items-center gap-2">
          <span class="text-[11px] font-bold text-slate-700">📊 Ticker Performance (P/L) &amp; Capital Requirement</span>
          <span id="totalOpenQty" class="text-[10px] font-semibold text-slate-500 bg-slate-200/70 px-1.5 py-0.5 rounded">Total Open: 0</span>
        </div>
        <div class="flex flex-wrap items-center gap-1.5 text-[10px] font-mono">
          <span id="moomooCspCapital" class="text-orange-800 bg-orange-100 border border-orange-200 px-2 py-0.5 rounded font-bold">Moo: $0</span>
          <span id="ibkrCspCapital" class="text-blue-800 bg-blue-100 border border-blue-200 px-2 py-0.5 rounded font-bold">IB: $0</span>
          <span id="totalCspCapital" class="text-sky-800 bg-sky-100 border border-sky-300 px-2 py-0.5 rounded font-bold">Total CSP: $0</span>
          <span id="totalCcCapital" class="text-purple-800 bg-purple-100 border border-purple-300 px-2 py-0.5 rounded font-bold">Total CC Stock: $0</span>
          <span id="avgAnnPctBadge" class="text-emerald-900 bg-emerald-100 border border-emerald-300 px-2 py-0.5 rounded font-extrabold">Avg Ann: 0.0%</span>
          <span id="estAnnualGainBadge" class="text-emerald-950 bg-emerald-200 border border-emerald-400 px-2 py-0.5 rounded font-extrabold">Est. Gain: +$0/yr</span>
        </div>
      </div>
      <div id="openSummaryCards" class="flex flex-wrap gap-2"></div>
    </div>

    <!-- Contract Positions Table -->
    <div class="overflow-x-auto border border-slate-200 rounded-lg relative">
      <table class="w-full text-left text-[10px]">
        <thead class="text-slate-600 font-bold">
          <tr>
            <th class="p-0 border-r align-middle sticky left-0 z-20 bg-slate-200 shadow-[1px_0_0_0_#cbd5e1] min-w-[140px]">
               <div class="flex flex-col">
                   <select id="posBrokerFilter" onchange="renderPositionsAndPL()" class="w-full bg-transparent font-bold text-slate-700 outline-none cursor-pointer px-1 py-1 text-center border-b border-slate-300 text-[10px]">
                     <option value="ALL">Broker (All)</option>
                   </select>
                   <div class="flex items-center">
                       <select id="posTickerFilter" onchange="renderPositionsAndPL()" class="w-1/2 bg-transparent font-bold text-slate-700 outline-none cursor-pointer px-1 py-1 border-r border-slate-300 text-[10px]">
                         <option value="ALL">Ticker (All)</option>
                       </select>
                       <select id="posTypeFilter" onchange="renderPositionsAndPL()" class="w-1/2 bg-transparent font-bold text-slate-700 outline-none cursor-pointer px-1 py-1 text-[10px]">
                         <option value="ALL">Type (All)</option>
                         <option value="PUT">PUT</option>
                         <option value="CALL">CALL</option>
                       </select>
                   </div>
               </div>
            </th>
            <th class="p-1.5 border-r text-center align-middle bg-slate-100">Trade Day</th>
            <th class="p-1.5 border-r text-center align-middle bg-slate-100">Exp</th>
            <th class="p-1.5 border-r align-middle bg-slate-100">Entry</th>
            <th id="thAnnPct" class="p-1.5 border-r font-extrabold text-emerald-900 bg-emerald-50 text-center align-middle">Ann %</th>
            <th class="p-1.5 border-r align-middle bg-slate-100">Live Mark</th>
            <th class="p-1.5 border-r font-extrabold text-blue-900 bg-blue-50 align-middle">Cur P/L ($)</th>
            <th class="p-1.5 border-r align-middle bg-slate-100">Max P/L ($)</th>
            <th class="p-1.5 border-r align-middle bg-slate-100">Status</th>
            <th class="p-1.5 text-center align-middle bg-slate-100">Del</th>
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
    <div class="flex items-center justify-between mb-3 pb-3 border-b border-slate-100">
      <span class="text-xs font-bold text-slate-700 uppercase tracking-wider flex items-center gap-1.5">
        <span>⚙️</span> Dashboard Controls
      </span>
      <div id="fearGreedBadge" class="hidden items-center gap-2 px-3 py-1.5 rounded-lg text-xs sm:text-sm font-extrabold border-2 shadow-md transition-all">
        <span id="fgIcon" class="text-base sm:text-lg"></span><span id="fgText" class="tracking-wide"></span>
      </div>
    </div>
    <div class="grid grid-cols-2 sm:grid-cols-3 gap-2 mb-2">
      <div>
        <label class="text-[11px] font-bold text-slate-500 uppercase tracking-wide">Tickers</label>
        <input id="tickers" type="text" value="IREN, RKLB, AMD" class="w-full border rounded p-2 text-sm uppercase font-semibold">
      </div>
      <div>
        <label class="text-[11px] font-bold text-slate-500 uppercase tracking-wide">Delta</label>
        <input id="delta" type="number" step="0.01" value="0.2" class="w-full border rounded p-2 text-sm font-semibold">
      </div>
      <div class="col-span-2 sm:col-span-1">
        <label class="text-[11px] font-bold text-slate-500 uppercase tracking-wide">Data Source</label>
        <select id="providerSelect" class="w-full border rounded p-2 text-sm font-semibold bg-white text-slate-700">
          <option value="yfinance">Yahoo Finance (Free)</option>
          <option value="marketdata">MarketData.app (Fast)</option>
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

    // Default password for editing features. Change this to whatever you want.
    const APP_PASSWORD = "admin"; 

    function checkAuth() {
        if (sessionStorage.getItem('app_unlocked') === 'true') return true;
        const pwd = prompt("Enter password to access edit features:");
        if (pwd === APP_PASSWORD) {
            sessionStorage.setItem('app_unlocked', 'true');
            return true;
        } else if (pwd !== null) {
            alert("Incorrect password.");
        }
        return false;
    }

    const DEFAULT_ALERT_CRITERIA = {
      putWarnPct: 3.0, putWarnDte: 10, putCritDte: 5,
      callWarnPct: 2.0, putHighIvPctile: 85.0, callHighIvPctile: 80.0,
      ivHistoryDays: 40,
      waEnabled: false, waNumber: "",
      refreshStart: "20:30", refreshEnd: "03:00"
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
      document.getElementById('critIvHistoryDays').value = alertCriteria.ivHistoryDays;
      document.getElementById('critWaEnabled').checked = alertCriteria.waEnabled;
      document.getElementById('critWaNumber').value = alertCriteria.waNumber;
      
      document.getElementById('critRefreshStart').value = alertCriteria.refreshStart || "20:30";
      document.getElementById('critRefreshEnd').value = alertCriteria.refreshEnd || "03:00";
    }

    function saveAlertCriteria() {
      alertCriteria.putWarnPct = parseFloat(document.getElementById('critPutWarnPct').value) || 3.0;
      alertCriteria.putWarnDte = parseInt(document.getElementById('critPutWarnDte').value) || 10;
      alertCriteria.putCritDte = parseInt(document.getElementById('critPutCritDte').value) || 5;
      alertCriteria.callWarnPct = parseFloat(document.getElementById('critCallWarnPct').value) || 2.0;
      alertCriteria.putHighIvPctile = parseFloat(document.getElementById('critPutHighIvPctile').value) || 85.0;
      alertCriteria.callHighIvPctile = parseFloat(document.getElementById('critCallHighIvPctile').value) || 80.0;
      alertCriteria.ivHistoryDays = parseInt(document.getElementById('critIvHistoryDays').value) || 40;
      alertCriteria.waEnabled = document.getElementById('critWaEnabled').checked;
      alertCriteria.waNumber = document.getElementById('critWaNumber').value.trim();
      
      alertCriteria.refreshStart = document.getElementById('critRefreshStart').value || "20:30";
      alertCriteria.refreshEnd = document.getElementById('critRefreshEnd').value || "03:00";
      
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

    // Removed the transparency modifiers (/80) so sticky rows block content underneath perfectly
    const EXP_COLOR_PALETTE = ['bg-indigo-50', 'bg-amber-50', 'bg-emerald-50', 'bg-purple-50', 'bg-rose-50', 'bg-sky-50', 'bg-teal-50'];

    function toggleHideExpired(checked) {
      hideExpired = checked;
      localStorage.setItem('hideExpiredContracts', checked);
      renderPositionsAndPL();
    }

    function getExpColor(expKey, map) {
      if (!expKey) return 'bg-white';
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
    
    function getBusinessDaysCount(startStr, endStr) {
        let s = new Date(startStr + 'T00:00:00');
        let e = new Date(endStr + 'T00:00:00');
        if (isNaN(s) || isNaN(e) || s >= e) return 1;
        let count = 0;
        let cur = new Date(s);
        cur.setDate(cur.getDate() + 1);
        while (cur <= e) {
            let dow = cur.getDay();
            if (dow !== 0 && dow !== 6) count++;
            cur.setDate(cur.getDate() + 1);
        }
        return Math.max(count, 1);
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
      
      // HIDE IMMEDIATELY to prevent double clicking while fetching
      document.getElementById('positionForm').classList.add('hidden');

      const res = await fetch('/api/positions', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(p) });
      if (res.ok) { 
        await loadCloudPositions(); 
        fetchData(false); 
      } else {
        // Fallback in case of server error so it can be corrected
        document.getElementById('positionForm').classList.remove('hidden');
        alert("Failed to save position.");
      }
    }

    async function deletePosition(id) {
      if (!checkAuth()) return;
      if (!confirm("Delete this position?")) return;
      if ((await fetch(`/api/positions/${id}`, { method: 'DELETE' })).ok) loadCloudPositions();
    }

    function renderPositionsAndPL() {
      document.getElementById('hideExpiredToggle').checked = hideExpired;
      
      // Setup Broker Filter Dropdown
      const brokerFilterSelect = document.getElementById('posBrokerFilter');
      const currentBrokerFilter = brokerFilterSelect ? brokerFilterSelect.value : 'ALL';
      const uniqueBrokers = Array.from(new Set(cloudPositions.map(p => (p.broker || 'moomoo').toUpperCase()))).sort();
      if (brokerFilterSelect) {
        brokerFilterSelect.innerHTML = `<option value="ALL">Broker (All)</option>` + uniqueBrokers.map(b => `<option value="${b}">${b}</option>`).join('');
        brokerFilterSelect.value = uniqueBrokers.includes(currentBrokerFilter) ? currentBrokerFilter : 'ALL';
      }
      const activeBrokerFilter = brokerFilterSelect ? brokerFilterSelect.value : 'ALL';

      // Setup Ticker Filter Dropdown
      const filterSelect = document.getElementById('posTickerFilter');
      const currentFilter = filterSelect ? filterSelect.value : 'ALL';
      const uniqueTickers = Array.from(new Set(cloudPositions.map(p => p.ticker))).sort();
      if (filterSelect) {
        filterSelect.innerHTML = `<option value="ALL">Ticker (All)</option>` + uniqueTickers.map(t => `<option value="${t}">${t}</option>`).join('');
        filterSelect.value = uniqueTickers.includes(currentFilter) ? currentFilter : 'ALL';
      }
      const activeFilter = filterSelect ? filterSelect.value : 'ALL';

      // Setup Type Filter Dropdown
      const typeFilterSelect = document.getElementById('posTypeFilter');
      const activeTypeFilter = typeFilterSelect ? typeFilterSelect.value : 'ALL';
      
      const tbody = document.getElementById('positionsBody');
      const now = new Date();
      const currentYear = now.getFullYear();
      const currentMonth = now.getMonth();
      const todayStr = now.toISOString().split('T')[0];
      let lastMonthYear = currentYear, lastMonth = currentMonth - 1;
      if (lastMonth < 0) { lastMonth = 11; lastMonthYear--; }

      let totalRealized = 0, thisMonthRealized = 0, lastMonthRealized = 0;
      let totalMaxUnrealized = 0, totalCurrentUnrealized = 0, thisMonthCurrentUnrealized = 0;
      let totalOpenCount = 0, totalCspCapital = 0, moomooCspCapital = 0, ibkrCspCapital = 0;
      let totalCcStockCapital = 0, totalActiveCapital = 0, totalAnnualDollarGain = 0;

      // Accumulator for per-ticker performance metrics
      const tickerStats = {};
      const initTicker = (tkr) => {
        if (!tickerStats[tkr]) {
          tickerStats[tkr] = {
            total: 0,
            puts: 0,
            calls: 0,
            cspCapital: 0,
            ccCapital: 0,
            activeCapital: 0,
            annualDollarGain: 0,
            moomooCsp: 0,
            ibkrCsp: 0,
            realizedPl: 0,
            unrealizedPl: 0,
            maxUnrealizedPl: 0
          };
        }
      };

      cloudPositions.forEach(p => {
        const tkr = (p.ticker || '').trim().toUpperCase();
        if (!tkr) return;
        initTicker(tkr);

        const parts = (p.exp || '').split('-');
        const isExpired = p.exp < todayStr;
        const isThisMonth = (parseInt(parts[0], 10) === currentYear && parseInt(parts[1], 10) - 1 === currentMonth);
        const isLastMonth = (parseInt(parts[0], 10) === lastMonthYear && parseInt(parts[1], 10) - 1 === lastMonth);

        const spot = (globalData && globalData.all_spots && globalData.all_spots[tkr] !== undefined) ? globalData.all_spots[tkr] : null;
        const liveMark = (globalData && globalData.live_positions && globalData.live_positions[`${tkr}_${(p.exp || '').trim()}_${parseFloat(p.strike).toFixed(2)}_${(p.type || '').trim().toUpperCase()}`]) || null;

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
          tickerStats[tkr].realizedPl += closedPl;
          if (isThisMonth) thisMonthRealized += closedPl;
          if (isLastMonth) lastMonthRealized += closedPl;
        } else {
          totalOpenCount += p.qty;
          const broker = (p.broker || 'moomoo').toLowerCase();
          
          tickerStats[tkr].total += p.qty;
          
          const capitalBasis = (p.type === 'CALL' && spot !== null && spot > 0) ? spot : p.strike;
          const contractCapital = capitalBasis * 100 * p.qty;

          if (p.action === 'SELL' && p.strike > 0 && p.prem > 0) {
            let bDays = getBusinessDaysCount(p.trade_date || todayStr, p.exp);
            let annDollarGain = (p.prem * 100 * p.qty) * (252 / bDays
