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

import hashlib
import hmac
import re
import time
from dataclasses import dataclass
from typing import Optional

from fastapi import Header, HTTPException, Query

import config as C

TENANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


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


def _valid_tenant(value: str) -> str:
    if not TENANT_RE.fullmatch(value):
        raise HTTPException(status_code=422, detail="invalid tenant_id")
    return value


def admin_signature(tenant_id: str, expires: int) -> str:
    """Sign a tenant-scoped Admin URL without exposing an API key."""
    message = f"admin:{tenant_id}:{expires}".encode()
    return hmac.new(C.SIGNING_KEY, message, hashlib.sha256).hexdigest()


def require_admin_tenant(
        tenant_id: str = Query(default=C.DEFAULT_TENANT),
        expires: int | None = Query(default=None),
        sig: str | None = Query(default=None)) -> Principal:
    """Resolve Admin tenant context from a signed URL.

    The default tenant remains frictionless only in keyless local development.
    Every other context needs an expiring signature, so a tenant id never acts
    as an authentication credential by itself.
    """
    tenant_id = _valid_tenant(tenant_id)
    local_default = not _api_keys() and tenant_id == C.DEFAULT_TENANT
    if not local_default:
        if expires is None or sig is None or expires < int(time.time()):
            raise HTTPException(status_code=401, detail="missing or expired admin URL signature")
        expected = admin_signature(tenant_id, expires)
        if not hmac.compare_digest(expected, sig):
            raise HTTPException(status_code=403, detail="invalid admin URL signature")
    return Principal(tenant_id=tenant_id, dev_mode=local_default)
