"""触金零引用守护（常驻静态扫描——market-data-feed spec 第 5 条）。

三面守护（母案 A-2 域限定双包分工）：
①binance-futures-connector（UMFutures 族）——import 面（AST 节点+动态
  字符串双面）仅允许 core/data/fapi/{stream,rest}.py；白名单两模块内
  的 SDK 方法调用族外即红（kline/exchange_info/funding/mark_price 白
  名单外——order/cancel/account/position/leverage 族拒）；
②python-binance——import 面仅允许 core/live_trading/（主防线）；
③python-binance——方法调用面（下单/账户/持仓族精确方法集）命中集
  ⊆live_trading 子集判定（域内合法零断言——live_trading 自主演进域；
  AST 调用节点形制判定，position_mode 参数字符串天然免疫假阳性）。

断言形制=域判定子集断言（域内命中数与族归属不作断言）；扫描器能力
证明=构造变异样本（各族调用样本断言命中——python-binance 侧植入
位置在 live_trading 域外，域内合法不触发红即位置有效性自证）。
白名单单源驻本文件。

live_trading 域内 python-binance 调用现状快照（2026-10-28 宽扫 13
处族分组，非断言依据、不随 live_trading 演进更新）：下单/撤单族
gateway.py:76/97/113/124；账户族 binance_client.py:140+runner.py:99；
持仓族 binance_client.py:129/135+runner.py:100+gateway.py:133；订单
查询族 runner.py:148+gateway.py:128/129；公共族 futures_time/
futures_exchange_info/futures_klines（非触金面）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

_PROJECT_ROOT = Path(__file__).resolve().parents[1]

# ── 白名单单源（权威源——tests/data/fapi/test_import_whitelist.py
#    的简版断言引用此处常量，勿在别处重复定义）─────────────────
# fapi SDK（binance-futures-connector）import 面唯一允许模块
FAPI_SDK_MODULE_WHITELIST = frozenset({
    "core/data/fapi/stream.py",
    "core/data/fapi/rest.py",
})
# python-binance 允许域（实盘模块本职——纸盘触金纪律不管辖实盘域）
PYTHON_BINANCE_ALLOWED_PREFIX = "core/live_trading/"

# fapi SDK 子树（binance-futures-connector 的 import 面；顶层包名
# binance 三包共享，按子树区分归属）
FAPI_SDK_SUBTREES = (
    "binance.um_futures", "binance.cm_futures", "binance.websocket",
    "binance.lib", "binance.error", "binance.api", "binance.spot",
)
# python-binance 子树
PYTHON_BINANCE_SUBTREES = (
    "binance.client", "binance.exceptions", "binance.enums",
    "binance.helpers", "binance.async_client",
)
# fapi SDK 方法白名单族（行情/规则/费率/标价——前缀族）
FAPI_ALLOWED_METHOD_PREFIXES = (
    "kline", "exchange_info", "funding", "mark_price",
)
# 触金方法族（fapi 白名单模块内禁用的 SDK 方法——词根子串族；
# 域小（两模块）子串宽无妨）
FAPI_TOUCH_METHOD_ROOTS = (
    "order", "cancel", "account", "position", "leverage", "trade",
)
# python-binance 触金方法精确集（下单/撤单/账户/持仓/杠杆族——
# 精确名防误伤：工程内自研包装类 BinanceDemoClient.account() 的同名
# 转发方法在案（scripts/spike_demo_live.py:107），裸 "account" 不入集）
PYTHON_BINANCE_TOUCH_METHODS = frozenset({
    # 下单/撤单族
    "create_order", "order_limit", "order_market", "order_oco",
    "order_limit_buy", "order_limit_sell", "cancel_order",
    "cancel_open_orders", "futures_create_order", "futures_order",
    "futures_cancel_order", "futures_cancel_all_open_orders",
    "futures_place_batch_order", "futures_modify_order",
    "futures_modify_batch_order", "futures_order_test",
    # 账户族
    "futures_account", "futures_account_balance",
    "futures_account_trades", "get_account",
    # 持仓/杠杆/保证金族
    "futures_position_risk", "futures_position_information",
    "futures_get_position_mode", "futures_change_position_mode",
    "futures_change_leverage", "change_leverage",
    "futures_change_margin_type", "change_margin_type",
})

# 扫描域（生产代码全集；tests 域不扫——测试替身与自述文档不是
# 运行时代码，变异自证经 entries 参数显式注入）
_SCAN_DIRS = ("core", "api", "mcpserver", "scripts", "alembic")


def _project_entries() -> list[tuple[str, str]]:
    """生产域 (relpath, source) 清单。"""
    entries: list[tuple[str, str]] = []
    for directory in _SCAN_DIRS:
        base = _PROJECT_ROOT / directory
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            entries.append((
                path.relative_to(_PROJECT_ROOT).as_posix(),
                path.read_text(encoding="utf-8")))
    return entries


def _module_subtree(module: str) -> str | None:
    """import 模块名的 SDK 归属（fapi / python-binance / None=无关）。"""
    for subtree in FAPI_SDK_SUBTREES:
        if module == subtree or module.startswith(subtree + "."):
            return "fapi"
    for subtree in PYTHON_BINANCE_SUBTREES:
        if module == subtree or module.startswith(subtree + "."):
            return "python_binance"
    return None


def _import_nodes(tree: ast.AST):
    """AST import 节点流（import/importfrom 双形制）。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:  # from binance import client 变体
                yield f"{module}.{alias.name}"
            yield module


def _string_constants(tree: ast.AST):
    """字符串字面量流（动态导入路径/getattr 绕过防线）。"""
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant)
                and isinstance(node.value, str)):
            yield node.value


def _called_attrs(tree: ast.AST):
    """方法调用节点流（AST Call func=Attribute 形制——非字符串 grep）。"""
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)):
            yield node.func.attr


def _tree_of(text: str) -> ast.Module:
    return ast.parse(text)


def scan_touch_money(entries: list[tuple[str, str]]) -> list[str]:
    """三面扫描（返回违规清单——主断言空集；变异自证构造样本命中）。

    Args:
        entries: (relpath_posix, source_text) 清单——生产主断言传
            _project_entries()；变异自证追加构造样本（relpath 手工
            指定以驱动域判定）。
    """
    violations: list[str] = []
    for relpath, source in entries:
        tree = _tree_of(source)
        in_fapi_whitelist = relpath in FAPI_SDK_MODULE_WHITELIST
        in_live_trading = relpath.startswith(PYTHON_BINANCE_ALLOWED_PREFIX)

        # ①fapi SDK import 面（AST 节点+字符串字面量双面）
        modules = {m for m in _import_nodes(tree)}
        for value in _string_constants(tree):
            if value.startswith("binance.") or (
                    "/" in value and "binance/" in value):
                modules.add(value.replace("/", "."))
        for module in sorted(modules):
            if _module_subtree(module) == "fapi" and not in_fapi_whitelist:
                violations.append(
                    f"{relpath}: fapi SDK import 面白名单外（{module}）")
            if (_module_subtree(module) == "python_binance"
                    and not in_live_trading):
                violations.append(
                    f"{relpath}: python-binance import 面域外（{module}）")

        # ②fapi 白名单模块内的方法族校验（该域内对象即 SDK 面）
        if in_fapi_whitelist:
            for attr in _called_attrs(tree):
                if attr.startswith(FAPI_ALLOWED_METHOD_PREFIXES):
                    continue
                if any(root in attr for root in FAPI_TOUCH_METHOD_ROOTS):
                    violations.append(
                        f"{relpath}: SDK 方法白名单外调用（.{attr}()）")

        # ③python-binance 方法面：命中集⊆live_trading 子集判定
        if not in_live_trading:
            for attr in _called_attrs(tree):
                if attr in PYTHON_BINANCE_TOUCH_METHODS:
                    violations.append(
                        f"{relpath}: python-binance 触金方法域外"
                        f"（.{attr}()）")
    return violations


class TestTouchMoneyGuard:
    """常驻主断言（生产域三面全绿——CI 阻断）。"""

    def test_production_zero_touch_money(self):
        """当前代码+新模块全绿：fapi SDK import/方法面白名单内+
        python-binance 命中集⊆live_trading（域判定子集断言）。"""
        violations = scan_touch_money(_project_entries())
        assert violations == [], (
            "触金零引用守护违例（market-data-feed spec「触金零引用"
            f"守护」）:\n" + "\n".join(violations))


class TestScannerCapabilityProof:
    """变异自证：构造各族调用样本断言扫描命中（能力证明非生产计数）。"""

    def test_mutation_fapi_import_outside_whitelist(self):
        """变异①：白名单外 import binance.um_futures →命中。"""
        sample = ("evil/leak.py", "from binance.um_futures import UMFutures\n")
        violations = scan_touch_money([sample])
        assert any("fapi SDK import 面白名单外" in v for v in violations)

    def test_mutation_fapi_dynamic_import_string(self):
        """变异①纵深：importlib 动态字符串绕过 →命中（字符串面）。"""
        sample = ("evil/dyn.py",
                  'import importlib\n'
                  'm = importlib.import_module("binance.um_futures")\n')
        violations = scan_touch_money([sample])
        assert any("binance.um_futures" in v for v in violations)

    def test_mutation_python_binance_import_outside_domain(self):
        """变异②：域外 from binance.client import Client →命中。"""
        sample = ("evil/pb.py", "from binance.client import Client\n")
        violations = scan_touch_money([sample])
        assert any("python-binance import 面域外" in v for v in violations)

    def test_mutation_python_binance_order_call_outside_domain(self):
        """变异③：域外 create_order 调用 →命中（AST 调用节点形制）。"""
        sample = ("evil/order.py", "result = client.create_order()\n")
        violations = scan_touch_money([sample])
        assert any("create_order" in v for v in violations)

    def test_position_mode_kwarg_not_false_positive(self):
        """非误伤锚：position_mode 作参数名/字符串不触发（AST 形制
        免疫——任务书防假阳性条款）。"""
        sample = ("core/paper_trading/params.py",
                  "position_mode = 'risk_budget'\n"
                  "x = {'position_mode': position_mode}\n")
        violations = scan_touch_money([sample])
        assert violations == []

    def test_inside_live_trading_domain_is_legal(self):
        """位置有效性自证：同触金调用在 live_trading 域内不触发红
        （域内合法零断言——植入域内不红证明域判定真实工作）。"""
        sample = ("core/live_trading/_sample.py",
                  "from binance.client import Client\n"
                  "c = Client()\n"
                  "c.futures_create_order()\n")
        violations = scan_touch_money([sample])
        assert violations == []

    def test_inside_fapi_whitelist_market_methods_legal(self):
        """白名单内行情族方法调用合法（klines/exchange_info）。"""
        sample = ("core/data/fapi/rest.py",
                  "frame = client.klines()\n"
                  "info = client.exchange_info()\n")
        violations = scan_touch_money([sample])
        assert violations == []

    def test_inside_fapi_whitelist_touch_method_is_red(self):
        """白名单模块内触金方法（order 族）→命中（方法族校验）。"""
        sample = ("core/data/fapi/rest.py",
                  "res = client.futures_order()\n")
        violations = scan_touch_money([sample])
        assert any("SDK 方法白名单外调用" in v for v in violations)

    def test_mutation_samples_do_not_touch_disk(self):
        """变异自证全内存构造（不写生产目录——扫描器 entries 注入）；
        主断言独立复跑仍绿（还原后复绿）。"""
        # 构造样本仅存在于内存 entries——生产文件零触碰
        sample = ("evil/ephemeral.py", "from binance.um_futures import X\n")
        assert scan_touch_money([sample])
        # 生产域独立扫描仍全绿（样本未污染真实工程）
        assert scan_touch_money(_project_entries()) == []
