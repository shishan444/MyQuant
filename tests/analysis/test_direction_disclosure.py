"""做空假设通道——方向披露负向面（add-direction-channel A4 存量收缩）。

retire-legacy 任务 5.1/5.2 后旧三关（隔离/随机窗/分段）编排与端点已
退役，方向披露的正向断言面随宿主通道消失；本文件保留存活的负向锚：
沙盒返回体无 direction 键（产品范围是三关，沙盒不在披露面——守范围）。
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.persistence.db_ext import init_db_ext
from core.persistence.repository.strategy import save_strategy
from core.strategy.dna import (
    ExecutionGenes,
    LogicGenes,
    RiskGenes,
    SignalGene,
    SignalRole,
    StrategyDNA,
)


@pytest.fixture()
def env(tmp_path: Path):
    """500 bar 4h 数据 + 库；app.state 由用例内覆写（TestClient 时序）。"""
    rng = np.random.default_rng(7)
    n = 500
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC",
                        name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 120, n))
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    pd.DataFrame({
        "open": close, "high": close + 80, "low": close - 80,
        "close": close, "volume": rng.uniform(100, 1000, n), "trades": 100,
    }, index=idx).to_parquet(data_dir / "BTCUSDT_4h.parquet")
    db = tmp_path / "q.db"
    init_db_ext(db)
    return {"db": db, "data_dir": data_dir, "n": n}


def _make_draft(db: Path, strategy_id: str, direction: str):
    """构造方向披露用 draft 策略。

    signal-sources-structure-gate 卡 R 起研究链装载挂全量
    validate_dna（Q-1 裁决）——构造补 EXIT 基因（原单 ENTRY 构造
    过不了装载校验：缺 exit 是 validate_dna 语义下的坏 DNA，生产
    路径 translate 产物恒含 exit；direction 断言面不受影响）。
    """
    gene = SignalGene(
        indicator="RSI", params={"period": 14},
        role=SignalRole.ENTRY_TRIGGER, field_name="rsi_14",
        condition={"type": "lt", "threshold": 35})
    exit_gene = SignalGene(
        indicator="RSI", params={"period": 14},
        role=SignalRole.EXIT_TRIGGER, field_name="rsi_14",
        condition={"type": "cross_above", "threshold": 50.0})
    dna = StrategyDNA(
        signal_genes=[gene, exit_gene], logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes(direction=direction))
    save_strategy(db, strategy_id=strategy_id, dna_json=dna.to_json(),
                  symbol="BTCUSDT", timeframe="4h", name=f"dir-{direction}")


class TestSandboxNegative:
    def test_sandbox_has_no_direction_key(self, env, api_client):
        """A3-3：沙盒返回体无 direction 键（守范围——不判门槛不入记录）。"""
        api_client.app.state.db_path = env["db"]
        api_client.app.state.data_dir = env["data_dir"]
        _make_draft(env["db"], "st_sb", "short")
        resp = api_client.post("/api/research/backtest-sandbox", json={
            "strategy_id": "st_sb", "symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-03-01T00:00:00Z", "end": "2024-03-15T00:00:00Z"})
        assert resp.status_code == 200, resp.text
        assert "direction" not in resp.json()
