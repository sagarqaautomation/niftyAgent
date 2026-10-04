import sys
import importlib

packages = [
    "fastapi", "uvicorn", "dotenv", "pandas", "numpy",
    "requests", "feedparser", "twilio", "kiteconnect"
]

print("Python:", sys.version)
for name in packages:
    try:
        importlib.import_module(name)
        print("[OK]", name)
    except Exception as e:
        print("[MISSING]", name, "-", e)

print("\nIf all packages show [OK], run: python main.py")
