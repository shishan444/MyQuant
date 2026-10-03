"""MCP server 装配与入口（add-ai-research-mcp 4.1 / switch-mcp-http-transport）。

默认以 streamable HTTP transport 常驻（127.0.0.1:8800/mcp——随应用
统一入口 scripts/start.sh 启动，宿主一次配 URL 即用）；stdio 备选保留（宿主本地
spawn 形态）。经 QuantApiClient 走 REST（127.0.0.1:8000）。

环境变量：
- MYQUANT_API_URL：工程 API 地址（默认 http://127.0.0.1:8000）
- MYQUANT_MCP_PROFILE：readonly | research（默认 readonly——最小权限）
- MYQUANT_MCP_TRANSPORT：streamable-http（默认）| stdio
- MYQUANT_MCP_PORT：HTTP 端口（默认 8800；**必须显式传**——SDK 默认
  8000 与 API 冲突，本入口已显式规避）
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from mcp.server.mcpserver import MCPServer

from mcpserver.api_client import QuantApiClient

logger = logging.getLogger(__name__)
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


def _check_api_reachable() -> None:
    """启动自检：API 可达性探测（不可达记 warning 后继续——总入口下
    API 可能稍后就绪；工具调用失败时有结构化错误兜底）。"""
    import httpx

    url = os.environ.get("MYQUANT_API_URL", "http://127.0.0.1:8000")
    try:
        resp = httpx.get(f"{url}/api/data/available-sources",
                         timeout=3.0, trust_env=False)
        if resp.status_code == 200:
            logger.info("API 自检通过: %s", url)
        else:
            logger.warning("API 自检异常（HTTP %s）: %s", resp.status_code, url)
    except httpx.HTTPError as exc:
        logger.warning("API 不可达（%s）: %s —— 工具调用将报可达性错误，"
                       "请确认应用已启动", url, exc)


def main() -> None:
    """进程入口：python -m mcpserver.server。

    默认 streamable-http（127.0.0.1:8800/mcp）；MYQUANT_MCP_TRANSPORT=stdio
    切回宿主 spawn 备选形态。
    """
    transport = os.environ.get("MYQUANT_MCP_TRANSPORT", "streamable-http")
    mcp, _ = build_server()
    if transport == "stdio":
        mcp.run(transport="stdio")
        return
    if transport != "streamable-http":
        raise ValueError(f"未知 transport: {transport}（streamable-http|stdio）")
    _check_api_reachable()
    # 端口必须显式传：SDK 默认 8000 与 API 冲突（设计 D1 硬约束）
    port = int(os.environ.get("MYQUANT_MCP_PORT", "8800"))
    logger.info("MCP server (streamable-http): http://127.0.0.1:%d/mcp", port)
    mcp.run(transport="streamable-http", host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
