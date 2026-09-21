import os
import json
from twilio.rest import Client

def send_whatsapp_alert(to_number, alert_message):
    """
    Sends a WhatsApp notification using Twilio's Sandbox template format 
    to comply with trial account content restrictions.
    """
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    
    # TWILIO_FROM_NUMBER must remain the shared Twilio Sandbox number: whatsapp:+14155238886
    from_number = os.environ.get("TWILIO_FROM_NUMBER", "whatsapp:+14155238886")
    
    if not account_sid or not auth_token:
        print("⚠️ MOCK WHATSAPP ALERT (Missing Twilio Credentials):", alert_message)
        return False

    try:
        client = Client(account_sid, auth_token)
        
        # Ensure the destination number has 'whatsapp:' prefixed correctly
        formatted_to = to_number if to_number.startswith("whatsapp:") else f"whatsapp:{to_number}"
        
        # Using Twilio's standard built-in sandbox template 
        # which maps your alert message into variable slot {"1": "..."}
        message = client.messages.create(
            from_=from_number,
            content_sid="HX2335606caa639f1507e0c4fdecf10427",
            content_variables=json.dumps({"1": alert_message}),
            to=formatted_to
        )
        print(f"✅ WhatsApp alert sent successfully! SID: {message.sid}")
        return True
        
    except Exception as e:
        print(f"❌ Twilio API Error: {str(e)}")
        return False
