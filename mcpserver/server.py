"""MCP server 装配与入口（add-ai-research-mcp 4.1）。

独立常驻进程（stdio transport；绑定本机概念——stdio 本身无网络面），
经 QuantApiClient 走 REST（127.0.0.1:8000，与前端同契约）。

环境变量：
- MYQUANT_API_URL：工程 API 地址（默认 http://127.0.0.1:8000）
- MYQUANT_MCP_PROFILE：readonly | research（默认 readonly——最小权限）
"""
from __future__ import annotations

import os
from typing import Optional

from mcp.server.mcpserver import MCPServer

from mcpserver.api_client import QuantApiClient
from mcpserver.capability import build_capability_doc
from mcpserver.tools import TOOL_TIERS, register_readonly_tools, register_research_tools

SERVER_NAME = "myquant-research"


def build_server(
    profile: Optional[str] = None,
    base_url: Optional[str] = None,
) -> tuple[MCPServer, QuantApiClient]:
    """装配 server：按档位注册工具 + 能力自述 resource。

    Returns:
        (server, api_client)——client 供测试内省。
    """
    profile = profile or os.environ.get("MYQUANT_MCP_PROFILE", "readonly")
    if profile not in ("readonly", "research"):
        raise ValueError(f"未知档位: {profile}（readonly|research）")

    api = QuantApiClient(base_url=base_url, profile=profile)
    mcp = MCPServer(SERVER_NAME)

    register_readonly_tools(mcp, api)
    if profile == "research":
        register_research_tools(mcp, api)

    # 能力自述 resource（四部分：工具清单/协议要点/可表达边界/状态机）
    tool_names = sorted(TOOL_TIERS["readonly"] | TOOL_TIERS["research"]
                        if profile == "research" else TOOL_TIERS["readonly"])
    doc = build_capability_doc(tool_names)

    @mcp.resource("quant://capabilities")
    def capabilities() -> str:
        return doc

    return mcp, api


def main() -> None:
    """进程入口：python -m mcpserver.server（stdio transport）。"""
    mcp, _ = build_server()
    mcp.run()


if __name__ == "__main__":
    main()
