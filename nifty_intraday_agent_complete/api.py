from fastapi import FastAPI, Query
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
