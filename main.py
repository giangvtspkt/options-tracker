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
  <meta name="viewport" content="width=device-width, initial-scale=1.
