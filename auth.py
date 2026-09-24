"""调用方身份——每个请求被授权的主体。

没有这一层，「多租户隔离」就是自说自话：调用方能在请求体里写任何 tenant_id
读别人的配置。身份只来自凭证，永不来自请求体。

配置（`.env` 里的 `RS_API_KEYS`，JSON）把 key 映射到 "tenant" 或 "tenant:customer"：

    RS_API_KEYS={"sk-dji-alice": "dji:Alice", "sk-dji-ops": "dji"}

未配时为 **dev 模式**：每个请求都当作默认租户，clone && run 仍然成立。
dev 模式是本地便利，不是鉴权绕过——下游的归属校验两种模式下都开着。

刻意与 helpmate 的 auth.py 同构：同一个 tenant 字符串在两个服务里必须指同
一件事，两套不同的解析规则迟早会漂移。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import Header, HTTPException

import config as C


@dataclass(frozen=True)
class Principal:
    """已鉴权的调用方。customer_id=None 表示没有订单数据访问权。"""
    tenant_id: str
    customer_id: Optional[str] = None
    dev_mode: bool = False


def _api_keys() -> dict:
    """独立成函数，测试可 monkeypatch。"""
    return C.API_KEYS


def parse_grant(grant: str) -> tuple[str, Optional[str]]:
    """把配置里的 grant 串拆成 (tenant_id, customer_id)。"""
    tenant, _, customer = grant.partition(":")
    return tenant.strip(), (customer.strip() or None)


def principal_from_key(api_key: Optional[str]) -> Optional[Principal]:
    """把 API key 解析成 Principal。未知 key 返回 None。

    dev 模式（未配任何 key）返回默认身份，让演示语料与 seed 订单不做任何
    配置就能跑通。
    """
    keys = _api_keys()
    if not keys:
        return Principal(tenant_id=C.DEFAULT_TENANT,
                         customer_id=C.DEFAULT_CUSTOMER or None,
                         dev_mode=True)
    if not api_key:
        return None
    grant = keys.get(api_key)
    if grant is None:
        return None
    tenant, customer = parse_grant(grant)
    return Principal(tenant_id=tenant or C.DEFAULT_TENANT, customer_id=customer)


def require_principal(x_api_key: Optional[str] = Header(default=None)) -> Principal:
    """FastAPI 依赖：把 X-API-Key 解析成 Principal 或 401。"""
    p = principal_from_key(x_api_key)
    if p is None:
        raise HTTPException(status_code=401, detail="missing or invalid API key")
    return p
