"""真实链路验证：打真 LLM、上报真 Langfuse。与单元测试分开，因为它依赖网络。

用法：.venv/bin/python scripts/verify_live.py
前置：.env 里填好 RS_LLM_API_KEY 与 LANGFUSE_*（缺了会直接告诉你缺哪个）。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

import config as C  # noqa: E402
import metrics  # noqa: E402
import observability as obs  # noqa: E402
from app import app  # noqa: E402

client = TestClient(app)

CASES = [
    # (标题, customer, order, message, 期望 status, 期望走真模型)
    ("价值差·VIP·$100 → 大模型", "C-001", "ORD-1001",
     "It's too small, I want a return", "offer_made", True),
    ("使用问题·$260 → 大模型 + 手册", "C-004", "ORD-1004",
     "I can't get it to pair, how do I connect it?", "offer_made", True),
    ("破损·$38 → 小模型 + 修换", "C-002", "ORD-1002",
     "The mug arrived cracked", "offer_made", True),
    ("超窗口 → 模板婉拒（零调用）", "C-003", "ORD-1003",
     "I want to return this jacket", "declined", False),
    ("情绪+小额 → 秒退（零调用）", "C-005", "ORD-1005",
     "This is ridiculous, just refund me NOW!", "instant_refund", False),
    ("情绪+大额 → 人工 P1（零调用）", "C-006", "ORD-1006",
     "This is unacceptable, I want my money back NOW!", "escalated", False),
]


def run(cid: str, oid: str, msg: str) -> dict:
    r1 = client.post("/api/negotiate", json={"customer_id": cid, "message": msg}).json()
    r2 = client.post("/api/negotiate", json={
        "session_id": r1["session_id"], "customer_id": cid,
        "message": msg, "confirm_order_id": oid})
    return r2.json()


def main() -> int:
    print("=" * 78)
    print(f"LLM      : configured={C.USE_REAL_LLM}  base_url={C.LLM_BASE_URL}")
    print(f"models   : intent={C.MODEL_INTENT}  small={C.MODEL_SMALL}  large={C.MODEL_LARGE}")
    print(f"thinking : {C.MODEL_THINKING}")
    print(f"Langfuse : enabled={C.USE_LANGFUSE}  client_ready={obs.enabled()}  "
          f"host={C.LANGFUSE_BASE_URL}")
    print("=" * 78)
    if not C.USE_REAL_LLM:
        print("✗ 没有 LLM key，无法验证真实链路。请在 .env 填 RS_LLM_API_KEY。")
        return 1

    failures: list[str] = []
    for title, cid, oid, msg, want_status, want_real in CASES:
        t0 = time.time()
        try:
            d = run(cid, oid, msg)
        except Exception as e:
            print(f"✗ {title}\n    异常 {type(e).__name__}: {e}")
            failures.append(title)
            continue
        dt = time.time() - t0
        calls = d.get("model_calls", [])
        gen = calls[-1] if calls else {}
        model = gen.get("model", "-")
        used_real = not model.startswith(("rule-fastpath", "template")) and "mock" not in model
        ok = d.get("status") == want_status and used_real == want_real
        mark = "✓" if ok else "✗"
        if not ok:
            failures.append(title)
        print(f"{mark} {title}")
        print(f"    status={d.get('status')} scenario={d.get('scenario')} "
              f"action={d.get('action')}  {dt:.1f}s")
        print(f"    路由 {gen.get('tier')}/{model} ({gen.get('route_reason','-')}) "
              f"tok={gen.get('tokens_in')}/{gen.get('tokens_out')} "
              f"会话成本 ${d.get('cost_usd')}")
        reply = (d.get("reply") or "").replace("\n", " ")
        print(f"    话术「{reply[:72]}」")
        if d.get("offer"):
            print(f"    offer {d['offer']['offer_id']} ${d['offer']['value']}")

    # 护栏在真模型下同样必须拦住
    print("-" * 78)
    for force, want in [("bad_offer", "OFFER_NOT_IN_ALLOWLIST"),
                        ("bad_text", "FORBIDDEN_PHRASE"),
                        ("bad_amount", "UNAPPROVED_AMOUNT")]:
        r1 = client.post("/api/negotiate", json={
            "customer_id": "C-001", "message": "It's too small, I want a return"}).json()
        r = client.post("/api/negotiate", json={
            "session_id": r1["session_id"], "customer_id": "C-001",
            "message": "too small", "confirm_order_id": "ORD-1001", "force": force})
        got = r.json().get("guardrail", {}).get("code")
        ok = r.status_code == 422 and got == want
        print(f"{'✓' if ok else '✗'} 护栏 {force:11s} → HTTP {r.status_code} {got}")
        if not ok:
            failures.append(f"guardrail:{force}")

    # CSAT → Langfuse score
    sid = client.post("/api/negotiate", json={
        "customer_id": "C-001", "message": "I want to return"}).json()["session_id"]
    c = client.post(f"/api/csat?score=5&session_id={sid}").json()
    print(f"{'✓' if c.get('langfuse') else '·'} CSAT 上报 langfuse={c.get('langfuse')}")

    print("-" * 78)
    snap = metrics.snapshot()
    print("指标：", json.dumps({"conversations": snap["conversations"],
                               "cost": snap["cost"], "routing": snap["routing"]},
                              ensure_ascii=False, indent=2))
    obs.flush()
    print("Langfuse 已 flush" if obs.enabled() else "Langfuse 未启用")
    print("=" * 78)
    if failures:
        print(f"✗ 失败 {len(failures)} 项：{failures}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
