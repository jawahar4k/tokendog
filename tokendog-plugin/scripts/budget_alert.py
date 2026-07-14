#!/usr/bin/env python3
# tokendog-plugin/scripts/budget_alert.py
"""Stop hook: POST a webhook alert when the daily alert threshold is crossed."""
import json
import os
import sys
import urllib.request


def _post(url: str, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5).read()


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw and raw.strip() else {}
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    try:
        from tokendog.budget import check
    except Exception:
        return 0
    try:
        glitch = os.path.join(os.getcwd(), ".glitch", "firmware", "firmware.db")
        result = check(session_id=payload.get("session_id"),
                       glitch_db=glitch if os.path.exists(glitch) else None)
        b = result["budget"]
        if result["over_alert"] and b.webhook_url:
            text = (f"TokenDog alert: today's spend ${result['daily']:.2f} crossed "
                    f"the ${b.alert_usd:.2f} threshold.")
            try:
                _post(b.webhook_url, {"text": text})
            except Exception:
                pass
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
