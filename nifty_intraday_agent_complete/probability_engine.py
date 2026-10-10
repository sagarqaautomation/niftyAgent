"""Leakage-resistant, explainable setup probability model.

Broad feature buckets replace the old exact setup-key lookup. Each bucket is
smoothed toward the global prior and shrunk by sample size.
"""
from __future__ import annotations
import json, math
from pathlib import Path
from typing import Any

FEATURE_NAMES = ("direction","regime","trend_strength","momentum","vwap_location","volume","candle","structure","time_bucket")

def _bucket(x: dict[str, Any]) -> dict[str, str]:
    adx=float(x.get("adx") or 0); rsi=float(x.get("rsi") or 50)
    rv=float(x.get("relative_volume") or 0); vw=float(x.get("vwap_distance_pct") or 0)
    candle=float(x.get("candle_strength") or 0); hour=int(x.get("hour") or 0)
    return {
      "direction":str(x.get("direction","UNKNOWN")),
      "regime":str(x.get("regime","UNKNOWN")),
      "trend_strength":"weak" if adx<20 else "medium" if adx<25 else "strong",
      "momentum":"bear" if rsi<45 else "neutral" if rsi<55 else "bull",
      "vwap_location":"below" if vw<-0.10 else "near" if vw<=0.10 else "above",
      "volume":"low" if rv<1 else "normal" if rv<1.25 else "high",
      "candle":"weak" if candle<0.50 else "medium" if candle<0.65 else "strong",
      "structure":str(x.get("structure","NONE")),
      "time_bucket":"open" if hour==9 else "morning" if hour==10 else "midday" if hour in (11,12,13) else "afternoon",
    }

def feature_snapshot(x: dict[str, Any]) -> dict[str,str]:
    return _bucket(x)

def _logit(p: float) -> float:
    p=min(.995,max(.005,p)); return math.log(p/(1-p))

def _sigmoid(x: float) -> float:
    if x>=0:
        z=math.exp(-x); return 1/(1+z)
    z=math.exp(x); return z/(1+z)

def fit_probability_model(trades: list[dict[str,Any]], min_bucket_samples:int=20, prior_strength:int=50) -> dict[str,Any]:
    resolved=[t for t in trades if t.get("status") in {"SUCCESS","FAILED"}]
    n=len(resolved); wins=sum(t.get("status")=="SUCCESS" for t in resolved)
    if not n: return {"version":2,"method":"smoothed_additive_log_odds","samples":0,"prior":.5,"features":{}}
    prior=(wins+prior_strength*.5)/(n+prior_strength); base=_logit(prior)
    features: dict[str, Any] = {}
    for name in FEATURE_NAMES:
        groups: dict[str, list[int]] = {}
        for t in resolved: groups.setdefault(_bucket(t)[name],[]).append(1 if t.get("status")=="SUCCESS" else 0)
        out: dict[str, dict[str, int | float]] = {}
        for key, vals in groups.items():
            count=len(vals)
            if count<min_bucket_samples: continue
            raw=(sum(vals)+prior_strength*prior)/(count+prior_strength)
            shrink=count/(count+prior_strength)
            out[key]={"samples":count,"win_rate":round(sum(vals)/count,6),"contribution":round((_logit(raw)-base)*shrink,6)}
        features[name]=out
    return {"version":2,"method":"smoothed_additive_log_odds","samples":n,"prior":round(prior,6),"min_bucket_samples":min_bucket_samples,"prior_strength":prior_strength,"features":features}

def predict(model:dict[str,Any], x:dict[str,Any]) -> tuple[float|None,int]:
    if not model or not model.get("features"): return None,0
    logit=_logit(float(model.get("prior",.5))); evidence=0; b=_bucket(x)
    for name in FEATURE_NAMES:
        p=model.get("features",{}).get(name,{}).get(b[name])
        if p: logit+=float(p.get("contribution",0)); evidence+=int(p.get("samples",0))
    return _sigmoid(logit), evidence

def load_model(path:str|Path)->dict[str,Any]|None:
    p=Path(path)
    if not p.exists(): return None
    try: return json.loads(p.read_text(encoding="utf-8"))
    except (OSError,ValueError,TypeError): return None

def save_model(model:dict[str,Any], path:str|Path)->None:
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(model,indent=2),encoding="utf-8")
