"""件⑦计数词消亡的 analysis 面红界（dna-strong-typing-and-disclosure-
hygiene，AC-7/设计 3.2 三书写点之一）。

契约出处：三书写点统一不写计数——api/routes/research/analysis.py
装载链 docstring 的「五步，」已删（「（五步，与 precheck 装载同构）」
→「（与 precheck 装载同构）」）；capability.py 面已有锚承载
（_disclosure_anchors.py G5-能力自述-装载计数词清零），spec 面随
delta；analysis.py 不在锚清单扫描域（终裁扫描域=两声明文件），
本文件补定向单文件红界——沿 test_exit_sweep.py 件⑥清零断言先例
（assert-not-in 定向防复发）。

边界写明：行内流程序号注释（「五步之①」「插件列五步链」）不在
design 3.2 清单、属合法在场（tasks 3.2 裁量不动），故断言只锚
docstring 改动点的特定回流形（「五步，与 precheck」），不做全文件
「五步」清零。
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

_SOURCE = Path(__file__).resolve().parents[2] / (
    "api/routes/research/analysis.py")


def test_loading_chain_step_count_cleared_in_docstring():
    """装载链 docstring 计数词消亡锚：改动点现状=「（与 precheck
    装载同构——两入口共用权威处理）」，计数形「五步，与 precheck」
    不得回流（spec 教四步、实现五步——计数词即漂移源）。"""
    source = _SOURCE.read_text(encoding="utf-8")
    assert "五步，与 precheck" not in source, (
        "装载链 docstring 计数词回流（件⑦消亡红界）——流程枚举本身"
        "即权威描述，不写计数")
    assert "与 precheck 装载同构——两入口" in source, (
        "装载链同构句现状形不在场——docstring 被改写，同步本锚")
