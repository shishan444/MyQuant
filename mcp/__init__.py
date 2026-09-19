"""MCP server 模块：AI 访问工程的唯一受控通道（add-ai-research-mcp）。

独立常驻进程（绑定 127.0.0.1），全部读写经工程 REST API（与前端同契约）；
权限分档（只读 / 研究执行），不提供任何触金类工具。
依赖方向：mcp -> api（REST）-> core；mcp 不直接 import core（D2 单一调用面）。
"""
