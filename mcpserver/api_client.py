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
    "/api/research/indicators",
    "/api/research/signals",
    "/api/research/market-snapshot",
    "/api/research/union-balance",  # 判定并集余量查询（0.4.1 R4）
    "/api/research/hypotheses",
    "/api/research/hypotheses/{hypothesis_id}",
    "/api/research/log",
    "/api/research/validation/{strategy_id}/report",
    "/api/research/analysis/tasks/{task_id}",
    "/api/research/feature-requests",
    "/api/research/plugins/formula",
    "/api/research/plugins/model",
    # 纸盘只读四件的 GET 端点族（rebuild-paper-trading 5.3）
    "/api/paper-trading/tasks",
    "/api/paper-trading/tasks/{task_id}",
    "/api/paper-trading/tasks/{task_id}/fills",
    "/api/paper-trading/tasks/{task_id}/equity",
    "/api/paper-trading/tasks/{task_id}/funding",
    "/api/paper-trading/tasks/{task_id}/report",
    "/api/paper-trading/tasks/{task_id}/report/final",
    "/api/paper-trading/runner-status",
})

# 研究执行档 POST 允许集（登记/验证/分析/需求单/进化触发）
RESEARCH_POST_ENDPOINTS = frozenset({
    "/api/research/hypotheses",                             # 登记假设
    "/api/research/hypotheses/{hypothesis_id}/transition",  # 状态迁移
    "/api/research/validate-backtest",                      # 隔离回测
    "/api/research/random-window-test",                     # 随机窗口
    "/api/research/backtest-sandbox",                       # 沙盒回测（M6 快速试错）
    "/api/research/analysis",                               # 分析模板
    "/api/research/inspect-cases",                          # 案例抽查（0.2.8 检查③）
    "/api/research/query-indicator",                        # 指标读数（0.2.9 检查④）
    "/api/research/staged-validation",                     # 分段验证（0.2.10 考试完整）
    "/api/research/feature-requests",                       # 需求单
    "/api/research/plugins/formula",                        # 公式插件注册
    "/api/research/plugins/model",                          # 模型插件配置
    "/api/research/translate/{hypothesis_id}",              # 假设→策略转化
    "/api/evolution/tasks",                                 # 触发进化
    "/api/paper-trading/tasks",                             # mount 上盘（rebuild-paper-trading 5.3）
    "/api/paper-trading/tasks/{task_id}/stop",              # unmount 撤盘（stop 语义）
})

# 触金面（任何档位永不出现：下单/调仓/风控修改/实盘开关）
FORBIDDEN_PATTERNS = ("/api/live",)


class EndpointNotAllowed(Exception):
    """端点不在当前档位白名单。"""


class ApiUnreachable(Exception):
    """API 不可达（结构化可达性错误——宿主与日志可读的排查指引）。"""


class ApiError(Exception):
    """服务端拒绝/失败：透传结构化错误契约 {code, message, detail}。

    body 含工程码族（RESEARCH_*）时原样透传；无结构化 code 的失败
    （如 pydantic 422、5xx 无 body）降级为 API_INTERNAL_ERROR 形制，
    原始响应信息保留在 detail。
    """

    def __init__(self, status: int, code: str, message: str,
                 detail: Any = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail


def _raise_api_error(resp: httpx.Response) -> None:
    """非 2xx：读 body 透传结构化错误，替代 raise_for_status() 丢 body 的坍缩。"""
    body: Any = None
    try:
        body = resp.json()
    except ValueError:
        pass
    d = body.get("detail") if isinstance(body, dict) else None
    if isinstance(d, dict) and isinstance(d.get("code"), str):
        raise ApiError(resp.status_code, d["code"],
                       str(d.get("message") or resp.reason_phrase),
                       d.get("detail"))
    raw = d if d is not None else (resp.text or None)
    raise ApiError(resp.status_code, "API_INTERNAL_ERROR",
                   f"HTTP {resp.status_code} {resp.reason_phrase}",
                   detail=raw)


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

    def _request(
        self, method: str, path: str, *,
        params: Dict[str, Any] | None = None,
        json_body: Dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """统一请求通道（get/post 薄壳共用）：白名单检查 + call_log
        留痕 + 可达性错误转译 + 非 2xx 结构化拒绝。

        timeout 缺省按方法取档（GET read_timeout / POST write_timeout）。
        """
        self.check_allowed(method, path)
        self.call_log.append({"method": method, "path": path})
        if timeout is None:
            timeout = (self.read_timeout if method == "GET"
                       else self.write_timeout)
        # A2：服务端启用 API key 时随请求携带（未设则不带，兼容放行态）
        headers = {}
        api_key = os.environ.get("MYQUANT_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        with httpx.Client(timeout=timeout, trust_env=False, headers=headers) as client:
            # 发送行按方法分派（保持 httpx.Client 的 get/post 方法面
            # ——测试 mock 契约面不变；两方法在 httpx 内等价）
            try:
                if method == "GET":
                    resp = client.get(
                        f"{self.base_url}{path}", params=params)
                else:
                    resp = client.post(
                        f"{self.base_url}{path}", json=json_body)
            except httpx.ConnectError as exc:
                raise ApiUnreachable(
                    f"API 不可达（{self.base_url}）：请确认应用已启动"
                    f"（./scripts/start.sh）——{exc}") from exc
            if resp.is_error:
                _raise_api_error(resp)
            return resp

    def get(self, path: str, params: Dict[str, Any] | None = None) -> Any:
        """只读 GET：超时报错不重试。"""
        return self._request("GET", path, params=params).json()

    def post(self, path: str, json_body: Dict[str, Any] | None = None) -> Any:
        """写 POST：失败不自动重试（幂等性未知；失败语义原样返回）。"""
        resp = self._request("POST", path, json_body=json_body)
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
            except (ApiError, httpx.HTTPError, KeyError):
                # 任务端点瞬时失败（含非 2xx 透传错误）：保留最后已知状态继续轮询
                pass
            time.sleep(delay)
            delay = min(delay * backoff, max_delay)
        return {**last, "_poll_timeout": True, "_polled_at": time.time()}
