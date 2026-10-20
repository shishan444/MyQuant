"""进化任务入口 signal_sources 结构校验红锚
（signal-sources-structure-gate 0.4·进化面）。

契约出处（design 卡 V 生效面矩阵）：evolution.py:241 进化任务创建
的 initial_dna 校验块自动承袭 validate_dna 新判据（块本身不进卡 G
收敛面——A-1 保留内联第一段，无插件能力判据；判据单源生效）——
dict 形态畸形 → 422 DNA_VALIDATION_FAILED+任务无新行（坏祖先不
落库——population.init_population 祖先 append 无校验的扩散防线
前移）。

population 子面（种群初始化/子代变异）无可判定进入路径（assessor
核实：模板/变异产物不含 signal_sources 键——不变式保持而非实测
路径），AC-11 全量兜底，本文件不设用例。

红锚语义：两类畸形在卡 V 落地前为红（现状 201 落库——坏祖先
扩散机理现状可达）。非 dict 条目被 Pydantic 前拦（A-3 同款边界，
不设结构码断言）。
"""
from __future__ import annotations

import sqlite3
import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient  # noqa: F401（fixture 类型标注用）

from core.strategy.dna import RiskGenes, SignalGene, SignalRole, StrategyDNA


def _dna_with_sources(sources) -> StrategyDNA:
    return StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                       "rsi", {"type": "lt", "threshold": 35}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                       "rsi", {"type": "cross_above", "threshold": 50.0}),
        ],
        signal_sources=sources,
        risk_genes=RiskGenes(),
    )


def _task_count(db_path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM evolution_task").fetchone()[0]
    finally:
        conn.close()


class TestEvolutionInitialDnaGate:
    """initial_dna dict 形态畸形 → 422+任务无新行。"""

    @pytest.mark.parametrize(
        ("label", "sources"),
        [
            pytest.param("拼错type", [{"type": "fomula"}], id="拼错type"),
            pytest.param("缺ref", [{"type": "formula"}], id="缺ref"),
        ],
    )
    def test_bad_initial_dna_422_no_task(self, api_client, label, sources):
        """两类畸形经 POST /api/evolution/tasks → 422
        DNA_VALIDATION_FAILED+errors 点名+任务表计数不变（坏祖先不
        落库——现状 201，红）。"""
        import json as _json

        db = api_client.app.state.db_path
        before = _task_count(db)

        resp = api_client.post("/api/evolution/tasks", json={
            "symbol": "BTCUSDT", "timeframe": "4h",
            "initial_dna": _json.loads(
                _dna_with_sources(sources).to_json()),
        })

        assert resp.status_code == 422, (
            f"[{label}] {resp.status_code} {resp.text[:300]}")
        body = resp.json()["detail"]
        assert body["code"] == "DNA_VALIDATION_FAILED", f"[{label}] {body}"
        assert any("signal_sources" in e for e in body["errors"]), (
            f"[{label}] {body['errors']}")
        assert _task_count(db) == before, (
            f"[{label}] 拒绝后任务表出现新行（坏祖先落库）")

    def test_legal_initial_dna_creates_task(self, api_client):
        """对照（绿态护栏）：合法 initial_dna（rule 裸条目）→ 201
        任务创建（判据零误伤面——挂载前后都绿）。"""
        import json as _json

        resp = api_client.post("/api/evolution/tasks", json={
            "symbol": "BTCUSDT", "timeframe": "4h",
            "initial_dna": _json.loads(
                _dna_with_sources([{"type": "rule"}]).to_json()),
        })

        assert resp.status_code == 201, resp.text
