"""import 方向守卫（L2-H）——replay 包依赖契约（双书写形制扫描，
沿 tests/paper_trading/test_no_backtest_import.py 形制）。"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

REPLAY_DIR = Path(__file__).resolve().parents[2] / "core" / "replay"


def _replay_sources() -> list[Path]:
    return sorted(REPLAY_DIR.glob("*.py"))


class TestNoBacktestImport:
    """replay→core.backtest 禁止（新旧引擎零依赖）。"""

    def test_no_dot_form_import(self):
        """L2-H1：`core.backtest`（点形）零出现。"""
        offenders = [
            p.name for p in _replay_sources()
            if "core.backtest" in p.read_text(encoding="utf-8")]
        assert offenders == [], (
            f"replay 包出现 core.backtest 点形引用: {offenders}")

    def test_slash_form_only_in_docstring(self):
        """L2-H2：`core/backtest`（斜杠形）命中行仅允许出现在注释
        或 docstring 语义行（实现引用禁止）。"""
        for p in _replay_sources():
            for i, line in enumerate(
                    p.read_text(encoding="utf-8").splitlines(), 1):
                if "core/backtest" not in line:
                    continue
                stripped = line.strip()
                is_comment = stripped.startswith("#")
                in_doc = any(
                    m in line for m in ('"""', "'''", "——", "（", "）"))
                assert is_comment or in_doc, (
                    f"{p.name}:{i} 实现层引用 core/backtest: {line!r}")

    def test_paper_trading_no_reverse_import(self):
        """L2-H3：core/paper_trading/ 对 core.replay import 零命中
        （反向禁止——实时域不依赖回放域，包结构天然保证+守卫双轨）。"""
        pt_dir = REPLAY_DIR.parent / "paper_trading"
        offenders = []
        for p in sorted(pt_dir.rglob("*.py")):
            text = p.read_text(encoding="utf-8")
            if "from core.replay" in text or "import core.replay" in text:
                offenders.append(str(p.relative_to(pt_dir)))
        assert offenders == [], (
            f"paper_trading 反向依赖 replay: {offenders}")
