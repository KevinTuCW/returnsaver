"""测试环境隔离。

必须在 import config 之前清空凭证：config 在模块导入时读取环境变量，
而 `load_dotenv` 不覆盖已存在的环境变量，所以这里设空字符串就能压过 .env。

理由：单元/e2e 测试不能依赖网络。带真 key 跑会变成 26 次真实 API 调用，
既慢又贵，还会因为模型抖动产生假失败。真实链路的验证走 `scripts/verify_live.py`。
"""
from __future__ import annotations

import os

import pytest

os.environ["RS_LLM_API_KEY"] = ""
os.environ["GLM_API_KEY"] = ""
os.environ["LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LANGFUSE_SECRET_KEY"] = ""
os.environ["RS_STORE_BACKEND"] = "memory"
os.environ["RS_HOLDOUT_PCT"] = "0"


@pytest.fixture(autouse=True)
def _isolate_in_memory_state():
    """Each test gets a clean process-local store, like a fresh request suite."""
    import merchant_store
    import store

    store.SESSIONS.clear()
    store.EXECUTED.clear()
    store.MANUAL_QUEUE.clear()
    store.POLICY_EXTRAS.clear()
    merchant_store._MEMORY_CONFIGS.clear()
    merchant_store.cache_clear()
    yield
