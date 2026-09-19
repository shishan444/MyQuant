"""工程 REST 客户端（MCP server 侧唯一出口，D2）。

调用语义（跨边界契约）：
- 只读：默认超时 30s，超时/失败直接报错不重试（下次调用自然重试）；
- 写操作（创建任务/登记类）：显式超时，**不自动重试**（幂等性未知，
  重试可能重复登记；失败语义原样返回由 AI 决策）；
- 长任务轮询：间隔退避 5s 起、×1.5 逐次、上限 60s，总时长上限可配。

端点白名单（D9）：两档（readonly / research-exec），白名单外不可达
——所有请求前先过白名单检查，触金类端点永不在任何档位。
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, Optional

import httpx

# ------------------------------------------------------------------ 白名单

# GET 允许集（两档相同：全部为无副作用查询）
READONLY_ENDPOINTS = frozenset({
    "/api/data/available-sources",
    "/api/strategies",
    "/api/strategies/{strategy_id}",
    "/api/evolution/tasks",
    "/api/evolution/tasks/{task_id}",
    "/api/evolution/runner-status",
    "/api/trading/runner-status",
    "/api/trading/status",
    "/api/research/indicators",
    "/api/research/signals",
    "/api/research/hypotheses",
    "/api/research/hypotheses/{hypothesis_id}",
    "/api/research/log",
    "/api/research/validation/{strategy_id}/report",
    "/api/research/analysis/tasks/{task_id}",
    "/api/research/feature-requests",
    "/api/research/plugins/formula",
    "/api/research/plugins/model",
})

# 研究执行档 POST 允许集（登记/验证/分析/需求单/进化触发）
RESEARCH_POST_ENDPOINTS = frozenset({
    "/api/research/hypotheses",                             # 登记假设
    "/api/research/hypotheses/{hypothesis_id}/transition",  # 状态迁移
    "/api/research/validate-backtest",                      # 隔离回测
    "/api/research/random-window-test",                     # 随机窗口
    "/api/research/analysis",                               # 分析模板
    "/api/research/feature-requests",                       # 需求单
    "/api/research/plugins/formula",                        # 公式插件注册
    "/api/research/plugins/model",                          # 模型插件配置
    "/api/research/translate/{hypothesis_id}",              # 假设→策略转化
    "/api/evolution/tasks",                                 # 触发进化
})

# 触金面（任何档位永不出现：下单/调仓/风控修改/实盘开关）
FORBIDDEN_PATTERNS = ("/api/trading/start", "/api/trading/stop",
                      "/api/live", "/api/trading/order", "/api/trading/close")


class EndpointNotAllowed(Exception):
    """端点不在当前档位白名单。"""


class ApiUnreachable(Exception):
    """API 不可达（结构化可达性错误——宿主与日志可读的排查指引）。"""


def _match(path: str, endpoints: frozenset) -> bool:
    """路径匹配：字面量或 {param} 模板。"""
    if path in endpoints:
        return True
    parts = path.strip("/").split("/")
    for ep in endpoints:
        ep_parts = ep.strip("/").split("/")
        if len(ep_parts) != len(parts):
            continue
        if all(p.startswith("{") or p == q for p, q in zip(ep_parts, parts)):
            return True
    return False


class QuantApiClient:
    """经 REST 访问工程 API 的受控客户端。"""

    def __init__(
        self,
        base_url: Optional[str] = None,
        profile: str = "research",
        read_timeout: float = 30.0,
        write_timeout: float = 60.0,
    ) -> None:
        self.base_url = (base_url or os.environ.get(
            "MYQUANT_API_URL", "http://127.0.0.1:8000")).rstrip("/")
        self.profile = profile
        self.read_timeout = read_timeout
        self.write_timeout = write_timeout
        self._endpoints = READONLY_ENDPOINTS
        self._post_endpoints = RESEARCH_POST_ENDPOINTS if profile == "research" else frozenset()
        # 统计写调用重试行为（测试断言用：写失败必须 0 重试）
        self.call_log: list[Dict[str, Any]] = []

    # ------------------------------------------------------------ 权限

    def check_allowed(self, method: str, path: str) -> None:
        for pat in FORBIDDEN_PATTERNS:
            if path.startswith(pat):
                raise EndpointNotAllowed(f"触金面端点永不可达: {path}")
        if method == "GET":
            if not _match(path, self._endpoints):
                raise EndpointNotAllowed(
                    f"GET 端点 {path} 不在白名单")
        elif method == "POST":
            if not _match(path, self._post_endpoints):
                raise EndpointNotAllowed(
                    f"POST 端点 {path} 不在 {self.profile} 档写白名单")
        else:
            raise EndpointNotAllowed(f"方法 {method} 不受支持")

    # ------------------------------------------------------------ 调用

    def get(self, path: str, params: Dict[str, Any] | None = None) -> Any:
        """只读 GET：超时报错不重试。"""
        self.check_allowed("GET", path)
        self.call_log.append({"method": "GET", "path": path})
        with httpx.Client(timeout=self.read_timeout, trust_env=False) as client:
            try:
                resp = client.get(f"{self.base_url}{path}", params=params)
            except httpx.ConnectError as exc:
                raise ApiUnreachable(
                    f"API 不可达（{self.base_url}）：请确认应用已启动"
                    f"（python run.py）——{exc}") from exc
            resp.raise_for_status()
            return resp.json()

    def post(self, path: str, json_body: Dict[str, Any] | None = None) -> Any:
        """写 POST：失败不自动重试（幂等性未知；失败语义原样返回）。"""
        self.check_allowed("POST", path)
        self.call_log.append({"method": "POST", "path": path})
        with httpx.Client(timeout=self.write_timeout, trust_env=False) as client:
            try:
                resp = client.post(f"{self.base_url}{path}", json=json_body)
            except httpx.ConnectError as exc:
                raise ApiUnreachable(
                    f"API 不可达（{self.base_url}）：请确认应用已启动"
                    f"（python run.py）——{exc}") from exc
            resp.raise_for_status()
            if resp.status_code == 204 or not resp.content:
                return None
            return resp.json()

    # ------------------------------------------------------------ 轮询

    def poll_until_terminal(
        self,
        path: str,
        *,
        status_field: str = "status",
        terminal: tuple = ("succeeded", "failed", "completed", "stopped", "error"),
        initial_delay: float = 5.0,
        backoff: float = 1.5,
        max_delay: float = 60.0,
        total_timeout: float = 600.0,
        get_params: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """轮询长任务到终态（间隔退避 5s×1.5 上限 60s）。

        每轮保留最后已知状态并标注来源时刻（D2）。
        """
        deadline = time.monotonic() + total_timeout
        delay = initial_delay
        last: Dict[str, Any] = {}
        while time.monotonic() < deadline:
            try:
                last = self.get(path, params=get_params) or {}
                last["_polled_at"] = time.time()
                if last.get(status_field) in terminal:
                    return last
            except (httpx.HTTPError, KeyError):
                # 任务端点瞬时失败：保留最后已知状态继续轮询
                pass
            time.sleep(delay)
            delay = min(delay * backoff, max_delay)
        return {**last, "_poll_timeout": True, "_polled_at": time.time()}
