"""第0步 spike：demo mode 实连验证（demo-fapi.binance.com）。

凭证注入（二选一）：
  A. 环境变量（推荐，凭证不进对话/不进 git）：
       export BINANCE_DEMO_API_KEY=你的key
       export BINANCE_DEMO_API_SECRET=你的secret
       ./venv/bin/python scripts/spike_demo_live.py
     （脚本自动从 env 存入 macOS Keychain，后续不依赖 env）
  B. 已用 save_credentials() 存入 Keychain：直接跑。

验证项（每项 ✅/❌ + 汇总）：
  1. 连接 demo-fapi（BinanceDemoClient，Client(demo=True)）
  2. 时间同步（防 -1021）
  3. 账户余额（核对 demo 虚拟资金，**防错连主网**）
  4. 持仓模式（确保 One-Way）
  5. 精度（BTCUSDT tickSize/stepSize）
  6. 下单闭环（小额开多 → 查持仓 → 平多）

触及 AI_CODING_GUIDE §1 红线（实盘执行），层3 demo 边界（模拟资金）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.live_trading.binance_client import BinanceDemoClient
from core.live_trading.credentials import (
    BinanceCredentials,
    has_credentials,
    load_credentials,
    save_credentials,
)
from core.live_trading.gateway import BinanceDemoGateway
from core.live_trading.types import OrderAction

# 小额测试单（BTCUSDT 最小步 0.001，用 0.002 确保成交）
_SPIKE_QTY = 0.002
_SYMBOL = "BTCUSDT"


def _ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def _fail(msg: str, exc: Exception | None = None) -> None:
    print(f"  ❌ {msg}" + (f": {exc}" if exc else ""))


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def get_credentials() -> BinanceCredentials | None:
    """keyring 优先；无则从环境变量读并存入 keyring。"""
    if has_credentials():
        return load_credentials()
    key = os.environ.get("BINANCE_DEMO_API_KEY")
    secret = os.environ.get("BINANCE_DEMO_API_SECRET")
    if key and secret:
        creds = BinanceCredentials(api_key=key, api_secret=secret, demo=True)
        save_credentials(creds)  # 校验 + 存 keyring
        _ok("从环境变量存入 macOS Keychain（后续无需 env）")
        return creds
    return None


def main() -> int:
    print("第0步 spike · demo mode 实连验证 (demo-fapi.binance.com)\n")

    _section("0. 凭证")
    creds = get_credentials()
    if creds is None:
        _fail("未找到凭证。请用环境变量或 save_credentials() 注入。")
        return 1
    _ok("demo 凭证就绪（demo=True 已校验）")

    results: list[bool] = []

    # 1. 连接
    _section("1. 连接 demo-fapi.binance.com")
    try:
        client = BinanceDemoClient(creds=creds)
        _ok("BinanceDemoClient 构造成功（Client(demo=True)）")
        results.append(True)
    except Exception as exc:
        _fail("连接失败", exc)
        return _summarize([False])

    # 2. 时间同步
    _section("2. 时间同步")
    try:
        offset = client.sync_time()
        synced = client.time_synced
        _ok(f"server-local 偏移 = {offset} ms（{'同步 OK' if synced else '⚠️ 偏移大'})")
        results.append(synced)
    except Exception as exc:
        _fail("时间同步失败", exc)
        results.append(False)

    # 3. 账户（防错连主网）
    _section("3. 账户余额（核对 demo 虚拟资金，防错连主网）")
    try:
        account = client.account()
        balances = {
            a["asset"]: a.get("availableBalance", a.get("balance"))
            for a in account.get("assets", [])
            if float(a.get("balance", 0)) > 0
        }
        _ok(f"账户非零资产: {balances}")
        print("     确认是 demo 虚拟资金（非主网真实资金）。如余额异常巨大或为真实持仓，立即停止！")
        results.append(True)
    except Exception as exc:
        _fail("账户查询失败", exc)
        results.append(False)

    # 4. 持仓模式
    _section("4. 持仓模式（确保 One-Way）")
    try:
        final = client.ensure_one_way_mode()
        _ok(f"dualSidePosition={final}（False=One-Way ✓）")
        results.append(final is False)
    except Exception as exc:
        _fail("持仓模式设置失败", exc)
        results.append(False)

    # 5. 精度
    _section("5. 精度")
    try:
        p = client.load_precision(_SYMBOL)
        _ok(f"{_SYMBOL}: tickSize={p.tick_size}, stepSize={p.step_size}")
        results.append(True)
    except Exception as exc:
        _fail("精度查询失败", exc)
        results.append(False)

    # 6. 下单闭环
    _section(f"6. 下单闭环（{_SPIKE_QTY} {_SYMBOL}：开多 → 查持仓 → 平多）")
    gw = BinanceDemoGateway(client)
    try:
        order = gw.place_market_order(_SYMBOL, OrderAction.OPEN_LONG, _SPIKE_QTY)
        _ok(f"开多: id={order.exchange_order_id} status={order.status.value} qty={order.quantity}")
        pos = gw.get_position_raw(_SYMBOL)
        if pos and float(pos.get("positionAmt", 0)) > 0:
            _ok(f"持仓确认: positionAmt={pos.get('positionAmt')} entry={pos.get('entryPrice')}")
            close = gw.place_market_order(_SYMBOL, OrderAction.CLOSE_LONG, _SPIKE_QTY)
            _ok(f"平多: id={close.exchange_order_id} status={close.status.value}")
            results.append(True)
        else:
            _fail("开多后无持仓（可能未成交或延迟）")
            results.append(False)
    except Exception as exc:
        _fail("下单闭环失败", exc)
        results.append(False)

    return _summarize(results)


def _summarize(results: list[bool]) -> int:
    _section("汇总")
    passed = sum(results)
    total = len(results)
    print(f"  {passed}/{total} 项通过")
    if passed == total:
        print("\n🎉 spike 实连全部通过！demo mode 接入成功，引擎层真实可用。")
        return 0
    print(f"\n⚠️ {total - passed} 项失败，需排查（看上方 ❌ 项）。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
