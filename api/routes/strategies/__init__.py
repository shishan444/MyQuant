"""Strategies 路由包（B5.1 自单文件 strategies.py 拆为五模块）。

对外接口不变：`from api.routes.strategies import router`（app.py 引用零改动）。
"""
from __future__ import annotations

from fastapi import APIRouter

from . import backtest, batch, compare, crud, verify

# 聚合形制（FastAPI 0.137 验证通过的二层层级）：子 router 无前缀，
# prefix/tags 在聚合 include 时统一附加——与原单文件 router 声明等价
router = APIRouter()
for _sub in (crud, backtest, compare, verify, batch):
    router.include_router(_sub.router, prefix="/api/strategies", tags=["strategies"])

# 对外符号 re-export（拆分前的模块级引用面：路由函数/处理器/工具——零改动兼容）
from .backtest import backtest_strategy  # noqa: E402,F401
from .batch import (  # noqa: E402,F401
    _BatchBacktestProcessor,
    batch_backtest_stream,
    get_batch_backtest_detail,
)
from .compare import compare_strategies  # noqa: E402,F401
from .verify import (  # noqa: E402,F401
    _VerifyProcessor,
    compute_verify_star,
    get_session_results,
    get_verify_history,
    list_sessions,
    verify_strategies,
    verify_strategies_stream,
)

__all__ = ["router"]
