"""商家配置的领域模型与边界校验。纯逻辑，不碰数据库。"""
from __future__ import annotations

import pytest

import merchant as M


def test_builtin_default_is_valid():
    """内置默认必须自己先合法，否则降级路径会推出一个非法配置。"""
    assert M.validate(M.BUILTIN_DEFAULT.as_fields()) == []


def test_builtin_default_matches_todays_behaviour():
    """内置默认必须等于今天 .env 默认值 + MERCHANT_POLICY，否则 36 个回归测试会变红。"""
    d = M.BUILTIN_DEFAULT
    assert d.return_window_days == 30
    assert d.max_discount_pct == 0.30
    assert d.instant_refund_cap_usd == 50.0
    assert d.manual_sla_hours == 2
    assert d.emotion_hard_stop == 0.70
    assert d.emotion_large_model == 0.45
    assert d.high_value_order_usd == 200.0
    assert d.max_negotiation_rounds == 2
    assert d.max_session_turns == 8
    assert d.abuse_negotiation_limit == 3
    assert set(d.rules) == {"R-WINDOW", "R-FINAL", "R-HYGIENE", "R-USED", "R-DAMAGE"}
    assert d.version == 0


@pytest.mark.parametrize("field,bad", [
    ("max_discount_pct", 0.51),
    ("max_discount_pct", -0.01),
    ("emotion_hard_stop", 0.95),
    ("emotion_hard_stop", 0.49),
    ("emotion_large_model", 0.19),
    ("emotion_large_model", 0.81),
    ("max_negotiation_rounds", 0),
    ("max_negotiation_rounds", 4),
    ("return_window_days", 6),
    ("return_window_days", 366),
    ("instant_refund_cap_usd", -1),
    ("instant_refund_cap_usd", 501),
    ("max_session_turns", 3),
    ("max_session_turns", 21),
    ("manual_sla_hours", 0),
    ("manual_sla_hours", 49),
    ("high_value_order_usd", 19),
    ("high_value_order_usd", 5001),
    ("abuse_negotiation_limit", 1),
    ("abuse_negotiation_limit", 11),
])
def test_out_of_bounds_rejected(field, bad):
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields[field] = bad
    violations = M.validate(fields)
    assert any(v.field == field for v in violations), \
        f"{field}={bad} 应被拒，实际 violations={violations}"


def test_emotion_cross_field_constraint():
    """large_model 必须严格小于 hard_stop，否则升档那一路永远命中不到。"""
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["emotion_large_model"] = 0.70
    fields["emotion_hard_stop"] = 0.70
    violations = M.validate(fields)
    assert any(v.field == "emotion_large_model" for v in violations)


def test_damage_rule_cannot_be_disabled():
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["rules"] = dict(fields["rules"])
    fields["disabled_rules"] = ["R-DAMAGE"]
    violations = M.validate(fields)
    assert any(v.field == "R-DAMAGE" for v in violations)


def test_other_rules_may_be_disabled():
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["disabled_rules"] = ["R-FINAL", "R-HYGIENE"]
    assert M.validate(fields) == []


def test_clamp_fixes_each_field_independently():
    """一个字段越界不该让商家丢掉其余设置。"""
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["max_discount_pct"] = 0.99
    fields["manual_sla_hours"] = 6          # 合法，必须保留
    cfg = M.clamp(fields)
    assert cfg.max_discount_pct == 0.50     # 夹到上界
    assert cfg.manual_sla_hours == 6        # 原样保留
