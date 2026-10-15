import os
from getpass import getpass
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parent
PROJECT_DIR = ROOT / "nifty_intraday_agent_complete"
ENV_FILE = PROJECT_DIR / ".env"

try:
    from dotenv import load_dotenv, set_key
except ImportError as exc:
    raise RuntimeError(
        "python-dotenv is missing. Run: '" + str(ROOT / '.venv' / 'Scripts' / 'python.exe') + " -m pip install -r " + str(PROJECT_DIR / 'requirements.txt') + "'"
    ) from exc

try:
    from kiteconnect import KiteConnect  # type: ignore[reportMissingTypeStubs]
except ImportError as exc:
    raise RuntimeError(
        "kiteconnect is missing. Run: '" + str(ROOT / '.venv' / 'Scripts' / 'python.exe') + " -m pip install -r " + str(PROJECT_DIR / 'requirements.txt') + "'"
    ) from exc

if not ENV_FILE.is_file():
    raise FileNotFoundError(f"Project .env file not found: {ENV_FILE}")

load_dotenv(ENV_FILE, override=True)

api_key = os.getenv("KITE_API_KEY")
api_secret = os.getenv("KITE_API_SECRET")

if not api_key or not api_secret:
    raise RuntimeError(
        "KITE_API_KEY or KITE_API_SECRET is missing in the active .env file. "
        f"Expected: {ENV_FILE}"
    )

kite: Any = KiteConnect(api_key=api_key)

print("Open this Kite Connect login URL in your browser and sign in:")
print(kite.login_url())
print("After Kite redirects, copy the request_token value from the redirect URL.")
request_token = getpass("Kite request token (input hidden): ").strip()

if not request_token:
    raise ValueError("Request token cannot be empty")

session = cast(
    dict[str, Any],
    kite.generate_session(request_token, api_secret=api_secret),
)
updated, _, _ = set_key(
    ENV_FILE, "KITE_ACCESS_TOKEN", session["access_token"], quote_mode="never"
)
if not updated:
    raise RuntimeError(f"Could not update {ENV_FILE}; check file permissions.")

print(f"Kite access token saved to {ENV_FILE}")