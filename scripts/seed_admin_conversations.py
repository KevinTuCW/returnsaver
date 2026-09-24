"""Generate persistent, API-level Admin demo conversations."""
from __future__ import annotations

import json
import urllib.request


BASE = "http://127.0.0.1:8777"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

CASES = [
    ("C-001", "ORD-1001", "The shirt is too small and I want to return it.", True),
    ("C-002", "ORD-1002", "The mug arrived cracked and I need a replacement.", True),
    ("C-003", "ORD-1003", "I changed my mind and want to return the jacket.", True),
    ("C-004", "ORD-1004", "I cannot get the headphones to work and may return them.", True),
    ("C-005", "ORD-1005", "This lamp is unacceptable. Refund me right now.", False),
    ("C-006", "ORD-1006", "I am furious with this camera. I want my money back now.", False),
    ("C-007", "ORD-1007", "I changed my mind about the sneakers and want a return.", False),
    ("C-008", "ORD-1009", "The tote does not feel worth the price. I want to return it.", True),
    ("C-009", "ORD-1010", "The kettle is defective and keeps shutting off.", False),
    ("C-010", "ORD-1011", "These sneakers are too large. I need to return them.", False),
]


def post(payload: dict) -> dict:
    request = urllib.request.Request(
        f"{BASE}/api/negotiate",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with OPENER.open(request, timeout=120) as response:
        return json.load(response)


def main() -> None:
    turns = 0
    outcomes = []
    for customer_id, order_id, opening, extended in CASES:
        first = post({"customer_id": customer_id, "message": opening})
        turns += 1
        session_id = first["session_id"]
        latest = post({"session_id": session_id, "customer_id": customer_id,
                       "message": f"Yes, it is {order_id}.",
                       "confirm_order_id": order_id})
        turns += 1
        if extended:
            latest = post({"session_id": session_id, "customer_id": customer_id,
                           "message": "That option does not solve it. I still want a return."})
            turns += 1
            latest = post({"session_id": session_id, "customer_id": customer_id,
                           "message": "No, I still want to return it."})
            turns += 1
        outcomes.append({"customer": customer_id, "order": order_id,
                         "session": session_id, "scenario": latest.get("scenario"),
                         "status": latest.get("status")})
    print(json.dumps({"turns": turns, "customers": len(CASES),
                      "outcomes": outcomes}, indent=2))


if __name__ == "__main__":
    main()
