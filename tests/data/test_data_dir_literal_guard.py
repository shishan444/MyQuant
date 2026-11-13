"""数据目录字面量与 __file__ 锚定形式守卫（fix-data-dir-single-source A-5）。

data-directory-governance「字面量单点」Scenario 的常驻防回潮网：
生产代码面 market_fapi 字面量仅 FAPI_DATA_DIR_DEFAULT 常量单点，
缺省数据目录解析不得绕开 default_data_dir 自行 __file__ 锚定。

双书写形（design A-3）：整串形 "data/market_fapi" 与拼接形
"data" / "market_fapi"——两者在 AST 字符串常量层均含 market_fapi
子串，经同一常量扫描覆盖（拼接形如 `/ "data" / "market_fapi"` 的
market_fapi 段是独立字符串常量，代码位置命中即红）。

断言一（market_fapi 字面量单点）：扫描生产域 core/ api/ mcpserver/
scripts/ 全部 .py——代码位置（字符串赋值/路径拼接/调用实参）的
market_fapi 字符串常量命中仅 rest.py FAPI_DATA_DIR_DEFAULT 赋值
单点；注释与 docstring 位置命中豁免，白名单在案披露：
- core/data/fapi/rest.py 模块 docstring 与 default_data_dir
  docstring（解析语义自述——rest.py 本尊）；
- api/app.py create_app docstring 的 data_dir 参数段（装配缺省
  语义说明，改调后语义仍正确）；
- core/paper_trading/runtime/scheduler.py __init__ docstring 参数段
  与 _default_data_dir docstring（委托语义自述，A-2 第 6 条更新）。

断言二（core/ 零 __file__ 数据目录锚定回潮）：core/ 内代码位置
出现 `__file__` 路径锚定的文件白名单（非数据目录锚定或唯一单点）：
- core/data/fapi/rest.py：_REPO_ROOT——default_data_dir 的工程根
  锚定基点（A-1 落位，全工程唯一数据目录锚定单点）；
- core/persistence/db_ext.py：_REPO_ROOT——业务库（quant.db）
  路径锚定，非行情数据目录；
- core/logging/config.py：日志配置 project_root 推导；
- core/backtest/trace_resolver.py：engine.py 源码文件读取
  （trace 解析用，非目录锚定）。
白名单外文件出现 __file__ 锚定即红（典型回潮形：引擎/调度器绕开
单点自行 `Path(__file__).resolve().parents[N] / ...` 推导——原
fix-data-dir-single-source 消除的四引擎+scheduler 独立锚定链）。

收敛域边界（specs 披露）：works/ 研究一次性脚本与历史档案不在
收敛域（死档案、硬编码绝对路径、无运行时影响），本守卫不扫。
tests/ 域不在生产代码面（spec Scenario 语义限生产域）。
"""
from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

_REPO = Path(__file__).resolve().parents[2]

# 生产域（活跃目录全集——works/ 豁免见模块 docstring）
_PROD_DIRS = ("core", "api", "mcpserver", "scripts")

# 断言二白名单：core/ 内合法 __file__ 锚定文件（用途登记见 docstring）
_CORE_FILE_ANCHOR_WHITELIST = frozenset({
    "core/data/fapi/rest.py",
    "core/persistence/db_ext.py",
    "core/logging/config.py",
    "core/backtest/trace_resolver.py",
})


def _prod_python_files() -> list[Path]:
    """生产域源码树（活跃目录全部 .py，排序稳定）。"""
    files: list[Path] = []
    for sub in _PROD_DIRS:
        files.extend(sorted((_REPO / sub).rglob("*.py")))
    return files


def _comment_and_docstring_lines(text: str) -> set[int]:
    """纯注释行集（tokenize COMMENT + 行首 # 双条件）∪ docstring
    行集（ast——模块/类/函数体首语句字符串，先例
    test_no_backtest_import 同形制）。双条件堵两个误差方向：
    纯行首判断会对三引号字符串内以 # 开头的行误豁免，tokenize
    保证字符串内 # 不算注释 token；纯 tokenize 有行内尾注盲区
    （代码 + # 注释整行被吞），行首 # 保证只豁免纯注释行。"""
    comment_lines: set[int] = set()
    lines = text.splitlines()
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            # 只把纯注释行收进豁免集：行内尾注（代码 + # 注释）
            # 的代码部分仍在守卫扫描面，不放行
            if lines[tok.start[0] - 1].strip().startswith("#"):
                comment_lines.update(range(tok.start[0], tok.end[0] + 1))
    docstring_lines: set[int] = set()
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                doc = body[0].value
                docstring_lines.update(range(doc.lineno, doc.end_lineno + 1))
    return comment_lines | docstring_lines


class TestMarketFapiLiteralSinglePoint:
    """字面量单点：生产域代码位置 market_fapi 常量仅 rest.py 单点。"""

    def test_code_position_literals_confined_to_default_constant(self):
        """代码位置（字符串常量节点）命中仅 FAPI_DATA_DIR_DEFAULT
        赋值单点；注释/docstring 豁免（白名单见模块 docstring）。"""
        offenders: list[str] = []
        for path in _prod_python_files():
            rel = path.relative_to(_REPO).as_posix()
            text = path.read_text(encoding="utf-8")
            if "market_fapi" not in text:
                continue
            allowed_lines = _comment_and_docstring_lines(text)
            tree = ast.parse(text)
            parents: dict[int, ast.AST] = {}
            for parent in ast.walk(tree):
                for child in ast.iter_child_nodes(parent):
                    parents[id(child)] = parent
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Constant)
                        and isinstance(node.value, str)
                        and "market_fapi" in node.value):
                    continue
                if node.lineno in allowed_lines:
                    continue  # 注释/docstring 位置豁免（白名单披露）
                # 代码位置：唯一合法载体是 rest.py 的常量定义赋值
                parent = parents.get(id(node))
                is_default_const = (
                    rel == "core/data/fapi/rest.py"
                    and isinstance(parent, ast.Assign)
                    and any(isinstance(t, ast.Name)
                            and t.id == "FAPI_DATA_DIR_DEFAULT"
                            for t in parent.targets))
                if not is_default_const:
                    offenders.append(
                        f"{rel}:{node.lineno}: {node.value!r}")
        assert offenders == [], (
            "生产代码面 market_fapi 字面量仅 FAPI_DATA_DIR_DEFAULT 单点"
            "（data-directory-governance 字面量单点 Scenario；缺省目录"
            f"解析经 default_data_dir 单点）: {offenders}")


class TestCoreFileAnchorWhitelist:
    """core/ 零 __file__ 数据目录锚定回潮：锚定文件白名单外零命中。"""

    def test_file_anchor_confined_to_whitelist(self):
        """core/ 代码位置 __file__ 路径锚定仅白名单四文件（用途登记
        见模块 docstring）；白名单外文件出现 `__file__` 锚定即红
        （绕开 default_data_dir 自行推导的回潮面）。"""
        offenders: list[str] = []
        for path in sorted((_REPO / "core").rglob("*.py")):
            rel = path.relative_to(_REPO).as_posix()
            text = path.read_text(encoding="utf-8")
            if "__file__" not in text:
                continue
            allowed_lines = _comment_and_docstring_lines(text)
            for lineno, line in enumerate(text.splitlines(), start=1):
                if ("__file__" in line and lineno not in allowed_lines
                        and rel not in _CORE_FILE_ANCHOR_WHITELIST):
                    offenders.append(f"{rel}:{lineno}: {line.strip()}")
        assert offenders == [], (
            "core/ 的 __file__ 路径锚定仅白名单文件合法（rest.py 单点"
            "数据目录锚定＋三处非数据目录用途）；缺省数据目录解析"
            f"必须经 default_data_dir 单点: {offenders}")
