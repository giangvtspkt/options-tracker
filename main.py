import os
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from twilio.rest import Client

# 1. Initialize FastAPI app (Must be named 'app' for Render/Uvicorn)
app = FastAPI(title="Options Tracker API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. WhatsApp API Request Schema
class WhatsAppPayload(BaseModel):
    to: str
    message: str

# 3. Bug-Free WhatsApp Endpoint
@app.post("/api/whatsapp")
def api_send_whatsapp(payload: WhatsAppPayload):
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_number = os.environ.get("TWILIO_FROM_NUMBER", "whatsapp:+14155238886")

    if not account_sid or not auth_token:
        print(f"⚠️ MOCK ALERT (Twilio credentials not set): {payload.message}")
        return {"status": "mock", "message": "Twilio credentials missing"}

    # Ensure recipient number starts with 'whatsapp:'
    formatted_to = payload.to if payload.to.startswith("whatsapp:") else f"whatsapp:{payload.to}"

    try:
        client = Client(account_sid, auth_token)
        
        # BUG FIX: Reverted to 'body' instead of a hardcoded 'content_sid'.
        # Since you joined the Sandbox, you have an active 24-hour session 
        # and Twilio will natively allow this free-form text to pass.
        message = client.messages.create(
            from_=from_number,
            body=payload.message,
            to=formatted_to
        )
        print(f"✅ WhatsApp alert dispatched! SID: {message.sid}")
        return {"status": "success", "sid": message.sid}
    except Exception as e:
        print(f"❌ Twilio API Error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

# ========================================================
# ⚠️ IMPORTANT: PASTE YOUR YFINANCE / OPTIONS ROUTES HERE
# (Do not delete your backend logic if you had any)
# ========================================================

# 4. Root Route: Fixes the 404 Error by serving your frontend HTML
@app.get("/", response_class=HTMLResponse)
def read_root():
    # Looks for your frontend file in common directories
    possible_paths = [
        Path("index.html"),
        Path("static/index.html"),
        Path("templates/index.html"),
        Path("dist/index.html")
    ]
    for p in possible_paths:
        if p.exists():
            return FileResponse(p)
    
    # Fallback UI if index.html is completely missing
    return """
    <!DOCTYPE html>
    <html>
      <head><title>Options Tracker</title></head>
      <body style="background:#0f172a; color:#fff; font-family:sans-serif; text-align:center; padding-top:50px;">
        <h1 style="color:#38bdf8;">Options Tracker Backend</h1>
        <p style="color:#94a3b8;">Status: Online and Healthy</p>
      </body>
    </html>
    """
