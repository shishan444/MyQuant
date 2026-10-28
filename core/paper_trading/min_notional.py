"""MIN_NOTIONAL 装载单点（change paper-depletion-terminal 任务 1）。

三来源优先级：环境变量 FAPI_MIN_NOTIONAL_{SYMBOL}（change-1 已建
解析面 fapi_min_notional_overrides）> 启动端点拉取（fetch_exchange_
info——change-1 建）> constants.MIN_NOTIONAL_BUILTIN 内置回退
（实施期真实核实值，核实记录随 constants 注释留档）。

装载产物=模块级表 MIN_NOTIONAL（kernel 拦截判定单点读——无运行期
网络调用；缺省形态=内置回退值副本，未装载/装载失败即保持，任务
行为不中断）。运行中不刷新（重启即更新——spec「取值三态」句）。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from core.data.fapi.stream import FAPI_SYMBOLS
from core.paper_trading.constants import MIN_NOTIONAL_BUILTIN

logger = logging.getLogger("paper_runtime")

# 装载目标表（模块级单点：app lifespan 启动装载写回；kernel 拦截
# 判定读。缺省=内置回退值副本——装载链任何失败形态下表保持可用）
MIN_NOTIONAL: Dict[str, float] = dict(MIN_NOTIONAL_BUILTIN)


def extract_min_notional(exchange_info: dict, symbol: str) -> Optional[float]:
    """exchangeInfo → 指定币种 MIN_NOTIONAL（纯 dict 解析，change-4
    新写——extract_precision 形制参考不跨用）。

    Returns:
        float | None: filters MIN_NOTIONAL.notional 值；symbol 不在或
        缺 filter 返回 None（调用方回退内置）。
    """
    for s in exchange_info.get("symbols", []):
        if s.get("symbol") != symbol:
            continue
        for f in s.get("filters", []):
            if f.get("filterType") == "MIN_NOTIONAL":
                return float(f["notional"])
        return None
    return None


def load_min_notional(
    fetch_exchange_info: Optional[Callable[[], dict]] = None,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, float]:
    """启动装载（三来源合并写回 MIN_NOTIONAL 表）。

    Args:
        fetch_exchange_info: 端点拉取注入面（缺省经 FapiRestClient
            真实拉取；测试注入替身）。拉取异常→warning 留痕+回退
            内置（spec Scenario「规则端点失败回退」——任务行为
            不中断）。
        env: 环境变量源（缺省 os.environ——测试注入 dict）。

    Returns:
        装载后的表（与模块级 MIN_NOTIONAL 同对象）。
    """
    from core.data.fapi.rest import fapi_min_notional_overrides

    table: Dict[str, Any] = {}
    # 端点源（失败回退内置——逐键缺省兜底）
    fetched: Dict[str, float] = {}
    fetch = fetch_exchange_info
    if fetch is None:
        def fetch() -> dict:
            from core.data.fapi.rest import FapiRestClient

            return FapiRestClient().fetch_exchange_info()
    try:
        info = fetch() or {}
        for symbol in FAPI_SYMBOLS:
            value = extract_min_notional(info, symbol)
            if value is not None:
                fetched[symbol] = value
        logger.info(
            "MIN_NOTIONAL 启动装载（端点拉取）: %s", fetched)
    except Exception as exc:
        logger.warning(
            "MIN_NOTIONAL 端点拉取失败（%s: %s）——回退内置常量 %s"
            "继续判定（任务行为不中断）",
            type(exc).__name__, exc, MIN_NOTIONAL_BUILTIN)
    for symbol in FAPI_SYMBOLS:
        # 优先级：env 覆写 > 端点 > 内置
        table[symbol] = (
            fetched.get(symbol, MIN_NOTIONAL_BUILTIN.get(symbol)))
    overrides = fapi_min_notional_overrides(env)
    table.update(overrides)
    MIN_NOTIONAL.clear()
    MIN_NOTIONAL.update(table)
    if overrides:
        logger.info("MIN_NOTIONAL 环境变量覆写生效: %s", overrides)
    return MIN_NOTIONAL
