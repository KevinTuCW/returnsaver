"""四层护栏。核心主张：LLM 有建议权，没有执行权。
L1 在 models.py（收窄动作空间），L2/L3 在这里，L4 是签名 token + 执行层独立复核。"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import uuid

from pydantic import ValidationError

import config as C
from models import Action, CopyProposal
from store import ORDERS


class GuardrailTripped(Exception):
    def __init__(self, layer: str, code: str, detail: str):
        self.layer, self.code, self.detail = layer, code, detail
        super().__init__(f"[{layer}] {code}: {detail}")


# ════════════════════════════════════════════ L2 Schema + 策略校验
def validate_proposal(raw: dict, allowed_ids: set[str]) -> CopyProposal:
    try:
        p = CopyProposal.model_validate(raw)
    except ValidationError as e:
        raise GuardrailTripped("L2", "SCHEMA_INVALID", e.errors()[0]["msg"])

    if p.offer_id not in allowed_ids:
        raise GuardrailTripped(
            "L2", "OFFER_NOT_IN_ALLOWLIST",
            f"LLM 提议 {p.offer_id!r}，本次会话白名单只有 {sorted(allowed_ids)}")
    return p


def validate_value_cap(offer: dict, order: dict) -> None:
    cap = round(order["total"] * C.MAX_DISCOUNT_PCT, 2)
    if offer["value"] > cap:
        raise GuardrailTripped("L2", "VALUE_OVER_CAP",
                               f"{offer['value']} > 上限 {cap}（订单 {order['total']} 的 "
                               f"{int(C.MAX_DISCOUNT_PCT*100)}%）")


# ════════════════════════════════════════════ L3 出参文本扫描
MONEY_RE = re.compile(
    r"(?:[$￥€£]\s?\d[\d,]*(?:\.\d+)?)|(?:\d[\d,]*(?:\.\d+)?\s?(?:%|percent|折|元|美元))")
FORBIDDEN_RE = re.compile(
    r"keep the (?:product|item)|full refund|refund you|double your|"
    r"无需退回|不用退回|不必寄回|全额退款|双倍|直接退款给你",
    re.IGNORECASE)


def scan_output_text(text: str, approved_values: set[str]) -> None:
    m = FORBIDDEN_RE.search(text)
    if m:
        raise GuardrailTripped("L3", "FORBIDDEN_PHRASE", f"文本包含越权承诺：{m.group(0)!r}")
    for mm in MONEY_RE.finditer(text):
        token = mm.group(0).replace(" ", "")
        if token not in approved_values:
            raise GuardrailTripped("L3", "UNAPPROVED_AMOUNT", f"文本出现未批准金额 {token!r}")


# ════════════════════════════════════════════ L4 执行层隔离
def issue_offer_token(order_id: str, offer: dict) -> str:
    payload = {"order_id": order_id, "offer_id": offer["offer_id"],
               "value": float(offer["value"]), "exp": int(time.time()) + C.OFFER_TOKEN_TTL,
               "jti": uuid.uuid4().hex}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    sig = hmac.new(C.SIGNING_KEY, raw, hashlib.sha256).hexdigest()[:32]
    return f"{raw.hex()}.{sig}"


def verify_offer_token(token: str) -> dict:
    try:
        raw_hex, sig = token.split(".", 1)
        raw = bytes.fromhex(raw_hex)
    except ValueError:
        raise GuardrailTripped("L4", "BAD_TOKEN", "token 格式非法")
    expect = hmac.new(C.SIGNING_KEY, raw, hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(expect, sig):
        raise GuardrailTripped("L4", "BAD_SIGNATURE", "token 签名校验失败")
    payload = json.loads(raw)
    if payload["exp"] < time.time():
        raise GuardrailTripped("L4", "TOKEN_EXPIRED", "offer 已过期，请重新发起")
    # 独立复核：不信任上游，自己按策略再算一遍上限
    order = ORDERS.get(payload["order_id"])
    if not order:
        raise GuardrailTripped("L4", "ORDER_NOT_FOUND", "执行层找不到订单")
    if payload["value"] > round(order["total"] * C.MAX_DISCOUNT_PCT, 2):
        raise GuardrailTripped("L4", "VALUE_OVER_CAP", "执行层复核：金额超过硬上限")
    return payload


# ════════════════════════════════════════════ 体验不变量（要求 4）
def assert_experience_invariants(reply: dict, allow_retention: bool) -> None:
    """降退货率不能靠伤体验换。这里把体验约束也做成会抛错的断言。"""
    inv = C.EXPERIENCE_INVARIANTS
    if inv["always_offer_escape_hatch"] and not reply.get("escape_hatch"):
        raise GuardrailTripped("EXP", "NO_ESCAPE_HATCH", "回复未提供放弃挽留的出口")
    if inv["no_retention_when_angry"] and not allow_retention and reply.get("offer"):
        raise GuardrailTripped("EXP", "RETENTION_WHEN_BLOCKED",
                               "该场景禁止挽留，但回复里仍带了挽留 offer")
    # 认 action 而不只认 status：status="declined" 只有模板分支会写，
    # 光看它的话，任何"走了模型的婉拒"都能从这条断言底下溜过去
    is_decline = (reply.get("status") == "declined"
                  or reply.get("action") == Action.DECLINE_WITH_RULES.value)
    if inv["decline_must_cite_rule"] and is_decline and not reply.get("cited_rules"):
        raise GuardrailTripped("EXP", "DECLINE_WITHOUT_RULE", "婉拒未附规则原文")
