"""商家配置的持久化。领域模型与校验在 merchant.py。

三条降级原则：
  1. 库连不上 → 内置默认 + 标记 degraded + 大声打日志。配置读取永不 500。
  2. 租户没有配置行 → 内置默认，且**这不算错误**（新租户就是这个状态）。
     把它标成 degraded 会让 /health 长期亮红灯，真故障就淹没在噪音里。
  3. 库里的行越界 → 逐字段 clamp，不整行回退（一个字段填错不该让商家丢掉
     其余所有设置）。

为什么这里不写第二份数值解析：merchant._coerce 已经是 validate 与 clamp 共用的
唯一入口，多一份就多一处漂移。本模块只做「取行 → 交给 merchant 裁决」。
"""
from __future__ import annotations

import logging

import config as C
import merchant as M

log = logging.getLogger(__name__)

# (tenant_id, version) → MerchantConfig。
# 可以永久缓存：append-only 让这个键不可变，这是选 append-only 的附带红利。
_CACHE: dict[tuple[str, int], M.MerchantConfig] = {}


def cache_clear() -> None:
    _CACHE.clear()


# ──────────────────────────────────────────── 库层间接层（测试可 monkeypatch）
def _enabled() -> bool:
    import db
    return db.enabled()


def _fetch_config(tenant_id: str, version: int | None) -> dict | None:
    import db
    return db.fetch_merchant_config(tenant_id, version)


def _fetch_tenant(tenant_id: str) -> dict | None:
    import db
    return db.fetch_tenant(tenant_id)


def _insert_config(tenant_id: str, fields: dict, *, created_by: str,
                   note: str | None) -> int:
    import db
    return db.insert_merchant_config(tenant_id, fields, created_by=created_by,
                                     note=note)


# ──────────────────────────────────────────── 读
def current_version(tenant_id: str) -> int:
    """该租户当前版本；无配置或库不可用时返回 0（= 内置默认）。"""
    if not _enabled():
        return 0
    try:
        row = _fetch_config(tenant_id, None)
    except Exception as e:
        log.warning("CONFIG_VERSION_LOOKUP_FAILED tenant=%s %s: %s",
                    tenant_id, type(e).__name__, e)
        return 0
    return int(row["version"]) if row else 0


def load(tenant_id: str, version: int | None) -> tuple[M.MerchantConfig, bool]:
    """返回 (配置, 是否降级)。version=0 或无库 → 内置默认。"""
    if not _enabled():
        return M.BUILTIN_DEFAULT, False
    if version == 0:
        # 会话钉的就是「内置默认」这一版，尊重它，不要悄悄升到库里的最新版
        return M.BUILTIN_DEFAULT, False

    if version is not None:
        hit = _CACHE.get((tenant_id, version))
        if hit is not None:
            return hit, False

    try:
        row = _fetch_config(tenant_id, version)
    except Exception as e:
        log.error("CONFIG_LOAD_FAILED tenant=%s version=%s %s: %s — "
                  "回退内置默认继续服务", tenant_id, version, type(e).__name__, e)
        return M.BUILTIN_DEFAULT, True

    if row is None:
        if version is not None:
            # 钉住的版本不存在。append-only 下不该发生，回退当前版本并告警。
            log.error("CONFIG_VERSION_MISSING tenant=%s version=%s — 回退最新版本",
                      tenant_id, version)
            return load(tenant_id, None)
        return M.BUILTIN_DEFAULT, False        # 新租户，不算错误

    violations = M.validate(row)
    if violations:
        log.error("CONFIG_OUT_OF_BOUNDS tenant=%s version=%s violations=%s — 逐字段 clamp",
                  tenant_id, row.get("version"),
                  [(v.field, v.detail) for v in violations])
        _score_clamp(tenant_id, violations)

    cfg = M.clamp(row)
    _CACHE[(tenant_id, cfg.version)] = cfg
    return cfg, False


def cost_budget(tenant_id: str) -> float:
    """随套餐下发的成本预算。读实时值——不进会话快照。

    快照语义是为了保护面向客户的承诺；没有任何 offer_token 依赖成本预算，
    为它加一套不可变机制是纯开销。
    """
    if not _enabled():
        return C.CONVERSATION_COST_BUDGET_USD
    try:
        row = _fetch_tenant(tenant_id)
    except Exception as e:
        log.warning("TENANT_LOOKUP_FAILED tenant=%s %s: %s",
                    tenant_id, type(e).__name__, e)
        return C.CONVERSATION_COST_BUDGET_USD
    if not row:
        return C.CONVERSATION_COST_BUDGET_USD
    return float(row["cost_budget_usd"])


# ──────────────────────────────────────────── 写
def save(tenant_id: str, fields: dict, *, created_by: str,
         note: str | None = None) -> int:
    """校验 → 插入 version+1。返回新版本号。校验失败抛 ValueError。

    校验在**碰库之前**：坏配置永不进入任何会话，这是写入主闸。读取时的
    clamp 是纵深防御，防的是绕过本函数直接 psql 插的行。
    """
    violations = M.validate(fields)
    if violations:
        raise ValueError("; ".join(f"{v.field}: {v.detail}" for v in violations))
    version = _insert_config(tenant_id, fields, created_by=created_by, note=note)
    return version


def _score_clamp(tenant_id: str, violations: list[M.Violation]) -> None:
    """越界配置要在 Langfuse 留痕——这是运维需要看见的事。"""
    try:
        import observability as obs
        obs.score(f"config:{tenant_id}", "config-out-of-bounds", True,
                  data_type="BOOLEAN",
                  comment=",".join(v.field for v in violations))
    except Exception:
        pass          # 埋点绝不能拖垮主流程
