"""纸盘域对回测引擎引用零命中断言（build-position-registry 任务 4.9
——R10/REQ-4-07 验收锚）。

沿革声明：本文件承接原 test_ledger.py TestSingleSourceGuard.
test_risk_budget_qty_single_source_consumption（组 3 提交 c68482a
删除，Team Lead 批复选项 A）——原守卫防「定仓公式复制进 ledger」
（inspect 断言 import 在场+消费点在场+risk_fraction 唯一出现），本
change 后 ledger 不再持有定仓公式（移驻 core/positioning 注册条目
），原守卫的前提（import 在场）与威胁面（公式复制进 ledger）同时
消亡。防回潮由三层承接，覆盖强于旧单点守卫：
- R10 本文件：core/paper_trading/ 全域对回测引擎的 import 引用零
  命中（REQ-4-07 工程实质之「ledger import 零引用」——双书写形制
  扫描防形制错位漏检）；
- R5 双实现对拍（tests/positioning/test_parity.py）：注册条目纯
  Python 实现与引擎 njit risk_budget_qty 公式等值锚；
- R7 受控等价（test_controlled_equivalence.py 既有三层+新增直对
  拍）：经注册表路径后行为逐位不变。

断言形制（design §11 R10「沿符号级双书写形制纪律」）：扫描
core/paper_trading/ 全部 .py 源码文本——
- 模块点形：`core.backtest` 任何位置零出现（含注释/docstring 与
  importlib 动态 import 字符串——点形是 Python import 语句与动态
  模块路径的真实书写通道；现状基线全域零命中）+子模块导入变体
  `from core import backtest` 同拒；
- 路径斜杠形：`core/backtest` 命中行必须全部位于注释或 docstring
  （文档性对照说明——在案两处：runtime/feed.py 模块 docstring 的
  ATR 装载形制对照、kernel/types.py 模块 docstring 的物化缺省表
  对照锚，均为设计保留的对照关系描述）；代码位置（含字符串字面量
  ——动态 import 路径）命中即红。

tester 收紧记录（2026-10-18 建网核对）：原点形断言只查
`from core.backtest`/`import core.backtest` 两前缀，变异实证暴露
两漏检面——importlib.import_module("core.backtest.engine") 点形
动态字符串与 from core import backtest 子模块变体均不红，而
acceptance 声称「含动态 import 字符串」名不副实；收紧为裸
`core.backtest` 任何位置零出现（现状零命中，收紧零成本）。
"""
from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

_PAPER_DIR = Path(__file__).resolve().parents[2] / "core" / "paper_trading"


def _python_files() -> list[Path]:
    """纸盘域源码树（递归全部 .py）。"""
    return sorted(_PAPER_DIR.rglob("*.py"))


class TestNoBacktestImport:
    """REQ-4-07：core/paper_trading/ 对回测引擎引用零命中。"""

    def test_no_dotted_import_of_core_backtest_anywhere(self):
        """模块点形零命中：全部源码文本（含注释/docstring/字符串
        字面量）不出现 `core.backtest`——涵盖 import 语句、importlib
        动态 import 字符串与任何书写变体；子模块导入变体
        `from core import backtest` 同拒（纸盘对回测引擎的 import
        依赖为零）。"""
        offenders: list[str] = []
        for path in _python_files():
            text = path.read_text(encoding="utf-8")
            for needle in ("core.backtest", "from core import backtest"):
                if needle in text:
                    offenders.append(f"{path.relative_to(_PAPER_DIR)}: "
                                     f"{needle}")
        assert offenders == [], (
            f"纸盘域不得 import 回测引擎（REQ-4-07）: {offenders}")

    def test_slash_form_mentions_confined_to_comments_or_docstrings(self):
        """路径斜杠形防漏检：`core/backtest` 命中行必须全部位于注释
        token 或 docstring 节点行范围内（文档性对照说明——两处在案
        基线见模块 docstring）；代码位置命中（含字符串字面量——
        importlib 动态路径）即红。"""
        offenders: list[str] = []
        for path in _python_files():
            text = path.read_text(encoding="utf-8")
            if "core/backtest" not in text:
                continue
            rel = str(path.relative_to(_PAPER_DIR))
            # 注释行集（tokenize——字符串内的 # 不算注释）
            comment_lines: set[int] = set()
            for tok in tokenize.generate_tokens(
                    io.StringIO(text).readline):
                if tok.type == tokenize.COMMENT:
                    comment_lines.update(range(tok.start[0],
                                               tok.end[0] + 1))
            # docstring 行集（ast——模块/类/函数体首语句的字符串）
            docstring_lines: set[int] = set()
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef,
                                     ast.FunctionDef,
                                     ast.AsyncFunctionDef)):
                    body = node.body
                    if (body and isinstance(body[0], ast.Expr)
                            and isinstance(body[0].value, ast.Constant)
                            and isinstance(body[0].value.value, str)):
                        doc = body[0].value
                        docstring_lines.update(
                            range(doc.lineno, doc.end_lineno + 1))
            allowed = comment_lines | docstring_lines
            for lineno, line in enumerate(text.splitlines(), start=1):
                if "core/backtest" in line and lineno not in allowed:
                    offenders.append(f"{rel}:{lineno}: {line.strip()}")
        assert offenders == [], (
            "core/backtest 不得出现于代码位置（动态 import 路径/字符串"
            f"字面量均拒——文档性对照说明限注释与 docstring）: "
            f"{offenders}")
