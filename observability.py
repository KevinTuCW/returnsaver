"""Langfuse 可观测性接入。

设计约束：**埋点绝不能拖垮主流程**。没配 key、SDK 没装、网络不通，
全部降级成 no-op，业务逻辑一行都不变。

用法：
    with obs.session_trace(session_id, customer_id) as t:
        ...
        obs.score(session_id, "guardrail-trip", 1, data_type="BOOLEAN")
"""
from __future__ import annotations

from contextlib import contextmanager

import config as C

_client = None
_enabled = False

if C.USE_LANGFUSE:
    try:
        from langfuse import Langfuse

        # 必须显式构造一次，把凭证注册进 SDK 的客户端表。只调 get_client() 拿不到，
        # langfuse.openai 的 drop-in 会报 "No Langfuse client ... has been initialized"
        # 并静默跳过整条 trace。
        _client = Langfuse(
            public_key=C.LANGFUSE_PUBLIC_KEY,
            secret_key=C.LANGFUSE_SECRET_KEY,
            base_url=C.LANGFUSE_BASE_URL,
            environment=C.LANGFUSE_ENVIRONMENT,
            sample_rate=C.LANGFUSE_SAMPLE_RATE,
        )
        if not _client.auth_check():
            raise RuntimeError("auth_check 失败：public/secret key 或 base_url 不对")
        _enabled = True
    except Exception as e:      # SDK 缺失 / 鉴权失败 / 网络不通
        print(f"LANGFUSE_DISABLED reason={type(e).__name__}: {e}")
        _client, _enabled = None, False


def enabled() -> bool:
    return _enabled


def openai_client():
    """开了 Langfuse 就返回带 tracing 的 drop-in OpenAI 客户端，否则返回原生的。
    两者 API 完全一致，调用方无感。"""
    if _enabled:
        try:
            from langfuse.openai import OpenAI as TracedOpenAI
            return TracedOpenAI(api_key=C.LLM_API_KEY, base_url=C.LLM_BASE_URL)
        except Exception:
            pass
    from openai import OpenAI
    return OpenAI(api_key=C.LLM_API_KEY, base_url=C.LLM_BASE_URL)


@contextmanager
def session_trace(session_id: str, customer_id: str | None, message: str):
    """一次 /api/negotiate 调用 = 一个 span，session_id 串起整段对话。"""
    if not _enabled:
        yield None
        return
    try:
        with _client.start_as_current_observation(
                as_type="span", name="return-saver.negotiate",
                input={"message": message}) as span:
            try:
                from langfuse import propagate_attributes
                with propagate_attributes(user_id=customer_id or "anonymous",
                                          session_id=session_id,
                                          tags=[C.LANGFUSE_ENVIRONMENT]):
                    yield span
            except ImportError:
                yield span
    except Exception as e:
        print(f"LANGFUSE_TRACE_FAILED {type(e).__name__}: {e}")
        yield None


def update(span, **kw) -> None:
    """给当前 span 补上业务维度：场景、动作、路由档位、成本。"""
    if not _enabled or span is None:
        return
    try:
        span.update(output=kw.get("output"), metadata=kw.get("metadata"))
    except Exception:
        pass


def score(session_id: str, name: str, value, *, data_type: str = "NUMERIC",
          comment: str | None = None) -> None:
    """打分。命名按「信号来源」而非「期望衡量的东西」（Langfuse 最佳实践）。
    本项目用到的分数名：
      user-csat          NUMERIC     会话结束后用户评分 1-5
      guardrail-trip     BOOLEAN     本次会话是否触发过护栏
      experience-violation BOOLEAN   是否违反体验不变量
      retention-outcome  CATEGORICAL deflected | refunded | escalated | released
    """
    if not _enabled:
        return
    try:
        _client.create_score(name=name, value=value, session_id=session_id,
                             data_type=data_type, comment=comment)
    except Exception as e:
        print(f"LANGFUSE_SCORE_FAILED name={name} {type(e).__name__}: {e}")


def flush() -> None:
    """进程退出前把缓冲里的 trace 刷出去，否则短生命周期容器会丢数据。"""
    if _enabled and _client:
        try:
            _client.flush()
        except Exception:
            pass
