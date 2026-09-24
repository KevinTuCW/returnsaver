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


# ════════════════════════════════ 跨版本兑付（决策 3 的核心）
def test_token_signed_under_v1_still_redeems_after_v2_lowers_cap():
    """商家在 TTL 内把上限从 30% 调到 10%，已签发的 token 仍须兑付。

    没有 cfg_v 的话 L4 会否掉系统自己承诺给客户的方案——直接踩
    「符合规则的退货绝不加阻力」这条体验红线。
    """
    v1 = dataclasses.replace(M.BUILTIN_DEFAULT, max_discount_pct=0.30, version=1)
    v2 = dataclasses.replace(M.BUILTIN_DEFAULT, max_discount_pct=0.10, version=2)
    table = {1: v1, 2: v2}

    d1 = dataclasses.replace(D.build_default(), config=v1, config_version=1)
    # $25 在 v1 的 30% 内、在 v2 的 10% 外
    tok = G.issue_offer_token("ORD-1001", {"offer_id": "CREDIT_25",
                                           "value": 25.0}, d1)

    payload = G.verify_offer_token(
        tok,
        get_order=lambda oid: {"order_id": oid, "total": 100.0},
        load_config=lambda v: table[v])
    assert payload["value"] == 25.0
    assert payload["cfg_v"] == 1


def test_token_still_capped_by_its_own_version():
    """跨版本豁免不是免检：超过签发那一版的上限照样拦。"""
    v1 = dataclasses.replace(M.BUILTIN_DEFAULT, max_discount_pct=0.30, version=1)
    d1 = dataclasses.replace(D.build_default(), config=v1, config_version=1)
    tok = G.issue_offer_token("ORD-1001", {"offer_id": "X", "value": 40.0}, d1)
    with pytest.raises(G.GuardrailTripped) as e:
        G.verify_offer_token(
            tok,
            get_order=lambda oid: {"order_id": oid, "total": 100.0},
            load_config=lambda v: v1)
    assert e.value.code == "VALUE_OVER_CAP"
