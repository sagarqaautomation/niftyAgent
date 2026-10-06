import json
from typing import Any, Mapping

from twilio.rest import Client  # pyright: ignore[reportMissingTypeStubs]
from config import settings

def send_whatsapp(
    message: str,
    signal: Mapping[str, Any] | None = None,
) -> dict[str, str | bool]:
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

    if settings.twilio_content_sid and not settings.twilio_content_variables:
        return {
            "sent": False,
            "reason": "TWILIO_CONTENT_VARIABLES is required for a dynamic signal template",
        }

    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    if settings.twilio_content_sid:
        template_args: dict[str, Any] = {
            "content_sid": settings.twilio_content_sid,
            "from_": settings.twilio_whatsapp_from,
            "to": settings.whatsapp_to,
        }
        if settings.twilio_content_variables:
            try:
                variable_map = json.loads(settings.twilio_content_variables)
            except json.JSONDecodeError as exc:
                raise RuntimeError("TWILIO_CONTENT_VARIABLES must be a JSON object") from exc
            if not isinstance(variable_map, dict):
                raise RuntimeError("TWILIO_CONTENT_VARIABLES must be a JSON object")
            signal_values = signal or {}
            variables = {
                str(variable): str(signal_values.get(field) or "Not available")
                for variable, field in variable_map.items()
            }
            template_args["content_variables"] = json.dumps(variables)
        msg = client.messages.create(**template_args)
    else:
        msg = client.messages.create(
            body=message,
            from_=settings.twilio_whatsapp_from,
            to=settings.whatsapp_to
        )
    return {"sent": True, "sid": str(msg.sid)}

def format_signal(signal: Mapping[str, Any]) -> str:
    source = signal.get("signal_instrument") or "signal instrument unavailable"
    candle_time = signal.get("signal_candle_time") or "unavailable"
    spot_price = signal.get("spot_reference_price")
    spot_time = signal.get("spot_reference_time")
    spot_line = (
        f"NIFTY spot reference: {spot_price} at {spot_time}\n"
        if spot_price is not None else "NIFTY spot reference: unavailable\n"
    )
    return (
        f"NIFTY SPOT DIRECTIONAL SIGNAL\n"
        f"Signal: {signal.get('signal')}\n"
        f"Signal basis: {source}\n"
        f"Signal candle: {candle_time}\n"
        f"{spot_line}"
        f"Technical: {signal.get('technical_score')}\n"
        f"Context: {signal.get('context_score')}\n"
        f"Total: {signal.get('total_score')}\n"
        f"NIFTY spot entry: {signal.get('entry_price')}\n"
        f"NIFTY spot SL: {signal.get('stop_loss')}\n"
        f"NIFTY spot target: {signal.get('target_price')}\n"
        f"Option contract/strike: {signal.get('option_symbol') or 'Not selected'}\n"
        f"Reason: {signal.get('reason')}\n"
        f"Mode: PAPER / ALERT ONLY"
    )
