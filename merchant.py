"""商家配置的领域模型与边界校验。

刻意不碰数据库：校验要能在没有 Postgres 的单测里跑，持久化在 merchant_store.py。

为什么要有平台天花板：阈值一旦由商家提供，商家就成了护栏的不可信输入——而这个
护栏原本是用来防模型的。max_discount_pct=1.0 会让 L2/L4 的上限检查变成空操作，
emotion_hard_stop=1.0 会关掉「发火用户不再挽留」。商家能调的只是「多宽松」，
不是「要不要有护栏」。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 破损与质量豁免是消费者保护底线，不是商家可选项
IMMUTABLE_RULES = ("R-DAMAGE",)

RULE_CODES = ("R-WINDOW", "R-FINAL", "R-HYGIENE", "R-USED", "R-DAMAGE")

# (min, max) 闭区间。改这张表等于改产品承诺，不要随手动。
BOUNDS: dict[str, tuple[float, float]] = {
    "return_window_days":      (7, 365),
    "max_discount_pct":        (0.0, 0.50),
    "instant_refund_cap_usd":  (0.0, 500.0),
    "manual_sla_hours":        (1, 48),
    "emotion_hard_stop":       (0.50, 0.90),
    "emotion_large_model":     (0.20, 0.80),
    "high_value_order_usd":    (20.0, 5000.0),
    "max_negotiation_rounds":  (1, 3),
    "max_session_turns":       (4, 20),
    "abuse_negotiation_limit": (2, 10),
}

INT_FIELDS = ("return_window_days", "manual_sla_hours", "max_negotiation_rounds",
              "max_session_turns", "abuse_negotiation_limit")


@dataclass(frozen=True)
class Violation:
    field: str
    detail: str


@dataclass(frozen=True)
class MerchantConfig:
    """一个租户在某个版本上的完整策略快照。不可变——快照就该是快照。"""
    return_window_days: int
    max_discount_pct: float
    instant_refund_cap_usd: float
    manual_sla_hours: int
    emotion_hard_stop: float
    emotion_large_model: float
    high_value_order_usd: float
    max_negotiation_rounds: int
    max_session_turns: int
    abuse_negotiation_limit: int
    exceptions_note: str
    rules: dict[str, str] = field(default_factory=dict)   # 只含 enabled 的规则
    version: int = 0                                      # 0 = 内置默认

    def as_fields(self) -> dict[str, Any]:
        """摊平成 validate/clamp 吃的 dict。"""
        return {
            **{k: getattr(self, k) for k in BOUNDS},
            "exceptions_note": self.exceptions_note,
            "rules": dict(self.rules),
            "disabled_rules": [c for c in RULE_CODES if c not in self.rules],
        }


BUILTIN_DEFAULT = MerchantConfig(
    return_window_days=30,
    max_discount_pct=0.30,
    instant_refund_cap_usd=50.0,
    manual_sla_hours=2,
    emotion_hard_stop=0.70,
    emotion_large_model=0.45,
    high_value_order_usd=200.0,
    max_negotiation_rounds=2,
    max_session_turns=8,
    abuse_negotiation_limit=3,
    exceptions_note="质量问题与物流破损不受窗口与拆封限制，随时受理。",
    rules={
        "R-WINDOW":  "自签收之日起 30 天内可申请退货，逾期不再受理。",
        "R-FINAL":   "标记为 Final Sale 的清仓商品不支持退换。",
        "R-HYGIENE": "内衣、泳装等贴身类目一经拆封，出于卫生考虑不支持退货。",
        "R-USED":    "商品需保持未使用、吊牌完整状态；已明显使用的不支持无理由退货。",
        "R-DAMAGE":  "商品到货破损或发错，不受上述限制，可直接换货或退款。",
    },
    version=0,
)


def validate(fields: dict[str, Any]) -> list[Violation]:
    """写入前的主闸。返回空列表表示可以入库。"""
    out: list[Violation] = []
    for name, (lo, hi) in BOUNDS.items():
        if name not in fields:
            out.append(Violation(name, "缺少该字段"))
            continue
        try:
            v = float(fields[name])
        except (TypeError, ValueError):
            out.append(Violation(name, f"不是数字：{fields[name]!r}"))
            continue
        if not (lo <= v <= hi):
            out.append(Violation(name, f"{v} 超出区间 [{lo}, {hi}]"))

    # 跨字段：相等或反过来会让「升大模型」永远命中不到
    try:
        if float(fields["emotion_large_model"]) >= float(fields["emotion_hard_stop"]):
            out.append(Violation(
                "emotion_large_model",
                "必须严格小于 emotion_hard_stop，否则升档分支成为死代码"))
    except (KeyError, TypeError, ValueError):
        pass

    for code in fields.get("disabled_rules", []) or []:
        if code in IMMUTABLE_RULES:
            out.append(Violation(code, "该规则不可停用（消费者保护底线）"))

    if not str(fields.get("exceptions_note", "")).strip():
        out.append(Violation("exceptions_note", "不能为空——婉拒时要展示给客户"))

    return out


def clamp(fields: dict[str, Any]) -> MerchantConfig:
    """读取时的纵深防御：逐字段夹到区间内，不整行回退默认。

    有人绕过 API 直接 psql 插了越界值时走这条路。一个字段填错不该让商家丢掉
    其余所有设置，所以这里是 per-field clamp 而不是 wholesale fallback。
    """
    vals: dict[str, Any] = {}
    for name, (lo, hi) in BOUNDS.items():
        raw = fields.get(name, getattr(BUILTIN_DEFAULT, name))
        try:
            v = float(raw)
        except (TypeError, ValueError):
            v = float(getattr(BUILTIN_DEFAULT, name))
        v = max(lo, min(hi, v))
        vals[name] = int(v) if name in INT_FIELDS else v

    # 夹完还可能违反跨字段约束（如两者都被夹到 0.80/0.90 之外的组合）
    if vals["emotion_large_model"] >= vals["emotion_hard_stop"]:
        vals["emotion_large_model"] = BUILTIN_DEFAULT.emotion_large_model
        vals["emotion_hard_stop"] = BUILTIN_DEFAULT.emotion_hard_stop

    rules = dict(fields.get("rules") or BUILTIN_DEFAULT.rules)
    for code in IMMUTABLE_RULES:                      # 强制在场
        rules.setdefault(code, BUILTIN_DEFAULT.rules[code])

    note = str(fields.get("exceptions_note", "")).strip() \
        or BUILTIN_DEFAULT.exceptions_note

    return MerchantConfig(**vals, exceptions_note=note, rules=rules,
                          version=int(fields.get("version", 0)))
