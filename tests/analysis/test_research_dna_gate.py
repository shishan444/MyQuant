"""研究链读库 DNA 校验红锚·直调层（signal-sources-structure-gate 0.3）。

契约出处（design 卡 R，Q-1 用户裁决=研究链挂全量 validate_dna，
2026-10-19 方案放行）：_load_strategy_dna（读库装载单点——隔离回测/
随机窗口/沙盒回测/分段验证四入口共用）在 from_json 成功后执行全量
结构/值域校验，不过即 raise ResearchError：
- code="RESEARCH_DNA_INVALID"；message=「库内策略 DNA 结构/值域非法:
  {strategy_id}」；status=422（缺省）；detail={"strategy_id": …,
  "errors": 判据明细}。
- 禁止未包装异常穿出（现状 signal_sources 裸下标/迭代炸链）与
  静默跳过（现状拼错型带绿灯跑完）。

红锚语义：直调层在卡 R 挂载前为红——现状 _load_strategy_dna 对
畸形 signal_sources 直接 return（拼错型）或炸 TypeError（无 ENTRY
基因+顶层非 list 形态经消费链）。断言从 design 卡 R 推导（不从实现
反推）。

入口覆盖分工：本文件直调单点+两轻量入口（random_window/sandbox，
hypothesis_id 缺省即达装载点）；isolated/staged 两入口经 HTTP 层
（tests/api/test_research_routes_dna_gate.py 四端点）全覆盖——四
入口×两层各证未绕过。
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.errors import ResearchError
from core.analysis.validation_service import (
    _load_strategy_dna, random_window_test, run_sandbox_backtest,
)
from core.persistence.db_ext import init_db_ext
from core.persistence.repository.strategy import save_strategy
from core.strategy.dna import RiskGenes, SignalGene, SignalRole, StrategyDNA


def _exit_gene() -> SignalGene:
    return SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                      "rsi", {"type": "cross_above", "threshold": 50.0})


def _dna_with_sources(sources) -> StrategyDNA:
    """合法骨架（entry/exit 齐全）+指定 signal_sources。"""
    return StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                       "rsi", {"type": "lt", "threshold": 35}),
            _exit_gene(),
        ],
        signal_sources=sources,
        risk_genes=RiskGenes(),
    )


@pytest.fixture()
def env(tmp_path: Path):
    """库+足量数据（500 根 4h bar——拼错型现状「带绿灯跑完」的静默
    失真形态可复现，红锚语义纯度；目标态装载点先拒，数据无关）。"""
    import numpy as np
    import pandas as pd

    db = tmp_path / "research.db"
    init_db_ext(db)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    n = 500
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC",
                        name="timestamp")
    rng = np.random.default_rng(5)
    close = 60000 + np.cumsum(rng.normal(0, 100, n))
    pd.DataFrame({
        "open": close, "high": close + 50, "low": close - 50,
        "close": close, "volume": rng.uniform(100, 800, n),
    }, index=idx).to_parquet(data_dir / "BTCUSDT_4h.parquet")
    return db, data_dir


BAD_SOURCES = {
    "拼错type": [{"type": "fomula"}],
    "非dict条目": [{"type": "rule"}, "x"],
    "缺ref": [{"type": "formula", "threshold": 0.5}],
    "顶层非list": 5,
}


def _seed_bad_row(db: Path, sid: str, sources) -> None:
    """直改库写畸形行（save_strategy 原语无校验——读库防线是被测面，
    直改库威胁模型即卡 R 的可达性前提）。"""
    save_strategy(
        db, strategy_id=sid, dna_json=_dna_with_sources(sources).to_json(),
        symbol="BTCUSDT", timeframe="4h", name=sid,
        gene_signature=f"sig-{sid}")


class TestLoadStrategyDnaRejectsMalformedSources:
    """单点直调：畸形 signal_sources → ResearchError 四元组齐备。"""

    @pytest.mark.parametrize("label", BAD_SOURCES, ids=list(BAD_SOURCES))
    def test_malformed_sources_raise_structured(self, env, label):
        """四形态畸形 → RESEARCH_DNA_INVALID+422+detail 含 strategy_id
        与 errors 判据明细（含条目序号定位——R-4 不吞定位细节）。
        ——现状直接 return（红）。"""
        db, _ = env
        sid = f"st-gate-{label}"
        _seed_bad_row(db, sid, BAD_SOURCES[label])

        with pytest.raises(ResearchError) as ei:
            _load_strategy_dna(db, sid)

        exc = ei.value
        assert exc.code == "RESEARCH_DNA_INVALID", (
            f"[{label}] 错误码错位：{exc.code}")
        assert exc.status == 422, f"[{label}] {exc.status}"
        assert f"库内策略 DNA 结构/值域非法: {sid}" == exc.message, (
            f"[{label}] message 形制偏差：{exc.message}")
        assert exc.detail["strategy_id"] == sid, f"[{label}] {exc.detail}"
        assert any("signal_sources" in e for e in exc.detail["errors"]), (
            f"[{label}] errors 未点名 signal_sources：{exc.detail}")

    def test_no_unwrapped_exception(self, env):
        """无未包装异常：非 dict 条目/顶层非 list 形态不裸穿
        （AttributeError/KeyError/TypeError）——AC-7「全程无 500」
        的服务层半面。四形态由参数化用例覆盖（均 pytest.raises
        ResearchError 语义），本条单独点名顶层非 list+无 ENTRY
        基因的炸穿组合形态。"""
        db, _ = env
        dna = StrategyDNA(
            signal_genes=[_exit_gene()],  # 无 ENTRY 基因
            signal_sources=5,
            risk_genes=RiskGenes(),
        )
        save_strategy(db, strategy_id="st-gate-crash", dna_json=dna.to_json(),
                      symbol="BTCUSDT", timeframe="4h", name="crash",
                      gene_signature="sig-crash")

        with pytest.raises(ResearchError) as ei:
            _load_strategy_dna(db, "st-gate-crash")
        assert ei.value.code == "RESEARCH_DNA_INVALID"

    def test_legal_dna_loads_unchanged(self, env):
        """合法 DNA 求值不变（绿态护栏）：rule 裸条目+formula 带 ref
        混合形态正常装载（挂载前后的行为不变面）。"""
        db, _ = env
        save_strategy(
            db, strategy_id="st-gate-legal",
            dna_json=_dna_with_sources(
                [{"type": "rule"},
                 {"type": "formula", "ref": "fx_demo"}]).to_json(),
            symbol="BTCUSDT", timeframe="4h", name="legal",
            gene_signature="sig-legal")

        dna, row = _load_strategy_dna(db, "st-gate-legal")

        assert dna.signal_sources == [
            {"type": "rule"}, {"type": "formula", "ref": "fx_demo"}]
        assert row["strategy_id"] == "st-gate-legal"


class TestEntriesNotBypassed:
    """入口未绕过证明：轻量两入口（装载点即拒绝，未进 warmup/回测）。"""

    def test_sandbox_entry_rejects_before_backtest(self, env):
        """run_sandbox_backtest：畸形行 → ResearchError
        RESEARCH_DNA_INVALID（装载点拦截——现状拼错型带绿灯跑完
        产出 metrics=静默失真，红锚要求显式拒绝）。"""
        db, data_dir = env
        _seed_bad_row(db, "st-gate-sb", [{"type": "fomula"}])

        with pytest.raises(ResearchError) as ei:
            run_sandbox_backtest(
                db, data_dir, strategy_id="st-gate-sb",
                symbol="BTCUSDT", timeframe="4h",
                start="2024-02-05", end="2024-03-01")

        assert ei.value.code == "RESEARCH_DNA_INVALID"

    def test_random_window_entry_rejects_before_sampling(self, env):
        """random_window_test：畸形行 → 同码拒绝（窗口抽样前拦截）。"""
        db, data_dir = env
        _seed_bad_row(db, "st-gate-rw", [{"type": "rule"}, "x"])

        with pytest.raises(ResearchError) as ei:
            random_window_test(
                db, data_dir, strategy_id="st-gate-rw",
                symbol="BTCUSDT", timeframe="4h",
                data_start="2024-01-01", data_end="2024-06-01")

        assert ei.value.code == "RESEARCH_DNA_INVALID"
