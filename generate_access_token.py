import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_DIR = ROOT / "nifty_intraday_agent_complete"

try:
    from dotenv import load_dotenv
except ImportError as exc:
    raise RuntimeError(
        "python-dotenv is missing. Run: '" + str(ROOT / '.venv' / 'Scripts' / 'python.exe') + " -m pip install -r " + str(PROJECT_DIR / 'requirements.txt') + "'"
    ) from exc

try:
    from kiteconnect import KiteConnect
except ImportError as exc:
    raise RuntimeError(
        "kiteconnect is missing. Run: '" + str(ROOT / '.venv' / 'Scripts' / 'python.exe') + " -m pip install -r " + str(PROJECT_DIR / 'requirements.txt') + "'"
    ) from exc

env_candidates = [
    ROOT / ".env",
    PROJECT_DIR / ".env",
    ROOT / "nifty_intraday_agent_complete" / ".env",
]
loaded_env = None
for env_file in env_candidates:
    if env_file.exists():
        load_dotenv(env_file)
        loaded_env = env_file
        break

if loaded_env is None:
    raise FileNotFoundError(
        "No .env file found. Place your Kite credentials in one of: "
        f"{', '.join(str(p) for p in env_candidates)}"
    )

api_key = os.getenv("KITE_API_KEY")
api_secret = os.getenv("KITE_API_SECRET")

if not api_key or not api_secret:
    raise RuntimeError(
        "KITE_API_KEY or KITE_API_SECRET is missing in the active .env file. "
        f"Loaded env: {loaded_env}"
    )

kite = KiteConnect(api_key=api_key)

request_token = input("Paste request token: ").strip()

if not request_token:
    raise ValueError("Request token cannot be empty")

session = kite.generate_session(request_token, api_secret=api_secret)

print("\n==============================")
print("KITE LOGIN SUCCESSFUL")
print("==============================")
print("User ID:", session["user_id"])
print("User Name:", session["user_name"])
print("Access Token:", session["access_token"])
print("==============================")