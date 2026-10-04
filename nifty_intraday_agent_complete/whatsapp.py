from typing import Any, Mapping

from twilio.rest import Client  # pyright: ignore[reportMissingTypeStubs]
from config import settings

def send_whatsapp(message: str) -> dict[str, str | bool]:
    if not settings.whatsapp_enabled:
        return {"sent": False, "reason": "WHATSAPP_ENABLED=false"}

    required = [
        settings.twilio_account_sid,
        settings.twilio_auth_token,
        settings.twilio_whatsapp_from,
        settings.whatsapp_to
    ]
    if not all(required):
        return {"sent": False, "reason": "Missing Twilio configuration"}

    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    if settings.twilio_content_sid:
        msg = client.messages.create(
            content_sid=settings.twilio_content_sid,
            from_=settings.twilio_whatsapp_from,
            to=settings.whatsapp_to
        )
    else:
        msg = client.messages.create(
            body=message,
            from_=settings.twilio_whatsapp_from,
            to=settings.whatsapp_to
        )
    return {"sent": True, "sid": str(msg.sid)}

def format_signal(signal: Mapping[str, Any]) -> str:
    return (
        f"NIFTY RESEARCH SIGNAL\n"
        f"Signal: {signal.get('signal')}\n"
        f"Technical: {signal.get('technical_score')}\n"
        f"Context: {signal.get('context_score')}\n"
        f"Total: {signal.get('total_score')}\n"
        f"Entry: {signal.get('entry_price')}\n"
        f"SL: {signal.get('stop_loss')}\n"
        f"Target: {signal.get('target_price')}\n"
        f"Option: {signal.get('option_symbol') or 'Not selected'}\n"
        f"Reason: {signal.get('reason')}\n"
        f"Mode: PAPER / ALERT ONLY"
    )
