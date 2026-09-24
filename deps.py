"""每请求的依赖装配。与 ShopScout 的 FunnelDeps 同构。

为什么是一个对象而不是二十个参数：阈值有 11 个，穿过 policy/llm/guardrails 的
四个模块。显式传每个值会让 build_resolution 变成 15 个参数；用 ContextVar 则
会在跨线程/流式边界丢 context——helpmate 已经为这个坑把流式路径写成了非生成器
（见其 app.py:208-222 的 docstring）。

RetentionDeps 就是「这段会话钉住的那一版配置」，所以快照语义是它的属性，
而不是每个函数都要记得去查的东西。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import config as C
from merchant import BUILTIN_DEFAULT, MerchantConfig


@dataclass(frozen=True)
class RetentionDeps:
    """一次请求内不变的一切。frozen：快照被下游改掉就不是快照了。"""
    config: MerchantConfig
    config_version: int
    tenant_id: str
    cost_budget_usd: float
    # 数据访问器。注入而非 import，让测试能喂假数据。
    get_order: Optional[Callable[[str], Optional[dict]]] = None
    get_orders_of: Optional[Callable[[str], list[dict]]] = None
    get_customer: Optional[Callable[[str], Optional[dict]]] = None
    # 配置是否来自降级路径（库不可用 / 租户无配置行）
    degraded: bool = False


def build_default() -> RetentionDeps:
    """内置默认配置的 deps。memory 模式、库不可用、以及全部 hermetic 测试走这条。"""
    import store
    return RetentionDeps(
        config=BUILTIN_DEFAULT,
        config_version=0,
        tenant_id=C.DEFAULT_TENANT,
        cost_budget_usd=C.CONVERSATION_COST_BUDGET_USD,
        get_order=lambda oid: store.ORDERS.get(oid),
        get_orders_of=lambda cid: store.orders_of(cid),
        get_customer=lambda cid: store.CUSTOMERS.get(cid),
        degraded=False,
    )


def build(principal, session) -> RetentionDeps:
    """按租户构造，配置版本取自会话。

    版本从 **session** 取而不是取租户当前版本——这就是快照语义的落点：商家
    中途发了新版本，进行中的会话仍按它开始时钉住的那一版结算。
    """
    import db
    import merchant_store as MS
    import store

    cfg, degraded = MS.load(principal.tenant_id, session.config_version)
    if db.enabled():
        t, c = principal.tenant_id, principal.customer_id
        get_order = lambda oid: db.fetch_order(oid, tenant_id=t, customer_id=c)   # noqa: E731
        get_orders_of = lambda cid: db.fetch_orders_of(t, cid)                    # noqa: E731
        get_customer = lambda cid: db.fetch_customer(t, cid)                      # noqa: E731
    else:
        # memory 模式：mock 语料。零配置演示与 hermetic 测试走这条
        get_order = lambda oid: store.ORDERS.get(oid)                             # noqa: E731
        get_orders_of = lambda cid: store.orders_of(cid)                          # noqa: E731
        get_customer = lambda cid: store.CUSTOMERS.get(cid)                       # noqa: E731
    return RetentionDeps(
        config=cfg,
        config_version=cfg.version,
        tenant_id=principal.tenant_id,
        # 成本预算读实时值、不进快照：没有任何 offer_token 依赖它，
        # 为它加一套不可变机制是纯开销
        cost_budget_usd=MS.cost_budget(principal.tenant_id),
        get_order=get_order,
        get_orders_of=get_orders_of,
        get_customer=get_customer,
        degraded=degraded,
    )
