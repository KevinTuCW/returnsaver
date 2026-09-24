"""商家配置的领域模型与边界校验。

刻意不碰数据库：校验要能在没有 Postgres 的单测里跑，持久化在 merchant_store.py。

为什么要有平台天花板：阈值一旦由商家提供，商家就成了护栏的不可信输入——而这个
护栏原本是用来防模型的。max_discount_pct=1.0 会让 L2/L4 的上限检查变成空操作，
emotion_hard_stop=1.0 会关掉「发火用户不再挽留」。商家能调的只是「多宽松」，
不是「要不要有护栏」。

clamp() 是读取层，跑在生产环境里处理不可信的数据库行——它绝不能抛异常，也绝不能
在修复冲突时滑向更宽松的一端（那等于让脏数据帮商家把护栏调松）。
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
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

EMOTION_GAP = 0.01   # large_model 必须严格小于 hard_stop，留一个最小间隔


def _coerce(raw: Any) -> float | None:
    """数值强制转换。失败或非有限值返回 None。

    validate 与 clamp 共用这一个入口：两者对「什么算合法数字」的判断必须一致，
    各写各的迟早漂移。OverflowError 不是 ValueError 的子类，必须显式捕获——
    一个 400 位的整数曾能穿过 validate 直接抛到请求里。NaN 会让
    `max(lo, min(hi, nan))` 退化成 hi，即静默选中区间最宽松的一端，所以也要挡。
    `int(float("inf"))` 本身也会抛 OverflowError——version 字段同样经这里过滤，
    所以它不需要另开一套 try/except。
    """
    try:
        v = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) else None


def _is_blank(value: Any) -> bool:
    """value 是否等价于「未填写」。

    只有非空字符串（strip 后）算有效值；None/数字/其它类型一律当作空——
    `str(None) == "None"` 会让朴素的 `not str(v).strip()` 检查失手放行 None，
    JSONB/text 列里 None 正是「未设置」最自然的表示，必须显式挡。
    """
    return not (isinstance(value, str) and value.strip())


@dataclass(frozen=True)
class Violation:
    field: str
    detail: str


@dataclass(frozen=True)
class MerchantConfig:
    """一个租户在某个版本上的完整策略快照。不可变——快照就该是快照。

    `as_fields()` 是唯一支持的序列化路径。`rules` 是 `mappingproxy`（见
    `__post_init__`），`dataclasses.asdict()`/`copy.deepcopy()`/`pickle.dumps()`
    都会在它上面抛 `TypeError: cannot pickle 'mappingproxy' object`——这是刻意
    保留的取舍（模块级共享的 BUILTIN_DEFAULT.rules 不可变，比兼容这几个内置函数
    更重要），不是待修的 bug。
    """
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
    rules: Mapping[str, str] = field(default_factory=dict)   # 只含 enabled 的规则
    version: int = 0                                         # 0 = 内置默认

    def __post_init__(self) -> None:
        # frozen dataclass 不能直接赋值，只能用 object.__setattr__。目的是让
        # rules 真正不可变——降级路径全局共享 BUILTIN_DEFAULT.rules，一次原地
        # 赋值（BUILTIN_DEFAULT.rules["R-DAMAGE"] = ...）就会污染所有后续读取。
        object.__setattr__(self, "rules", MappingProxyType(dict(self.rules)))

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
        v = _coerce(fields[name])
        if v is None:
            out.append(Violation(name, f"不是有限数字：{fields[name]!r}"))
            continue
        if not (lo <= v <= hi):
            out.append(Violation(name, f"{v} 超出区间 [{lo}, {hi}]"))
        elif name in INT_FIELDS and v != int(v):
            out.append(Violation(name, f"必须是整数：{v}"))

    # 跨字段：相等或反过来会让「升大模型」永远命中不到。
    # 缺失/非数字已由上面的 bounds 循环报过，这里只防重复上报。
    lm = _coerce(fields.get("emotion_large_model"))
    hs = _coerce(fields.get("emotion_hard_stop"))
    if lm is not None and hs is not None and lm >= hs:
        out.append(Violation(
            "emotion_large_model",
            "必须严格小于 emotion_hard_stop，否则升档分支成为死代码"))

    for code in fields.get("disabled_rules", []) or []:
        if code in IMMUTABLE_RULES:
            out.append(Violation(code, "该规则不可停用（消费者保护底线）"))

    # 规则原文会在婉拒时展示给客户（config.EXPERIENCE_INVARIANTS 的
    # decline_must_cite_rule）：空文案等于什么都没引用，未知代码没有对应文案。
    raw_rules = fields.get("rules")
    if isinstance(raw_rules, Mapping):
        # disabled_rules 只是「客户端声称禁用了什么」；rules 本身才是权威来源。
        # 一个只传 rules、没同步维护 disabled_rules 的写请求（Task 9 的
        # merchant_store.save 正是这样）不该绕过消费者保护底线。
        for code in IMMUTABLE_RULES:
            if code not in raw_rules:
                out.append(Violation(code, "该规则不可停用（消费者保护底线）"))
        for code, body in raw_rules.items():
            if code not in RULE_CODES:
                out.append(Violation(code, "未知规则代码"))
            elif _is_blank(body):
                out.append(Violation(code, "规则内容不能为空——婉拒时要展示给客户"))
    elif raw_rules is not None:
        out.append(Violation("rules", f"不是合法的规则映射：{raw_rules!r}"))

    if _is_blank(fields.get("exceptions_note")):
        out.append(Violation("exceptions_note", "不能为空——婉拒时要展示给客户"))

    return out


def clamp(fields: dict[str, Any]) -> MerchantConfig:
    """读取时的纵深防御：逐字段夹到区间内，不整行回退默认。

    有人绕过 API 直接 psql 插了越界值时走这条路。一个字段填错不该让商家丢掉
    其余所有设置，所以这里是 per-field clamp 而不是 wholesale fallback。
    对任何非有限数字（非数字类型、NaN、超出 float 表示范围的大整数……）一律
    回退到该字段的内置默认值，绝不抛异常——这一层就是给脏数据兜底的。
    """
    vals: dict[str, Any] = {}
    for name, (lo, hi) in BOUNDS.items():
        raw = fields.get(name, getattr(BUILTIN_DEFAULT, name))
        v = _coerce(raw)
        if v is None:
            v = float(getattr(BUILTIN_DEFAULT, name))
        v = max(lo, min(hi, v))
        vals[name] = int(v) if name in INT_FIELDS else v

    # 单边修复：保留 hard_stop（它是安全规则，低=严），只把 large_model 压到它
    # 下面。绝不反过来抬高 hard_stop——那是朝不安全方向改一个商家设对了的护栏。
    # 上界不需要再夹：这个分支只在 hs <= BOUNDS[large_model].hi 时才会触发（hs
    # 更大的话 large_model 不可能 >= hs，因为 large_model 本身已经夹到
    # <= BOUNDS[large_model].hi 了），所以 hs - GAP < hs <= hi_lm 恒成立——这条
    # 对任意 BOUNDS 取值都对，不依赖具体数字。下界则要看具体数字：本表
    # hard_stop.lo(0.50) - GAP(0.49) 仍然 > large_model.lo(0.20)，但这是这张表
    # 的巧合，不是结构性保证，换一张表可能不成立，所以下界的 max() 保留作纵深
    # 防御，不删。
    if vals["emotion_large_model"] >= vals["emotion_hard_stop"]:
        lo, _ = BOUNDS["emotion_large_model"]
        vals["emotion_large_model"] = max(lo, vals["emotion_hard_stop"] - EMOTION_GAP)

    # 规则表：clamp 在这里主动收编，不是透传。未知代码丢弃（clamp 本就是在整理
    # 这张表，留着没有对应文案的代码没有意义）；R-DAMAGE 等消费者保护底线强制在
    # 场；任何字段（含缺失代码本身）的空/None/非字符串文案，回退到平台原文而不
    # 是丢弃这条规则——丢弃等于关闭这条规则的展示，是朝不安全方向滑；回退保留
    # 规则仍然生效、只是文案换成默认值，方向和 exceptions_note 的兜底一致。
    raw_rules = fields.get("rules")
    rules = dict(raw_rules) if isinstance(raw_rules, Mapping) else dict(BUILTIN_DEFAULT.rules)
    rules = {code: (body if not _is_blank(body) else BUILTIN_DEFAULT.rules[code])
             for code, body in rules.items() if code in RULE_CODES}
    for code in IMMUTABLE_RULES:                      # 强制在场
        rules.setdefault(code, BUILTIN_DEFAULT.rules[code])

    note = fields.get("exceptions_note")
    note = note.strip() if not _is_blank(note) else BUILTIN_DEFAULT.exceptions_note

    # version 是非负整数快照号。_coerce 已经挡了非数字/None/NaN/inf（含
    # int(inf) 会抛的 OverflowError）；这里只需再夹住负数、显式截断小数。
    v = _coerce(fields.get("version", 0))
    version = max(0, int(v)) if v is not None else 0

    return MerchantConfig(**vals, exceptions_note=note, rules=rules, version=version)
