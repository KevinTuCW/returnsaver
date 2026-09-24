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
        "R-WINDOW": "自签收之日起 30 天内可申请退货，逾期不再受理。",
        "R-FINAL": "标记为 Final Sale 的清仓商品不支持退换。",
        "R-HYGIENE": "内衣、泳装等贴身类目一经拆封，出于卫生考虑不支持退货。",
        "R-USED": "商品需保持未使用、吊牌完整状态；已明显使用的不支持无理由退货。",
        "R-DAMAGE": "商品到货破损或发错，不受上述限制，可直接换货或退款。",
    },
    "exceptions_note": "质量问题与物流破损不受窗口与拆封限制，随时受理。",
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
    created_at: float = field(default_factory=time.time)


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
    return SESSIONS.get(session_id) if session_id else None


def orders_of(customer_id: str) -> list[dict]:
    return [o for o in ORDERS.values() if o["customer_id"] == customer_id]
