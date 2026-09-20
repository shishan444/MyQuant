"""MCP server 模块：AI 访问工程的唯一受控通道（add-ai-research-mcp）。

独立常驻进程（绑定 127.0.0.1），全部读写经工程 REST API（与前端同契约）；
权限分档（只读 / 研究执行），不提供任何触金类工具。

命名注记：D1 原定顶层模块名 `mcp/`，实施首日发现与 MCP SDK 的
PyPI 包名冲突（`import mcp` 解析优先本地目录，SDK 被遮蔽），
故更名 `mcpserver/`——设计文档 D1 已加实施注记。

依赖方向：mcpserver -> api（REST）-> core；不直接 import core（单一调用面）。
"""
