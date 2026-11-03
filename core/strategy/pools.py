"""数学组件四层池权威源（be-plugin-pool-authority D10）。

POOLS 常量自 Plugins.tsx:308 静态投影逐字移植（对账表零差异留档——
快照权威曾归需求文档《数学算法组件库-完整需求-2026-10-28.md》，
本模块升格为工程内单一权威源：GET /api/plugins/pools 单源直出，
前端静态投影与「静态快照」标识随之退役（fe-asset-data-domain 承接））。
"""
from __future__ import annotations

from typing import Any, Dict, List

POOLS: List[Dict[str, Any]] = [
    {
        "name": "默认池",
        "note": "9 项，直接可用",
        "items": ["ATR 自适应", "EWMA 追踪线", "卡尔曼追踪线",
                  "卡尔曼方向信号", "小波去噪", "series 结构线（限顺耦合）",
                  "方向门控", "FundingPressure", "BB 通道"],
    },
    {
        "name": "候选池",
        "note": "约 8 项，须配市况条件——未配市况条件不得单独入假设",
        "items": ["滚动分位", "Parkinson", "Garman-Klass", "Hurst", "ADX 等"],
    },
    {
        "name": "黑名单",
        "note": "8 项，写死禁用——命中即整卡排除，不因叠加其他组件解禁",
        "items": ["trail 2%", "同轨道 series 线", "长持有策略×任何止损",
                  "卡尔曼做多（裸用）", "两级路由", "动量交叉族",
                  "趋势跟踪做多", "裸深度网络"],
    },
    {
        "name": "观察池",
        "note": "4 项，挂起——不进入方向枚举，不投入研究预算",
        "items": ["GARCH", "HMM", "粒子滤波", "扩展卡尔曼"],
    },
]
