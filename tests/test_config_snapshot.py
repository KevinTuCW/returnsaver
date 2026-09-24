"""配置快照语义：会话钉版本，token 带版本，L4 按签发时那一版复核。"""
from __future__ import annotations

import dataclasses
import json

import pytest

import deps as D
import guardrails as G
import merchant as M


def _deps(cap: float, version: int) -> D.RetentionDeps:
    cfg = dataclasses.replace(M.BUILTIN_DEFAULT, max_discount_pct=cap,
                              version=version)
    return dataclasses.replace(D.build_default(), config=cfg,
                               config_version=version)


def test_token_carries_config_version():
    d = _deps(0.30, 7)
    tok = G.issue_offer_token("ORD-1001", {"offer_id": "X", "value": 10.0}, d)
    raw_hex, _, _sig = tok.partition(".")
    payload = json.loads(bytes.fromhex(raw_hex))
    assert payload["cfg_v"] == 7


def test_value_cap_reads_deps():
    d = _deps(0.10, 1)
    order = {"order_id": "ORD-1001", "total": 100.0}
    with pytest.raises(G.GuardrailTripped) as e:
        G.validate_value_cap({"offer_id": "X", "value": 20.0}, order, d)
    assert e.value.code == "VALUE_OVER_CAP"
    # 10.0 在 10% 上限内，不该抛
    G.validate_value_cap({"offer_id": "X", "value": 10.0}, order, d)
