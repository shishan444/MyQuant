"""端点保留登记表（fix-replay-gap-alignment T5 基础设施——design D8）。

「路由面 ⊆ 消费面 ∪ 保留面」对账（T5-b）的机读保留面：路由在册而
前端/MCP 两消费面皆无调用时，须在此显式登记保留理由，否则视为
孤儿端点（T5-b 红）。先例同构=tests/mcpserver/_expected_tools.py
（长期权威清单型）——有集合运算消费方（T5-b）作必要机读载体。

条目五字段：
- endpoint：键形制 "METHOD /path"，路径模板段归一 {param} 形（与
  openapi.json 提取同源规范化）；
- retention_type：枚举 mcp-consumed（MCP 消费面实证——双消费面中
  仅 MCP 到达）/ pending-handover（待承接——deadline 必填）/
  deliberate（明示保留的基础设施/可观测性端点）；
- owner：保留责任域（backend/frontend/infra）；
- deadline：pending-handover 型必填（ISO 日期）；其余型可空；
- note：保留理由（一句话）。

纪律：孤儿端点删除不进此表（删除走 change 文档/TD 台账留档——
本表只登记「活着且无人到达但有明示理由」的保留面）。
"""
from __future__ import annotations

from typing import Dict, List

RETAINED_ENDPOINTS: List[Dict] = [
    # ── mcp-consumed：前端零调用、MCP 消费面实证（2026-11-21 提取，
    #    mcpserver/tools.py api.* 字面量+模板归一）──
    {
        "endpoint": "GET /api/research/analysis/tasks/{}",
        "retention_type": "mcp-consumed",
        "owner": "backend",
        "deadline": None,
        "note": "MCP get_analysis_task 轮询（分析长任务状态）",
    },
    {
        "endpoint": "GET /api/research/indicators",
        "retention_type": "mcp-consumed",
        "owner": "backend",
        "deadline": None,
        "note": "MCP list_indicators（指标元数据——登记面双向锚实证样本）",
    },
    {
        "endpoint": "POST /api/research/analysis",
        "retention_type": "mcp-consumed",
        "owner": "backend",
        "deadline": None,
        "note": "MCP run_analysis（分析长任务发起）",
    },
    {
        "endpoint": "POST /api/research/backtest-replay",
        "retention_type": "mcp-consumed",
        "owner": "backend",
        "deadline": None,
        "note": "MCP run_replay_backtest（现役回测主入口）",
    },
    {
        "endpoint": "POST /api/research/backtest-sandbox",
        "retention_type": "mcp-consumed",
        "owner": "backend",
        "deadline": None,
        "note": "MCP run_backtest（legacy 沙盒——下线标注但调用可用）",
    },
    {
        "endpoint": "POST /api/research/inspect-cases",
        "retention_type": "mcp-consumed",
        "owner": "backend",
        "deadline": None,
        "note": "MCP inspect_cases（案例抽查）",
    },
    {
        "endpoint": "POST /api/research/plugins/formula",
        "retention_type": "mcp-consumed",
        "owner": "backend",
        "deadline": None,
        "note": "MCP register_formula_plugin",
    },
    {
        "endpoint": "POST /api/research/plugins/model",
        "retention_type": "mcp-consumed",
        "owner": "backend",
        "deadline": None,
        "note": "MCP register_model_plugin",
    },
    {
        "endpoint": "POST /api/research/query-indicator",
        "retention_type": "mcp-consumed",
        "owner": "backend",
        "deadline": None,
        "note": "MCP query_indicator（指标读数归约）",
    },
    # ── deliberate：基础设施/可观测性端点（无业务消费方属设计意图）──
    {
        "endpoint": "GET /api/health",
        "retention_type": "deliberate",
        "owner": "infra",
        "deadline": None,
        "note": "存活探针（部署健康检查）",
    },
    {
        "endpoint": "GET /api/research/analysis/tasks",
        "retention_type": "deliberate",
        "owner": "backend",
        "deadline": None,
        "note": "分析任务列表观察面（MCP 轮询按 id 不列集——保留为"
                "可观测性端点；若长期无消费方可随下轮清理重评估）",
    },
]
