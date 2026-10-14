from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from kiteconnect import KiteConnect
from config import settings
from kite_auth_store import save_access_token, token_status
from database import (init_db, performance, latest_signal, list_signals, list_news, latest_premarket_snapshot, list_premarket_snapshots)

app = FastAPI(title="Nifty Intraday Agent API", version="1.0.0")
init_db()

@app.get("/health")
def health():
    return {"status": "ok", "service": "nifty-intraday-agent"}

@app.get("/performance")
def get_performance():
    return performance()

@app.get("/latest-signal")
def get_latest_signal():
    return latest_signal()

@app.get("/signals")
def get_signals(limit: int = Query(50, ge=1, le=500)):
    return list_signals(limit)

@app.get("/news")
def get_news(limit: int = Query(50, ge=1, le=500)):
    return list_news(limit)


@app.get("/premarket/latest")
def get_latest_premarket():
    return latest_premarket_snapshot()


@app.get("/premarket/history")
def get_premarket_history(limit: int = Query(50, ge=1, le=500)):
    return list_premarket_snapshots(limit)

@app.get("/kite/auth/status")
def kite_auth_status():
    """Expose token readiness only; never return the token itself."""
    return token_status()


@app.get("/kite/login")
def kite_login():
    """Redirect the account holder to official Kite login."""
    if not settings.kite_api_key or not settings.kite_api_secret:
        raise HTTPException(status_code=503, detail="Configure KITE_API_KEY and KITE_API_SECRET on the backend first.")
    kite = KiteConnect(api_key=settings.kite_api_key)
    return RedirectResponse(url=kite.login_url(), status_code=302)


@app.get("/kite/callback", response_class=HTMLResponse)
def kite_callback(
    request_token: str | None = None,
    status: str | None = None,
    action: str | None = None,
    enctoken: str | None = None,
):
    """Exchange Kite's short-lived request token and store the resulting access token."""
    if status != "success" or not request_token:
        raise HTTPException(status_code=400, detail="Kite login was cancelled or did not return a request token. Please try /kite/login again.")
    if not settings.kite_api_key or not settings.kite_api_secret:
        raise HTTPException(status_code=503, detail="Kite API credentials are not configured on the backend.")
    try:
        kite = KiteConnect(api_key=settings.kite_api_key)
        session = kite.generate_session(request_token, api_secret=settings.kite_api_secret)
        access_token = session.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            raise ValueError("Kite returned no access token")
        save_access_token(access_token, session.get("public_token"))
    except Exception as exc:
        # Do not include request token, API secret, or provider response in the page/log.
        raise HTTPException(status_code=502, detail="Kite token exchange failed. Restart login and check backend credentials and the registered callback URL.") from exc
    return HTMLResponse(
        "<!doctype html><html><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>NiftyAgent connected</title><body style='font:16px system-ui;max-width:600px;margin:3rem auto;padding:1rem'>"
        "<h2>Kite login completed</h2><p>NiftyAgent saved the session token securely on the backend.</p>"
        "<p>This token normally expires at 6:00 AM IST. You will need to complete Kite login again for a new session.</p>"
        "<p>You can close this tab. Automatic order placement remains disabled.</p></body></html>"
    )
