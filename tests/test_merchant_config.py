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


# ── Fix 1: 强制转换必须是 total function，不能抛异常 ──────────────────────

def test_clamp_handles_non_numeric_version():
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["version"] = "v3"
    cfg = M.clamp(fields)          # 不应抛异常
    assert cfg.version == 0


def test_clamp_handles_none_version():
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["version"] = None
    cfg = M.clamp(fields)          # 不应抛异常
    assert cfg.version == 0


def test_clamp_handles_non_mapping_rules():
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["rules"] = "R-WINDOW"
    cfg = M.clamp(fields)          # 不应抛异常
    assert dict(cfg.rules) == dict(M.BUILTIN_DEFAULT.rules)


def test_clamp_handles_overflow_number():
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["instant_refund_cap_usd"] = 10 ** 400
    cfg = M.clamp(fields)          # 不应抛 OverflowError
    assert cfg.instant_refund_cap_usd == M.BUILTIN_DEFAULT.instant_refund_cap_usd


def test_validate_rejects_overflow_number_without_raising():
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["instant_refund_cap_usd"] = 10 ** 400
    violations = M.validate(fields)  # OverflowError 不是 ValueError 子类，不应抛异常
    assert any(v.field == "instant_refund_cap_usd" for v in violations)


# ── Fix 3: NaN 不能被 max(lo, min(hi, nan)) 静默选中最宽松端 ────────────────

def test_clamp_handles_nan():
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["max_discount_pct"] = float("nan")
    cfg = M.clamp(fields)
    assert cfg.max_discount_pct == M.BUILTIN_DEFAULT.max_discount_pct


def test_validate_rejects_nan():
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["max_discount_pct"] = float("nan")
    violations = M.validate(fields)
    assert any(v.field == "max_discount_pct" for v in violations)


# ── Fix 2: emotion 冲突修复必须单边——保留更严格的 hard_stop ─────────────────

def test_clamp_emotion_repair_preserves_stricter_hard_stop():
    """商家把 hard_stop 收紧到 0.50，一行脏数据把 large_model 设成 0.60（本身
    在合法区间内）。单边修复必须只压低 large_model，绝不能把商家调严的
    hard_stop 抬回默认 0.70——那是朝不安全方向改一个商家设对了的护栏。"""
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["emotion_hard_stop"] = 0.50
    fields["emotion_large_model"] = 0.60
    cfg = M.clamp(fields)
    assert cfg.emotion_hard_stop == 0.50
    assert cfg.emotion_large_model < cfg.emotion_hard_stop


# ── Fix 4: 规则内容也要校验——空文案/未知代码不能悄悄过关 ────────────────────

def test_validate_rejects_blank_rule_body():
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["rules"] = dict(fields["rules"])
    fields["rules"]["R-WINDOW"] = "  "
    violations = M.validate(fields)
    assert any(v.field == "R-WINDOW" for v in violations)


def test_validate_accepts_mapping_proxy_rules():
    """MerchantConfig.rules 本身就是 MappingProxyType（Fix 8）；load-modify-save
    时把它原样传回 validate 不该因为容器类型不是 dict 而被误拒。"""
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["rules"] = M.BUILTIN_DEFAULT.rules
    assert M.validate(fields) == []


def test_validate_rejects_unknown_rule_code():
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["rules"] = dict(fields["rules"])
    fields["rules"]["R-BOGUS"] = "某些内容"
    violations = M.validate(fields)
    assert any(v.field == "R-BOGUS" for v in violations)


# ── Fix 5: 整数字段不接受小数——夹了个 2.9 却按 2 生效，等于骗商家 ───────────

@pytest.mark.parametrize("field", list(M.INT_FIELDS))
def test_non_integral_int_field_rejected(field):
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields[field] = fields[field] + 0.5   # 仍在区间内，只是非整数
    violations = M.validate(fields)
    assert any(v.field == field for v in violations), \
        f"{field}={fields[field]} 应被拒（非整数），实际 violations={violations}"


# ── Fix 6: 闭区间的两端都必须合法，防止 <= 被悄悄改成 < ─────────────────────

@pytest.mark.parametrize("field", list(M.BOUNDS))
def test_boundary_values_are_accepted(field):
    lo, hi = M.BOUNDS[field]
    for edge in (lo, hi):
        fields = M.BUILTIN_DEFAULT.as_fields()
        if field == "emotion_large_model" and edge == hi:
            # 0.80 会撞上默认 hard_stop=0.70 的跨字段约束，与本测试无关，先垫高
            fields["emotion_hard_stop"] = 0.90
        fields[field] = edge
        violations = M.validate(fields)
        assert not any(v.field == field for v in violations), \
            f"{field}={edge}（边界值）不应被拒，实际 violations={violations}"


# ── Fix 7: clamp 的其余分支——缺字段/非数字/不可变规则重置/空文案/version 透传 ──

def test_clamp_defaults_missing_field():
    fields = M.BUILTIN_DEFAULT.as_fields()
    del fields["manual_sla_hours"]
    cfg = M.clamp(fields)
    assert cfg.manual_sla_hours == M.BUILTIN_DEFAULT.manual_sla_hours


def test_clamp_falls_back_on_non_numeric_field():
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["max_discount_pct"] = "not-a-number"
    cfg = M.clamp(fields)
    assert cfg.max_discount_pct == M.BUILTIN_DEFAULT.max_discount_pct


def test_clamp_reinstates_immutable_rule():
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["rules"] = {k: v for k, v in fields["rules"].items() if k != "R-DAMAGE"}
    cfg = M.clamp(fields)
    assert cfg.rules["R-DAMAGE"] == M.BUILTIN_DEFAULT.rules["R-DAMAGE"]


def test_clamp_falls_back_on_blank_note():
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["exceptions_note"] = "   "
    cfg = M.clamp(fields)
    assert cfg.exceptions_note == M.BUILTIN_DEFAULT.exceptions_note


def test_clamp_preserves_valid_version():
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["version"] = 7
    cfg = M.clamp(fields)
    assert cfg.version == 7


def test_validate_reports_missing_field():
    fields = M.BUILTIN_DEFAULT.as_fields()
    del fields["manual_sla_hours"]
    violations = M.validate(fields)
    assert any(v.field == "manual_sla_hours" for v in violations)


def test_validate_reports_non_numeric_field():
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["max_discount_pct"] = "not-a-number"
    violations = M.validate(fields)
    assert any(v.field == "max_discount_pct" for v in violations)


def test_validate_reports_blank_exceptions_note():
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["exceptions_note"] = "   "
    violations = M.validate(fields)
    assert any(v.field == "exceptions_note" for v in violations)


# ── Fix 8: rules 必须真不可变，不能被原地赋值污染全局共享的 BUILTIN_DEFAULT ──

def test_rules_mapping_is_immutable():
    with pytest.raises(TypeError):
        M.BUILTIN_DEFAULT.rules["R-DAMAGE"] = "tampered"


def test_rules_mapping_still_usable():
    import dataclasses

    d = M.BUILTIN_DEFAULT
    assert set(d.rules) == set(M.RULE_CODES)
    assert dict(d.rules)["R-DAMAGE"] == d.rules["R-DAMAGE"]
    d2 = dataclasses.replace(d, version=5)
    assert d2.version == 5
    assert dict(d2.rules) == dict(d.rules)


# ── Fix A: version 必须经 _coerce，非负整数，绝不抛异常 ─────────────────────

def test_clamp_handles_infinite_version():
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["version"] = float("inf")
    cfg = M.clamp(fields)          # int(float("inf")) 本身会抛 OverflowError，不应逃逸
    assert cfg.version == 0


def test_clamp_truncates_fractional_version():
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["version"] = 3.9
    cfg = M.clamp(fields)
    assert cfg.version == 3


def test_clamp_floors_negative_version_at_zero():
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["version"] = -5
    cfg = M.clamp(fields)
    assert cfg.version == 0


# ── Fix B/C: 规则文案与 exceptions_note 的「空」判定必须挡住 None/数字，
#            clamp 对空文案要回退到平台原文，不能丢弃这条规则 ──────────────

def test_clamp_falls_back_blank_rule_body_instead_of_dropping():
    """空文案不能被丢弃——丢弃等于关闭这条规则的展示，是朝不安全方向滑。
    必须保留规则代码，只把文案换成平台默认值。"""
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["rules"] = {"R-WINDOW": "   "}
    cfg = M.clamp(fields)
    assert cfg.rules["R-WINDOW"] == M.BUILTIN_DEFAULT.rules["R-WINDOW"]


def test_clamp_falls_back_none_rule_body():
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["rules"] = {"R-WINDOW": None}
    cfg = M.clamp(fields)
    assert cfg.rules["R-WINDOW"] == M.BUILTIN_DEFAULT.rules["R-WINDOW"]


def test_clamp_falls_back_non_string_rule_body():
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["rules"] = {"R-WINDOW": 123}
    cfg = M.clamp(fields)
    assert cfg.rules["R-WINDOW"] == M.BUILTIN_DEFAULT.rules["R-WINDOW"]


def test_validate_rejects_none_rule_body():
    """str(None) == "None" 会骗过朴素的 .strip() 检查，必须显式挡 None。"""
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["rules"] = dict(fields["rules"])
    fields["rules"]["R-WINDOW"] = None
    violations = M.validate(fields)
    assert any(v.field == "R-WINDOW" for v in violations)


def test_validate_rejects_numeric_rule_body():
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["rules"] = dict(fields["rules"])
    fields["rules"]["R-WINDOW"] = 123
    violations = M.validate(fields)
    assert any(v.field == "R-WINDOW" for v in violations)


def test_clamp_falls_back_none_exceptions_note():
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["exceptions_note"] = None
    cfg = M.clamp(fields)
    assert cfg.exceptions_note == M.BUILTIN_DEFAULT.exceptions_note


def test_validate_rejects_none_exceptions_note():
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["exceptions_note"] = None
    violations = M.validate(fields)
    assert any(v.field == "exceptions_note" for v in violations)


# ── Fix D: R-DAMAGE 不能只靠 disabled_rules 挡——rules 字典本身也要查 ────────

def test_validate_rejects_damage_rule_missing_from_rules_dict():
    """Task 9 的 save() 直接传 rules，未必同步维护 disabled_rules；
    光查 disabled_rules 挡不住有人只删 rules 里的 R-DAMAGE。"""
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["rules"] = {k: v for k, v in fields["rules"].items() if k != "R-DAMAGE"}
    # disabled_rules 刻意不同步更新，证明漏洞不是靠它才被堵上的
    violations = M.validate(fields)
    assert any(v.field == "R-DAMAGE" for v in violations)


# ── Fix E: clamp 主动收编规则表，未知代码应被丢弃而非保留 ───────────────────

def test_clamp_drops_unknown_rule_codes():
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["rules"] = {"R-BOGUS": "some text"}
    cfg = M.clamp(fields)
    assert "R-BOGUS" not in cfg.rules


# ── Fix H: 钉住 rules={}（显式清空）与 rules=None（缺省）的不同降级语义 ──────

def test_clamp_rules_empty_dict_yields_curated_minimum():
    """rules={} 是「显式清空」——clamp 收编后只留下不可停用的 R-DAMAGE，
    不会像 rules=None 那样退回全部 5 条默认。"""
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["rules"] = {}
    cfg = M.clamp(fields)
    assert set(cfg.rules) == set(M.IMMUTABLE_RULES)


def test_clamp_rules_none_yields_all_defaults():
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields["rules"] = None
    cfg = M.clamp(fields)
    assert set(cfg.rules) == set(M.RULE_CODES)
    assert dict(cfg.rules) == dict(M.BUILTIN_DEFAULT.rules)


# ── Fix G: clamp 的输出必须永远能通过 validate——这是读闸/写闸自洽的不变量,
#           本该能一次性抓到 B/C/E 三个问题 ─────────────────────────────────

_HOSTILE_CLAMP_INPUTS = [
    {"max_discount_pct": float("inf")},
    {"max_discount_pct": float("-inf")},
    {"max_discount_pct": float("nan")},
    {"return_window_days": -100},
    {"return_window_days": 10 ** 9},
    {"instant_refund_cap_usd": "not-a-number"},
    {"instant_refund_cap_usd": 10 ** 400},
    {"rules": {"R-WINDOW": None, "R-DAMAGE": 123}},
    {"rules": {"R-WINDOW": "   "}},
    {"rules": {}},
    {"rules": {"R-BOGUS": "x"}},
    {"rules": "not-a-mapping"},
    {"rules": None},
    {"version": "v3"},
    {"version": None},
    {"version": float("inf")},
    {"version": 3.9},
    {"version": -5},
    {"exceptions_note": ""},
    {"exceptions_note": "   "},
    {"exceptions_note": None},
]


@pytest.mark.parametrize("overrides", _HOSTILE_CLAMP_INPUTS, ids=[str(o) for o in _HOSTILE_CLAMP_INPUTS])
def test_clamp_output_always_validates(overrides):
    fields = M.BUILTIN_DEFAULT.as_fields()
    fields.update(overrides)
    cfg = M.clamp(fields)
    assert M.validate(cfg.as_fields()) == [], \
        f"clamp({overrides}) 的输出没能通过 validate"


# ── Fix I 的推理由这两个已有测试覆盖，见 test_boundary_values_are_accepted
#    （闭区间两端）与 test_clamp_emotion_repair_preserves_stricter_hard_stop
#    （单边修复不越界）；这里不再重复。


# ════════════════════════════════ RetentionDeps
def test_deps_defaults_to_builtin_config():
    import deps as D
    d = D.build_default()
    assert d.config is M.BUILTIN_DEFAULT
    assert d.config_version == 0
    assert d.tenant_id == "public"
    assert d.cost_budget_usd == 0.05      # 与今天的 RS_COST_BUDGET 默认一致


def test_deps_is_immutable():
    """快照被下游改掉就不是快照了。"""
    import dataclasses
    import deps as D
    d = D.build_default()
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.config_version = 9


# ════════════════════════════════ policy 读 deps 而非模块常量
def test_policy_reads_discount_cap_from_deps():
    """把上限调到 10%，让利必须跟着降——证明它读的是 deps 不是 C。"""
    import dataclasses
    import deps as D
    import policy
    from models import Scenario

    tight = dataclasses.replace(M.BUILTIN_DEFAULT, max_discount_pct=0.10)
    d = dataclasses.replace(D.build_default(), config=tight)

    order = {"order_id": "O-1", "total": 100.0, "category": "apparel",
             "sku": "X", "sizes_in_stock": [], "repairable": False,
             "negotiations_last_90d": 0, "days_since_delivery": 5,
             "final_sale": False, "opened": False, "used": False}
    customer = {"name": "T", "tier": "normal", "risk_flag": False,
                "returns_last_90d": 0}
    res = policy.build_resolution(order, customer, Scenario.VALUE_GAP,
                                  {"eligible": True, "violated": []}, 1, d)
    assert all(o["value"] <= 10.0 for o in res["offers"]), res["offers"]


def test_policy_reads_emotion_threshold_from_deps():
    import dataclasses
    import deps as D
    import policy
    from models import ReturnReason, Scenario

    calm = dataclasses.replace(M.BUILTIN_DEFAULT, emotion_hard_stop=0.90)
    d = dataclasses.replace(D.build_default(), config=calm)
    order = {"days_since_delivery": 5, "final_sale": False, "opened": False,
             "used": False, "category": "home"}
    # 0.85 在默认 0.70 下会判成 EMOTIONAL_INSIST，在 0.90 下不会
    got = policy.classify_scenario(order, ReturnReason.SIZE_FIT, 0.85,
                                   {"eligible": True, "violated": []}, d)
    assert got is not Scenario.EMOTIONAL_INSIST


def test_disabled_rule_stops_blocking():
    """停用 R-FINAL 后，final sale 不再拦人。"""
    import dataclasses
    import deps as D
    import policy
    from models import ReturnReason

    rules = {k: v for k, v in M.BUILTIN_DEFAULT.rules.items() if k != "R-FINAL"}
    cfg = dataclasses.replace(M.BUILTIN_DEFAULT, rules=rules)
    d = dataclasses.replace(D.build_default(), config=cfg)

    order = {"days_since_delivery": 5, "final_sale": True, "opened": False,
             "used": False, "category": "home"}
    elig = policy.check_eligibility(order, ReturnReason.CHANGED_MIND, d)
    assert elig["eligible"], elig


# ════════════════════════════════ llm 路由读 deps
def test_routing_reads_high_value_threshold_from_deps():
    import dataclasses
    import deps as D
    import llm
    from models import ModelTier, Scenario

    cfg = dataclasses.replace(M.BUILTIN_DEFAULT, high_value_order_usd=50.0)
    d = dataclasses.replace(D.build_default(), config=cfg)
    tier, why = llm.route_generation(Scenario.VALUE_GAP, 0.1, 80.0, 1, "normal",
                                     0.0, d)
    assert tier is ModelTier.LARGE
    assert why == "high_value_order_worth_the_spend"


def test_routing_reads_cost_budget_from_deps():
    """预算跟套餐走：starter 的 0.02 花完就该降模板。"""
    import dataclasses
    import deps as D
    import llm
    from models import ModelTier, Scenario

    d = dataclasses.replace(D.build_default(), cost_budget_usd=0.02)
    tier, why = llm.route_generation(Scenario.VALUE_GAP, 0.1, 100.0, 1, "normal",
                                     0.02, d)
    assert tier is ModelTier.NONE
    assert why == "cost_budget_exhausted"


def test_rule_intent_reads_emotion_threshold_from_deps():
    import dataclasses
    import deps as D
    import llm

    cfg = dataclasses.replace(M.BUILTIN_DEFAULT, emotion_hard_stop=0.60)
    d = dataclasses.replace(D.build_default(), config=cfg)
    # "失望" 给 0.45，低于 0.60，不该走硬停止那一支
    res = llm.rule_intent("I am disappointed, I want to return", d)
    assert res is not None
    assert res.emotion < 0.60
