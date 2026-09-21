import os
import json
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from twilio.rest import Client

# 1. Initialize FastAPI app (Render looks specifically for 'app')
app = FastAPI(title="Options Tracker API")

# Enable CORS so your frontend can call backend APIs seamlessly
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. Twilio WhatsApp Alert Function
def send_whatsapp_alert(to_number: str, alert_message: str) -> dict:
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_number = os.environ.get("TWILIO_FROM_NUMBER", "whatsapp:+14155238886")

    if not account_sid or not auth_token:
        print(f"⚠️ MOCK ALERT (Twilio credentials not set in Render): {alert_message}")
        return {"status": "mock", "message": "Twilio credentials missing; logged to console."}

    # Ensure recipient number starts with 'whatsapp:'
    formatted_to = to_number if to_number.startswith("whatsapp:") else f"whatsapp:{to_number}"

    try:
        client = Client(account_sid, auth_token)

        # Twilio's default sandbox template SID with dynamic variable mapping
        message = client.messages.create(
            from_=from_number,
            content_sid="HX2335606caa639f1507e0c4fdecf10427",
            content_variables=json.dumps({"1": alert_message}),
            to=formatted_to
        )
        print(f"✅ WhatsApp alert dispatched! SID: {message.sid}")
        return {"status": "success", "sid": message.sid}
    except Exception as e:
        print(f"❌ Twilio API Error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))


# 3. API Request Schema
class WhatsAppPayload(BaseModel):
    to: str
    message: str


# 4. WhatsApp Endpoint
@app.post("/api/whatsapp")
def api_send_whatsapp(payload: WhatsAppPayload):
    return send_whatsapp_alert(to_number=payload.to, alert_message=payload.message)


# 5. Health Check Endpoint
@app.get("/api/health")
def health_check():
    return {"status": "ok", "app": "Options Tracker is running smoothly"}


# 6. Root Route: Serves index.html to fix the 404 error
@app.get("/", response_class=HTMLResponse)
def read_root():
    # Check common locations where index.html might live
    possible_paths = [
        Path("index.html"),
        Path("static/index.html"),
        Path("templates/index.html"),
        Path("dist/index.html")
    ]

    for p in possible_paths:
        if p.exists():
            return FileResponse(str(p))

    # Fallback dashboard if index.html is in another folder
    return """
    <!DOCTYPE html>
    <html>
      <head>
        <title>Options Tracker</title>
        <style>
          body { font-family: system-ui, -apple-system, sans-serif; background: #0f172a; color: #f8fafc; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; margin: 0; }
          .card { background: #1e293b; padding: 2.5rem; border-radius: 12px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); text-align: center; max-width: 500px; border: 1px solid #334155; }
          h1 { color: #38bdf8; margin-bottom: 0.5rem; font-size: 1.75rem; }
          p { color: #94a3b8; font-size: 0.95rem; line-height: 1.5; }
          .badge { display: inline-block; background: #059669; color: #fff; padding: 0.35rem 0.85rem; border-radius: 9999px; font-weight: 600; font-size: 0.85rem; margin-top: 1rem; }
        </style>
      </head>
      <body>
        <div class="card">
          <h1>Options Tracker Service</h1>
          <p>The FastAPI backend and WhatsApp alert worker are online and healthy.</p>
          <div class="badge">System Online</div>
        </div>
      </body>
    </html>
    """
