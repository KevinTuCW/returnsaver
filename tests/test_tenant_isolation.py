"""鉴权与租户隔离。对标 helpmate eval 的 tenant_isolation: 1.0 硬门。"""
from __future__ import annotations

import pytest

import auth


def test_dev_mode_when_no_keys_configured(monkeypatch):
    """未配 API_KEYS 时保持 clone-and-run。"""
    monkeypatch.setattr(auth, "_api_keys", lambda: {})
    p = auth.principal_from_key(None)
    assert p is not None
    assert p.dev_mode is True
    assert p.tenant_id == "public"


def test_unknown_key_rejected(monkeypatch):
    monkeypatch.setattr(auth, "_api_keys", lambda: {"sk-a": "dji:Alice"})
    assert auth.principal_from_key("sk-nope") is None
    assert auth.principal_from_key(None) is None


def test_key_resolves_tenant_and_customer(monkeypatch):
    monkeypatch.setattr(auth, "_api_keys", lambda: {"sk-a": "dji:Alice"})
    p = auth.principal_from_key("sk-a")
    assert (p.tenant_id, p.customer_id) == ("dji", "Alice")
    assert p.dev_mode is False


def test_tenant_only_grant_has_no_customer(monkeypatch):
    """customer_id=None 表示没有订单数据访问权，不是"访问所有订单"。"""
    monkeypatch.setattr(auth, "_api_keys", lambda: {"sk-ops": "dji"})
    p = auth.principal_from_key("sk-ops")
    assert (p.tenant_id, p.customer_id) == ("dji", None)


def test_parse_grant():
    assert auth.parse_grant("dji:Alice") == ("dji", "Alice")
    assert auth.parse_grant("dji") == ("dji", None)
    assert auth.parse_grant(" dji : Alice ") == ("dji", "Alice")


# ════════════════════════════════ 会话的租户边界
from fastapi.testclient import TestClient      # noqa: E402

import config as C                             # noqa: E402
from app import app                            # noqa: E402

client = TestClient(app)


def _keys(monkeypatch, mapping):
    monkeypatch.setattr(C, "API_KEYS", mapping)


def test_session_from_another_tenant_is_rejected(monkeypatch):
    """A 租户的会话配 B 租户的 key → 403。对标 helpmate 的 tenant_isolation 硬门。"""
    _keys(monkeypatch, {"sk-a": "tenant-a:C-001", "sk-b": "tenant-b:C-001"})
    r1 = client.post("/api/negotiate",
                     headers={"X-API-Key": "sk-a"},
                     json={"customer_id": "C-001", "message": "I want to return"})
    assert r1.status_code == 200, r1.json()
    sid = r1.json()["session_id"]

    r2 = client.post("/api/negotiate",
                     headers={"X-API-Key": "sk-b"},
                     json={"session_id": sid, "customer_id": "C-001",
                           "message": "still want to return"})
    assert r2.status_code == 403, r2.json()
    assert r2.json()["detail"] == "session belongs to another tenant"


def test_missing_key_is_401_when_keys_configured(monkeypatch):
    _keys(monkeypatch, {"sk-a": "tenant-a:C-001"})
    r = client.post("/api/negotiate",
                    json={"customer_id": "C-001", "message": "return"})
    assert r.status_code == 401


def test_dev_mode_needs_no_key(monkeypatch):
    """未配 key 时 clone-and-run 仍然成立。"""
    _keys(monkeypatch, {})
    r = client.post("/api/negotiate",
                    json={"customer_id": "C-001", "message": "I want to return"})
    assert r.status_code == 200
    assert r.json()["status"] == "awaiting_order_confirmation"


def test_session_records_tenant_and_pinned_version(monkeypatch):
    _keys(monkeypatch, {})
    import store
    r = client.post("/api/negotiate",
                    json={"customer_id": "C-001", "message": "I want to return"})
    s = store.get_session(r.json()["session_id"])
    assert s.tenant_id == C.DEFAULT_TENANT
    assert s.config_version == 0          # memory 模式钉 0 = 内置默认


def test_health_reports_config_source(monkeypatch):
    """跑库里的配置还是内置默认，运维得看得见。"""
    _keys(monkeypatch, {})
    h = client.get("/health").json()
    assert h["config"]["source"] in ("builtin", "database")
    assert h["config"]["degraded"] is False
    assert h["config"]["tenant"] == C.DEFAULT_TENANT
    assert "auth" in h and h["auth"]["key_count"] == 0


def test_health_goes_not_ok_when_config_degraded(monkeypatch):
    """库配了却连不上：用默认继续服务，但 ok=false，别假装一切正常。"""
    import merchant_store as MS
    _keys(monkeypatch, {})
    monkeypatch.setattr(MS, "_enabled", lambda: True)
    monkeypatch.setattr(MS, "_fetch_config", lambda t, v: (_ for _ in ()).throw(
        RuntimeError("connection refused")))
    MS.cache_clear()
    h = client.get("/health").json()
    assert h["config"]["degraded"] is True
    assert h["ok"] is False
    MS.cache_clear()


def test_customer_scoped_key_cannot_act_for_another_customer(monkeypatch):
    """key 绑到 C-001 就只能代表 C-001 说话。"""
    _keys(monkeypatch, {"sk-alice": "public:C-001"})
    r = client.post("/api/negotiate",
                    headers={"X-API-Key": "sk-alice"},
                    json={"customer_id": "C-003", "message": "I want to return"})
    assert r.status_code == 403, r.json()
    assert r.json()["detail"] == "api key is bound to another customer"


def test_tenant_scoped_key_may_pass_customer_through(monkeypatch):
    """服务间调用：租户级 key 不带客户身份，客户主体由上游鉴权后透传。

    这条是 T16 抓到的回归——原先订单归属绑的是 principal.customer_id，租户级
    key 下它恒为 None，postgres 模式里每个订单查询都返回 None，六个场景全
    order_mismatch。memory 模式看不出来，因为 store.ORDERS 根本不校验归属。
    """
    _keys(monkeypatch, {"sk-svc": "public"})
    r = client.post("/api/negotiate",
                    headers={"X-API-Key": "sk-svc"},
                    json={"customer_id": "C-001", "message": "I want to return"})
    assert r.status_code == 200, r.json()
    assert r.json()["status"] == "awaiting_order_confirmation"
    assert r.json()["candidates"], "租户级 key 必须能查到透传客户的订单"
