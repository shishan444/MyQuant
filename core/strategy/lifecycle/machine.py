"""策略生命周期状态机常量基座（build-strategy-lifecycle 1.1）。

单源纪律：六态值域/迁移矩阵/trigger 枚举/人工边封闭集/水位序全部
在本模块声明，消费方（service/credential/守卫断言/测试）只 import
不复制——沿 PT_STATUS_PREDECESSORS「常量+派生视图」先例（repository/
paper.py:50）。零 IO 零第三方依赖（纯常量模块，可被 alembic 与测试
无副作用 import）。
"""
from __future__ import annotations

from typing import Any, Dict, FrozenSet, Tuple

# 六态值域（strategy.status 列值域；三态旧值原义保留：draft=假设
# 转化草稿（验证对象），active=可消费，archived=归档；新三态：
# paper=纸盘在验，candidate=达标候选，live=实盘在役）
STRATEGY_STATUSES: frozenset = frozenset({
    "draft", "active", "paper", "candidate", "live", "archived",
})

# 迁移矩阵封闭集（11 对逐对清单；同态对不入矩阵=not_migrated 域）：
# 七条单列边+「任意→archived」四 from 展开（live→archived 已单列，
# 展开不重复）。len 计数锚：len(TRANSITION_MATRIX)==11 断言入测试
# （多写/漏写/重复必红）
_TRANSITION_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("draft", "active"),        # 转正（研究链 promote）
    ("active", "paper"),        # 上盘
    ("paper", "active"),        # 纸盘任务结束回流
    ("paper", "candidate"),     # 达标（预留，凭证签发归后续 change）
    ("candidate", "live"),      # 实盘晋升（预留，人工唯一必经）
    ("live", "archived"),       # 退役（人工）
    ("archived", "draft"),      # 重启重验
    ("draft", "archived"),      # 归档（证伪自动+人工）
    ("active", "archived"),     # 归档（人工）
    ("paper", "archived"),      # 归档（人工）
    ("candidate", "archived"),  # 归档（人工）
)
TRANSITION_MATRIX: FrozenSet[Tuple[str, str]] = frozenset(_TRANSITION_PAIRS)

# 触发枚举（九值；与矩阵边的挂钩见 TRIGGER_EDGES——映射完备性断言
# 入测试：矩阵每边至少被一个 trigger 覆盖）
TRIGGERS: frozenset = frozenset({
    "credential", "mount", "task_end", "qualify",
    "manual_promote", "manual_archive", "retire", "revalidate", "refute",
})

# trigger → 合法边集。refute 边集只有 (draft, archived)：refuted 时
# 谱系恒 draft 或不存在（无「转正后事后证伪」路径），「任意→archived」
# 的完整 from 覆盖由 manual_archive/retire 承载。
TRIGGER_EDGES: Dict[str, frozenset] = {
    "credential": frozenset({("draft", "active")}),
    "mount": frozenset({("active", "paper")}),
    "task_end": frozenset({("paper", "active")}),
    "qualify": frozenset({("paper", "candidate")}),
    "manual_promote": frozenset({("candidate", "live")}),
    "retire": frozenset({("live", "archived")}),
    "manual_archive": frozenset({
        ("draft", "archived"), ("active", "archived"), ("paper", "archived"),
        ("candidate", "archived"), ("live", "archived"),
    }),
    "revalidate": frozenset({("archived", "draft")}),
    "refute": frozenset({("draft", "archived")}),
}

# 人工 trigger 封闭集+人工边封闭集（REQ-1-02 人工点封闭列举：
# candidate→live/人工归档/live→archived/archived→draft 全部走
# transit——集合外的状态变更点不存在）
MANUAL_TRIGGERS: frozenset = frozenset({
    "manual_promote", "manual_archive", "retire", "revalidate",
})
MANUAL_EDGES: frozenset = frozenset().union(
    *(TRIGGER_EDGES[t] for t in MANUAL_TRIGGERS))

# 合法后继集派生视图（by-from 索引——service 边校验与 409 detail
# 「合法后继集」消费；数据单源在矩阵，两视图不可能漂移）
SUCCESSORS: Dict[str, frozenset] = {
    status: frozenset(t for f, t in TRANSITION_MATRIX if f == status)
    for status in STRATEGY_STATUSES
}

# 水位序（五值；archived 不入序——水位语义=曾达到的最高执行阶，
# →archived 事件不代表阶；archived 行推导=事件表 max，无事件=0）
WATERLINE_ORDER: Dict[str, int] = {
    "draft": 0, "active": 1, "paper": 2, "candidate": 3, "live": 4,
}

# 初始合法态白名单（INSERT 域：save_strategy 函数级校验与 DB INSERT
# 触发器同集——新策略只能以草稿或直接转正态入场，不得擅自加严/放宽）
INITIAL_STATUSES: frozenset = frozenset({"draft", "active"})


class LifecycleError(Exception):
    """生命周期域错误：code + http_status + message + detail。

    沿 ResearchError 形制（core/analysis/errors.py——code/status/
    message/detail 四件套，路由层转 HTTPException）；本类住常量基座
    模块=轻量定义点纪律（零第三方依赖、零域内反向依赖，repository
    与 service 均可安全模块级 import，不拖引擎链）。
    """

    def __init__(self, code: str, message: str, *, status: int = 409,
                 detail: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail
