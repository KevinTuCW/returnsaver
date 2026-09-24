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
