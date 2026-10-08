"""ResearchError 单一定义与轻量 import 链（CARD-02 回归锚）。

错误类型唯一权威定义在 core/analysis/errors.py（零第三方依赖零域内
反向依赖），hypothesis_service 模块头 import re-export 同一对象——
全域 38+ 文件 ``from ...hypothesis_service import ResearchError`` 面
零适配的前提是身份同一（is 同一类对象，except/isinstance 跨路径
成立）；轻消费方（event_policy / signal_assembly）import 不拖
sqlalchemy / core.persistence 重链。

已知事实（回归证据，非契约）：param_guard 虽已切 import 自 errors，
但其身处 templates 包内、包 __init__ 无条件全量 import 九模板（含
自装载的 precheck_hypothesis），包语义下断链不可达——import
param_guard 仍拖全链（139 模块）。包 __init__ 懒加载属独立改造，
本文件不锚该现状。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

_REPO_ROOT = Path(__file__).resolve().parents[2]

_CHAIN_PROBE = (
    "import sys, json; import {module}; "
    "print(json.dumps([m for m in sys.modules "
    "if m.startswith('sqlalchemy') "
    "or m.startswith('core.persistence')]))"
)


class TestSingleDefinition:
    """单一定义与 re-export 身份（CARD-02 核心契约）。"""

    def test_re_export_身份同一(self) -> None:
        from core.analysis import errors
        from core.analysis import hypothesis_service as hs
        assert hs.ResearchError is errors.ResearchError

    def test_四属性语义(self) -> None:
        from core.analysis.errors import ResearchError
        exc = ResearchError("X", "msg", status=409, detail={"k": 1})
        assert (exc.code, exc.message, exc.status, exc.detail) == \
            ("X", "msg", 409, {"k": 1})
        assert str(exc) == "msg"

    def test_跨路径捕获同身份(self) -> None:
        """event_policy（errors 直取方）抛 → hypothesis_service 路径的
        except 同身份捕获（全域 38+ import 面零适配的行为证据）。"""
        from core.analysis import hypothesis_service as hs
        from core.analysis.event_policy import validate_policy
        with pytest.raises(hs.ResearchError) as ei:
            validate_policy({"mode": "bogus"})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.status == 422


class TestLightweightImportChain:
    """轻消费方断链（干净子进程验证——pytest 进程自身已加载重链，
    本进程内验证会自污染失真）。"""

    @pytest.mark.parametrize("module", [
        "core.analysis.event_policy",
        "core.analysis.signal_assembly",
    ])
    def test_轻消费方_import_不拖重链(self, module: str) -> None:
        out = subprocess.run(
            [sys.executable, "-c", _CHAIN_PROBE.format(module=module)],
            capture_output=True, text=True, cwd=_REPO_ROOT)
        assert out.returncode == 0, out.stderr
        assert json.loads(out.stdout) == []   # 重链零模块（断链达成）
