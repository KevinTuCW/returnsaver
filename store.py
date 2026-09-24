"""数据层。MVP 用内存 mock；上线换 Supabase(Postgres)，接口签名不变。"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from models import Stage

# ──────────────────────────────────────────── 商家售后规则（Task 2 要求硬编码，不接真 Shopify）
# 每条规则带 code + 可展示原文，婉拒时必须引用（体验护栏：decline_must_cite_rule）
MERCHANT_POLICY = {
    "merchant_id": "M-DEMO",
    "return_window_days": 30,
    "rules": {
        "R-WINDOW": "Returns are accepted within 30 days of delivery.",
        "R-FINAL": "Items marked Final Sale are not eligible for return or exchange.",
        "R-HYGIENE": "Opened intimate apparel and swimwear cannot be returned for hygiene reasons.",
        "R-USED": "Items must be unused and retain all original tags to qualify for a discretionary return.",
        "R-DAMAGE": "Damaged or incorrectly shipped items qualify for replacement or refund regardless of the standard restrictions.",
    },
    "exceptions_note": "Quality issues and shipping damage are exempt from the return window and opened-item restrictions.",
    "auto_refund": {
        "enabled": False,
        "min_order_amount_usd": 0.0,
        "max_order_amount_usd": 50.0,
        "minimum_prior_attempts": 1,
        "keep_item_below_usd": 20.0,
        "execution_mode": "manual_review",
    },
}

# ──────────────────────────────────────────── Mock 数据：覆盖全部五个场景
CUSTOMERS = {
    "C-001": {"customer_id": "C-001", "name": "Sarah", "tier": "vip",
              "lifetime_orders": 11, "returns_last_90d": 0, "risk_flag": False},
    "C-002": {"customer_id": "C-002", "name": "Mike", "tier": "normal",
              "lifetime_orders": 2, "returns_last_90d": 0, "risk_flag": False},
    "C-003": {"customer_id": "C-003", "name": "Elena", "tier": "normal",
              "lifetime_orders": 4, "returns_last_90d": 1, "risk_flag": False},
    "C-004": {"customer_id": "C-004", "name": "Raj", "tier": "normal",
              "lifetime_orders": 1, "returns_last_90d": 0, "risk_flag": False},
    "C-005": {"customer_id": "C-005", "name": "Nina", "tier": "normal",
              "lifetime_orders": 3, "returns_last_90d": 0, "risk_flag": False},
    "C-006": {"customer_id": "C-006", "name": "Tom", "tier": "vip",
              "lifetime_orders": 8, "returns_last_90d": 0, "risk_flag": False},
    "C-007": {"customer_id": "C-007", "name": "Alex", "tier": "normal",
              "lifetime_orders": 20, "returns_last_90d": 6, "risk_flag": True},  # 薅羊毛
    "C-008": {"customer_id": "C-008", "name": "Maya", "tier": "normal",
              "lifetime_orders": 5, "returns_last_90d": 0, "risk_flag": False},
    "C-009": {"customer_id": "C-009", "name": "Jordan", "tier": "vip",
              "lifetime_orders": 14, "returns_last_90d": 1, "risk_flag": False},
    "C-010": {"customer_id": "C-010", "name": "Leo", "tier": "normal",
              "lifetime_orders": 3, "returns_last_90d": 0, "risk_flag": False},
}

ORDERS = {
    "ORD-1001": {  # 尺码不合 → VALUE_GAP / 换货
        "order_id": "ORD-1001", "customer_id": "C-001", "sku": "TEE-BLK-M",
        "product": "Merino Crew Tee (Black / M)", "category": "apparel",
        "total": 100.00, "gross_margin_pct": 0.60, "days_since_delivery": 6,
        "final_sale": False, "opened": True, "used": False,
        "sizes_in_stock": ["S", "L", "XL"], "repairable": False,
        "negotiations_last_90d": 0, "has_manual": True,
    },
    "ORD-1002": {  # 破损 → PRODUCT_DAMAGE
        "order_id": "ORD-1002", "customer_id": "C-002", "sku": "MUG-CER-01",
        "product": "Ceramic Mug", "category": "home",
        "total": 38.00, "gross_margin_pct": 0.55, "days_since_delivery": 2,
        "final_sale": False, "opened": True, "used": False,
        "sizes_in_stock": [], "repairable": False,
        "negotiations_last_90d": 0, "has_manual": False,
    },
    "ORD-1003": {  # 超窗口 → NOT_ELIGIBLE
        "order_id": "ORD-1003", "customer_id": "C-003", "sku": "JKT-DWN-L",
        "product": "Down Jacket (L)", "category": "apparel",
        "total": 420.00, "gross_margin_pct": 0.58, "days_since_delivery": 41,
        "final_sale": False, "opened": True, "used": True,
        "sizes_in_stock": ["M", "L"], "repairable": True,
        "negotiations_last_90d": 0, "has_manual": True,
    },
    "ORD-1004": {  # 不会用 → USAGE_ISSUE
        "order_id": "ORD-1004", "customer_id": "C-004", "sku": "HDP-ANC-02",
        "product": "ANC Headphones Gen2", "category": "electronics",
        "total": 260.00, "gross_margin_pct": 0.45, "days_since_delivery": 5,
        "final_sale": False, "opened": True, "used": True,
        "sizes_in_stock": [], "repairable": True,
        "negotiations_last_90d": 0, "has_manual": True,
    },
    "ORD-1005": {  # 情绪激烈 + 小额 → INSTANT_REFUND
        "order_id": "ORD-1005", "customer_id": "C-005", "sku": "LMP-DSK-03",
        "product": "Desk Lamp", "category": "home",
        "total": 45.00, "gross_margin_pct": 0.50, "days_since_delivery": 3,
        "final_sale": False, "opened": True, "used": True,
        "sizes_in_stock": [], "repairable": True,
        "negotiations_last_90d": 0, "has_manual": True,
    },
    "ORD-1006": {  # 情绪激烈 + 大额 → ESCALATE_HUMAN
        "order_id": "ORD-1006", "customer_id": "C-006", "sku": "CAM-MRL-01",
        "product": "Mirrorless Camera Body", "category": "electronics",
        "total": 680.00, "gross_margin_pct": 0.32, "days_since_delivery": 4,
        "final_sale": False, "opened": True, "used": True,
        "sizes_in_stock": [], "repairable": True,
        "negotiations_last_90d": 0, "has_manual": True,
    },
    "ORD-1007": {  # Final sale + 薅羊毛用户 → NOT_ELIGIBLE
        "order_id": "ORD-1007", "customer_id": "C-007", "sku": "SNK-RUN-42",
        "product": "Runner Sneakers (42)", "category": "footwear",
        "total": 90.00, "gross_margin_pct": 0.52, "days_since_delivery": 7,
        "final_sale": True, "opened": True, "used": True,
        "sizes_in_stock": ["41", "43"], "repairable": False,
        "negotiations_last_90d": 4, "has_manual": False,
    },
    "ORD-1008": {  # 完全合规 + 薅羊毛用户 → 冷静期，放行标准退货不再给券
        "order_id": "ORD-1008", "customer_id": "C-007", "sku": "TEE-BLK-M",
        "product": "Merino Crew Tee (Black / S)", "category": "apparel",
        "total": 100.00, "gross_margin_pct": 0.60, "days_since_delivery": 5,
        "final_sale": False, "opened": True, "used": False,
        "sizes_in_stock": ["M", "L"], "repairable": False,
        "negotiations_last_90d": 4, "has_manual": True,
    },
    "ORD-1009": {
        "order_id": "ORD-1009", "customer_id": "C-008", "sku": "BAG-TOTE-01",
        "product": "Canvas Everyday Tote", "category": "accessories",
        "total": 72.00, "gross_margin_pct": 0.62, "days_since_delivery": 8,
        "final_sale": False, "opened": True, "used": False,
        "sizes_in_stock": [], "repairable": False,
        "negotiations_last_90d": 0, "has_manual": True,
    },
    "ORD-1010": {
        "order_id": "ORD-1010", "customer_id": "C-009", "sku": "KTL-ELC-01",
        "product": "Smart Temperature Kettle", "category": "home",
        "total": 128.00, "gross_margin_pct": 0.48, "days_since_delivery": 4,
        "final_sale": False, "opened": True, "used": True,
        "sizes_in_stock": [], "repairable": True,
        "negotiations_last_90d": 0, "has_manual": True,
    },
    "ORD-1011": {
        "order_id": "ORD-1011", "customer_id": "C-010", "sku": "SNK-WALK-43",
        "product": "City Walker Sneakers (43)", "category": "footwear",
        "total": 115.00, "gross_margin_pct": 0.57, "days_since_delivery": 5,
        "final_sale": False, "opened": True, "used": False,
        "sizes_in_stock": ["42", "44"], "repairable": False,
        "negotiations_last_90d": 0, "has_manual": True,
    },
}

# 使用类问题的知识库（USAGE_ISSUE 场景走这里，命中即可零 LLM 成本回答）
KNOWLEDGE_BASE = {
    "HDP-ANC-02": {
        "title": "ANC Headphones Gen2 配对与降噪调校",
        "manual_url": "https://help.example.com/hdp-anc-02/manual",
        "video_url": "https://help.example.com/hdp-anc-02/setup-90s",
        "tips": ["长按右耳 3 秒进入配对模式，指示灯闪白即可被手机发现",
                 "App 内「耳道适配测试」跑一次，降噪深度平均提升 40%",
                 "耳塞尺寸换 L 号可解决大部分漏音导致的「降噪没感觉」"],
    },
    "LMP-DSK-03": {
        "title": "Desk Lamp 亮度与色温调节",
        "manual_url": "https://help.example.com/lmp-dsk-03/manual",
        "video_url": None,
        "tips": ["底座触控条长按切换色温，短按调亮度", "闪烁多为适配器未插紧"],
    },
    "TEE-BLK-M": {
        "title": "Merino 羊毛衫洗护与尺码",
        "manual_url": "https://help.example.com/tee-blk-m/care",
        "video_url": None,
        "tips": ["首次冷水手洗可避免缩水", "肩宽偏紧建议上一个码"],
    },
    "JKT-DWN-L": {
        "title": "羽绒服蓬松度恢复与维修",
        "manual_url": "https://help.example.com/jkt-dwn-l/care",
        "video_url": None,
        "tips": ["低温烘干加两颗烘干球可恢复蓬松", "拉链头损坏可走免费维修"],
    },
}


# ──────────────────────────────────────────── 会话状态
@dataclass
class Session:
    session_id: str
    customer_id: str | None = None
    tenant_id: str = "public"
    # 会话开始时钉住的配置版本，0 = 内置默认。钉住了就不再变：商家中途改配置
    # 不该让同一段对话前后两轮按不同规则结算。
    config_version: int = 0
    order_id: str | None = None
    stage: Stage = Stage.INTENT
    round: int = 0      # 已发出的挽留轮次
    turns: int = 0      # 总交互次数（安全上限）
    intent: str | None = None
    reason: str | None = None
    emotion: float = 0.0
    scenario: str | None = None
    llm_cost_usd: float = 0.0
    model_calls: list[dict] = field(default_factory=list)
    guardrail_trips: list[dict] = field(default_factory=list)
    candidate_orders: list[str] = field(default_factory=list)
    outcome: str | None = None
    language: str = "zh"
    created_at: float = field(default_factory=time.time)
    last_activity_at: float = field(default_factory=time.time)
    messages: list[dict] = field(default_factory=list)


SESSIONS: dict[str, Session] = {}
EXECUTED: dict[str, dict] = {}        # 幂等表
MANUAL_QUEUE: list[dict] = []         # 人工介入工单


def new_session(customer_id: str | None, tenant_id: str = "public",
                config_version: int = 0) -> Session:
    s = Session(session_id=f"S-{uuid.uuid4().hex[:12]}", customer_id=customer_id,
                tenant_id=tenant_id, config_version=config_version)
    SESSIONS[s.session_id] = s
    return s


def persist(s: Session) -> None:
    """双写：内存永远是真相源，Postgres 用于重启恢复与离线分析。
    backend=memory 时整个函数是 no-op。"""
    import db
    db.save_session(s)


def get_session(session_id: str | None) -> Session | None:
    if not session_id:
        return None
    session = SESSIONS.get(session_id)
    if session is not None:
        return session
    import db
    row = db.load_session(session_id)
    if not row:
        return None
    session = session_from_row(row)
    SESSIONS[session.session_id] = session
    return session


def session_from_row(row: dict) -> Session:
    def timestamp(value, fallback: float) -> float:
        return value.timestamp() if hasattr(value, "timestamp") else fallback

    now = time.time()
    return Session(
        session_id=row["session_id"], customer_id=row.get("customer_id"),
        tenant_id=row.get("tenant_id") or "public",
        config_version=int(row.get("config_version") or 0),
        order_id=row.get("order_id"), stage=Stage(row["stage"]),
        round=int(row.get("round") or 0), turns=int(row.get("turns") or 0),
        intent=row.get("intent"), reason=row.get("reason"),
        emotion=float(row.get("emotion") or 0), scenario=row.get("scenario"),
        llm_cost_usd=float(row.get("llm_cost_usd") or 0),
        model_calls=list(row.get("model_calls") or []),
        guardrail_trips=list(row.get("guardrail_trips") or []),
        outcome=row.get("outcome"), language=row.get("language") or "zh",
        created_at=timestamp(row.get("created_at"), now),
        last_activity_at=timestamp(row.get("last_activity_at"), now),
        messages=list(row.get("messages") or []),
    )


def all_sessions(tenant_id: str | None = None) -> list[Session]:
    import db
    merged = dict(SESSIONS)
    for row in db.list_sessions(tenant_id=tenant_id):
        merged.setdefault(row["session_id"], session_from_row(row))
    rows = list(merged.values())
    if tenant_id:
        rows = [s for s in rows if s.tenant_id == tenant_id]
    return sorted(rows, key=lambda s: s.created_at, reverse=True)


def orders_of(customer_id: str) -> list[dict]:
    return [o for o in ORDERS.values() if o["customer_id"] == customer_id]
