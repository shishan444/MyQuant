"""repay-vr-review-debts TD-02 源级防复发锚（任务 6.2）。

四个收敛文件（研究域 2+纸盘域 2）的裸 json.loads 命中行集合必须与
豁免清单精确相等——豁免仅 core/persistence/repository/research.py 的
parse_parent_ids 一处（裸串是合法落库形制——双形制归一解析器，
TD-16/CARD-21 背景，收敛即灾难）。新增裸解析=新增收敛点位漏网，
本锚即红。

沿 tests/api/test_vr_decision_retired.py 防复发锚先例形制（源文本
级断言）。锚灵敏度自证：实施时曾以临时越界行（在 research.py 顶部
临时插入 json.loads("x")）验证本锚能红后移除。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

_REPO_ROOT = Path(__file__).resolve().parents[2]

# 四个收敛文件（TD-02 八点位所在）——新裸解析必须显式进豁免清单
_CONVERGED_FILES = [
    "core/persistence/repository/research.py",
    "api/routes/research/validation_detail.py",
    "api/routes/paper_trading.py",
    "core/paper_trading/reporting.py",
]

# 豁免清单：file -> 裸 json.loads 允许出现的唯一函数名集合
# （research.py 的 parse_parent_ids——双形制归一解析器，设计豁免）
_EXEMPT = {
    "core/persistence/repository/research.py": {"parse_parent_ids"},
}


def _bare_loads_lines(rel: str) -> list[tuple[int, str]]:
    """文件内裸 json.loads 调用行（非通道内、非注释）。"""
    lines = (_REPO_ROOT / rel).read_text(encoding="utf-8").splitlines()
    hits = []
    for i, ln in enumerate(lines, 1):
        if "json.loads(" not in ln:
            continue
        code = ln.split("#", 1)[0].rstrip()
        if "json.loads(" not in code:
            continue  # 注释行（# 前无命中）不在禁域
        hits.append((i, code.strip()))
    return hits


def _enclosing_function(rel: str, lineno: int) -> str | None:
    """行号所属函数名（AST 定位——嵌套函数取最近内层）。"""
    import ast
    tree = ast.parse((_REPO_ROOT / rel).read_text(encoding="utf-8"))
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.lineno <= lineno <= max(
                    n.end_lineno or node.lineno
                    for n in ast.walk(node) if hasattr(n, "end_lineno")):
            if best is None or node.lineno > best.lineno:
                best = node
    return best.name if best else None


@pytest.mark.parametrize("rel", _CONVERGED_FILES)
def test_bare_json_loads_confined_to_exempt_functions(rel):
    """收敛文件内裸 json.loads 只允许出现在豁免函数体。"""
    allowed = _EXEMPT.get(rel, set())
    offenders = []
    for lineno, code in _bare_loads_lines(rel):
        fn = _enclosing_function(rel, lineno)
        if fn not in allowed:
            offenders.append(f"{rel}:{lineno} in {fn}: {code}")
    assert not offenders, (
        "收敛文件出现豁免清单外的裸 json.loads（新收敛点位漏网或"
        "通道旁路）:\n" + "\n".join(offenders))
