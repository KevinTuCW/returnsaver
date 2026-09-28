from fastapi.testclient import TestClient
import time

from app import app
from auth import admin_signature
import metrics
import store


client = TestClient(app)


def test_admin_shell_serves_the_english_shopify_style_entrypoint():
    r = client.get("/admin")
    assert r.status_code == 200
    assert '<html lang="en">' in r.text
    assert "Return Saver Admin" in r.text
    assert "Dashboard" in r.text
    assert "Chats" in r.text
    assert "Policy" in r.text
    assert r.text.index("Dashboard") < r.text.index("Chats") < r.text.index("Policy")
    assert 'data-icon="dashboard"' in r.text
    assert 'data-icon="chats"' in r.text
    assert 'data-icon="policy"' in r.text
    assert "售后规则" not in r.text
    assert "历史咨询" not in r.text


def test_admin_assets_are_served_under_the_admin_namespace():
    css = client.get("/admin/assets/app.css")
    js = client.get("/admin/assets/app.js")
    assert css.status_code == 200
    assert "--p-color-primary" in css.text
    assert ".font-icon" in css.text
    assert '[data-icon="chart"]' in css.text
    assert js.status_code == 200
    assert "renderDashboard" in js.text
    assert "renderChats" in js.text
    assert "renderPolicy" in js.text
    assert "legacyDashboard" in js.text
    assert "drawer-backdrop" in js.text


def test_default_policy_copy_is_english():
    policy = client.get("/api/policy").json()
    assert all(not any("\u4e00" <= char <= "\u9fff" for char in text)
               for text in [*policy["rules"].values(), policy["exceptions_note"]])


def test_admin_chats_returns_session_summaries_without_raw_tokens():
    metrics.reset()
    store.SESSIONS.clear()
    first = client.post("/api/negotiate", json={
        "customer_id": "C-001",
        "message": "It is too small, I want a return",
    }).json()
    client.post("/api/negotiate", json={
        "session_id": first["session_id"],
        "customer_id": "C-001",
        "message": "It is too small, I want a return",
        "confirm_order_id": "ORD-1001",
    })

    r = client.get("/admin/api/chats")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    row = data["sessions"][0]
    assert row["customer_name"] == "Sarah"
    assert row["order_id"] == "ORD-1001"
    assert row["product"] == "Merino Crew Tee (Black / M)"
    assert [message["role"] for message in row["messages"]] == [
        "customer", "agent", "customer", "agent",
    ]
    assert "offer_token" not in str(data)


def test_admin_dashboard_exposes_business_kpis_and_time_series():
    r = client.get("/admin/api/dashboard?range=today")
    assert r.status_code == 200
    data = r.json()
    assert set(data["kpis"]) == {
        "agent_takeovers", "orders_processed", "estimated_loss_saved_usd",
        "human_escalations", "avg_handle_seconds", "avg_session_cost_usd",
    }
    assert len(data["series"]) == 12


def test_dashboard_handle_time_excludes_long_customer_away_gaps():
    from app import _active_handle_seconds

    session = store.new_session("C-001")
    session.messages = [
        {"role": "customer", "text": "start", "at": 1000},
        {"role": "agent", "text": "reply", "at": 1010},
        {"role": "customer", "text": "back later", "at": 4610},
        {"role": "agent", "text": "continued", "at": 4620},
    ]
    assert _active_handle_seconds(session) == 320


def test_admin_chat_filters_search_transcript_and_order():
    first = client.post("/api/negotiate", json={
        "customer_id": "C-001", "message": "It is too small, I want a return",
    }).json()
    client.post("/api/negotiate", json={
        "session_id": first["session_id"], "customer_id": "C-001",
        "message": "It is too small, I want a return",
        "confirm_order_id": "ORD-1001",
    })
    assert client.get("/admin/api/chats", params={"q": "too small"}).json()["total"] == 1
    assert client.get("/admin/api/chats", params={"order_id": "ORD-1001"}).json()["total"] == 1
    assert client.get("/admin/api/chats", params={"product": "mug"}).json()["total"] == 0


def test_policy_admin_update_supports_global_and_special_rules():
    original = dict(store.MERCHANT_POLICY)
    payload = {
        "return_window_days": 45,
        "exceptions_note": "Damage is always covered.",
        "rules": {"R-WINDOW": "Returns are accepted within 45 days."},
        "special_rules": [{"scope": "Product", "match": "Final Sale", "rule": "No returns."}],
        "auto_refund": {"enabled": True, "min_order_amount_usd": 10,
                        "max_order_amount_usd": 80, "minimum_prior_attempts": 2,
                        "keep_item_below_usd": 15},
    }
    try:
        r = client.put("/api/policy", json=payload)
        assert r.status_code == 200
        assert r.json()["policy"]["special_rules"] == payload["special_rules"]
        assert r.json()["policy"]["auto_refund"]["execution_mode"] == "automatic_when_qualified"
    finally:
        store.MERCHANT_POLICY.clear()
        store.MERCHANT_POLICY.update(original)


def _admin_params(tenant_id: str) -> dict:
    expires = int(time.time()) + 3600
    return {"tenant_id": tenant_id, "expires": expires,
            "sig": admin_signature(tenant_id, expires)}


def test_admin_url_is_signed_and_tenant_scoped():
    a = store.new_session("C-001", tenant_id="merchant-a")
    a.messages.append({"role": "customer", "text": "A only", "at": int(time.time())})
    b = store.new_session("C-002", tenant_id="merchant-b")
    b.messages.append({"role": "customer", "text": "B only", "at": int(time.time())})

    assert client.get("/admin/api/chats", params={"tenant_id": "merchant-a"}).status_code == 401
    data = client.get("/admin/api/chats", params=_admin_params("merchant-a")).json()
    assert data["total"] == 1
    assert data["sessions"][0]["tenant_id"] == "merchant-a"
    assert "B only" not in str(data)


def test_policy_publish_is_versioned_and_isolated_by_tenant():
    import merchant_store as MS

    payload = {
        "return_window_days": 45,
        "exceptions_note": "Damage is always covered.",
        "rules": dict(store.MERCHANT_POLICY["rules"]),
        "special_rules": [],
        "auto_refund": {"enabled": True, "min_order_amount_usd": 0,
                        "max_order_amount_usd": 80, "minimum_prior_attempts": 1,
                        "keep_item_below_usd": 20},
    }
    result = client.put("/api/policy", params=_admin_params("merchant-a"),
                        json=payload)
    assert result.status_code == 200, result.json()
    assert result.json()["version"] == 1
    cfg_a, _ = MS.load("merchant-a", None)
    cfg_b, _ = MS.load("merchant-b", None)
    assert cfg_a.return_window_days == 45
    assert cfg_a.instant_refund_cap_usd == 80
    assert cfg_b.return_window_days == 30
