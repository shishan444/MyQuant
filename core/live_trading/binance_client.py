"""币安 USDT-M demo 客户端封装（第2步）。

封装 python_binance ``Client(demo=True)``，提供实盘所需的基础能力：
- **时间同步**：``sync_time`` 校正服务器时间偏移，防 -1021（时间窗过期）。
- **精度取整**：按 ``futures_exchange_info`` 的 tickSize/stepSize 取整，防 -1111。
- **持仓模式**：``ensure_one_way_mode`` 确保单向（One-Way），规划 v2 决策。
- **账户查询**：``account`` 供防错连主网核对。

不含业务下单逻辑（第4步 BinanceDemoGateway）。本层只做"连接/精度/模式"基础设施。
API 签名经源码确认（client.py:7394 futures_time / 8474 change_position_mode /
8482 get_position_mode / 7913 create_order）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from binance.client import Client

from .credentials import BinanceCredentials, load_credentials

# 持仓模式常量（dualSidePosition 值）
MODE_HEDGE = True  # 双向持仓
MODE_ONEWAY = False  # 单向持仓（规划 v2 选定）


class CredentialMissingError(RuntimeError):
    """未找到 demo 凭证。"""


@dataclass
class SymbolPrecision:
    """交易对精度（价格 tickSize + 数量 stepSize）。"""

    tick_size: float
    step_size: float

    def round_price(self, price: float) -> float:
        return _round_to_step(price, self.tick_size)

    def round_qty(self, qty: float) -> float:
        return _round_to_step(qty, self.step_size)


def _round_to_step(value: float, step: float) -> float:
    """按 step 取整到合法精度（币安 LOT_SIZE/PRICE_FILTER 规则）。"""
    if step <= 0:
        return value
    return round(round(value / step) * step, 10)


def extract_precision(exchange_info: dict, symbol: str) -> SymbolPrecision:
    """从 futures_exchange_info 提取交易对精度。"""
    for s in exchange_info.get("symbols", []):
        if s.get("symbol") == symbol:
            tick = step = 0.0
            for f in s.get("filters", []):
                ftype = f.get("filterType")
                if ftype == "PRICE_FILTER":
                    tick = float(f["tickSize"])
                elif ftype == "LOT_SIZE":
                    step = float(f["stepSize"])
            return SymbolPrecision(tick_size=tick, step_size=step)
    raise ValueError(f"symbol {symbol} 未在 exchange_info 中找到")


class BinanceDemoClient:
    """币安 USDT-M demo 客户端封装。

    Args:
        creds: 凭证；为 None 时从 Keychain 加载。
        client: 注入的 binance.Client（测试用 mock）；为 None 时构造真实 demo Client。
    """

    # 时间偏移超过此阈值(ms)视为未同步
    TIME_SYNC_THRESHOLD_MS = 1000

    def __init__(
        self,
        creds: Optional[BinanceCredentials] = None,
        client: Optional[Client] = None,
    ) -> None:
        if client is not None:
            self._client = client
        else:
            if creds is None:
                creds = load_credentials()
            if creds is None:
                raise CredentialMissingError(
                    "未找到 demo 凭证。请先 save_credentials() 存入 Keychain。"
                )
            creds.validate()
            # ping=False：跳过现货 ping（否则连 demo-api.binance.com 现货 demo 超时）。
            # 合约 demo（demo-fapi.binance.com）连接由 futures_time()/futures_account() 首次触发。
            self._client = Client(
                api_key=creds.api_key, api_secret=creds.api_secret, demo=True, ping=False
            )
        self._time_offset_ms = 0
        self._precision_cache: dict[str, SymbolPrecision] = {}

    # ---- 时间同步 ----
    def sync_time(self) -> int:
        """同步服务器时间，返回偏移(ms) = server - local。"""
        local_ms = int(time.time() * 1000)
        server_ms = int(self._client.futures_time()["serverTime"])
        self._time_offset_ms = server_ms - local_ms
        return self._time_offset_ms

    @property
    def time_offset_ms(self) -> int:
        return self._time_offset_ms

    @property
    def time_synced(self) -> bool:
        return abs(self._time_offset_ms) <= self.TIME_SYNC_THRESHOLD_MS

    # ---- 精度 ----
    def load_precision(self, symbol: str) -> SymbolPrecision:
        if symbol not in self._precision_cache:
            self._precision_cache[symbol] = extract_precision(
                self._client.futures_exchange_info(), symbol
            )
        return self._precision_cache[symbol]

    # ---- 持仓模式 ----
    def get_position_mode(self) -> bool:
        """返回 dualSidePosition：True=Hedge(双向)，False=One-Way(单向)。"""
        return bool(self._client.futures_get_position_mode()["dualSidePosition"])

    def ensure_one_way_mode(self) -> bool:
        """确保单向模式；当前为双向则切换。返回最终 dualSidePosition（False=单向）。"""
        if not self.get_position_mode():
            return False  # 已是单向
        self._client.futures_change_position_mode(dualSidePosition=MODE_ONEWAY)
        return self.get_position_mode()  # 预期 False

    # ---- 账户（防错连主网核对用）----
    def account(self) -> dict:
        return self._client.futures_account()

    @property
    def raw(self) -> Client:
        """暴露底层 Client（供 Gateway 第4步调用下单方法）。"""
        return self._client
