from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytz

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
ARCHIVE_DIR = DATA_DIR / "archive"

def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

def hkt_now() -> datetime:
    tz = pytz.timezone("Asia/Hong_Kong")
    return datetime.now(tz)

def build_payload() -> dict:
    now = hkt_now()
    target_date = (now - timedelta(days=1)).date().isoformat()
    items = []
    return {
        "generated_at_hkt": now.isoformat(),
        "target_date_hkt": target_date,
        "source": "HKEX AP/PHIP (deployment test mode)",
        "count": len(items),
        "items": items,
        "message": "Deployment test mode: replace with real HKEX scraping logic later."
    }

def save_payload(payload: dict) -> None:
    latest_path = DATA_DIR / "latest.json"
    archive_path = ARCHIVE_DIR / f"{payload['target_date_hkt']}.json"
    latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    archive_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved: {latest_path}")
    print(f"Saved: {archive_path}")

def main() -> None:
    ensure_dirs()
    payload = build_payload()
    save_payload(payload)

if __name__ == "__main__":
    main()
