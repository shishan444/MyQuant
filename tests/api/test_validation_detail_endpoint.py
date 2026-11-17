"""add-validation-detail B2 读取端点契约（tester B2 组 1-9）。

两态判定序（404 资源不存在/200 空骨架两义分立）+ 三 level 份结构
对齐 + equity 双形制透传 + 历史 null 容缺 + 纯 GET 幂等。

正向用例明细行数据源=真实落盘链路（任务 4 三入口实际执行——契约
测试可信度锚=被测数据走生产写入路径）；读取面降级形制透传允许
ORM 直插降级行证如实透传（tasks.md 完成标准补充⑥）。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.hypothesis_service import register_hypothesis
from core.analysis.validation_service import run_sandbox_backtest
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import research as rr
from core.persistence.repository.strategy import save_strategy
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
    StrategyDNA, TimeframeLayer,
)

_RSI_GENE = lambda: SignalGene(  # noqa: E731 —— 与既有测试同构的构造捷径
    "RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
    {"type": "lt", "threshold": 35})

_EXIT_GENE = lambda: SignalGene(  # noqa: E731
    "RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, "rsi_14",
    {"type": "gt", "threshold": 65})


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """覆写 conftest 空路径形：建满研究域十表（含两明细表）。"""
    d = tmp_path / "test.db"
    init_db_ext(d)
    from core.analysis.executor import ensure_research_tables
    ensure_research_tables(d)
    return d


@pytest.fixture()
def tmp_data_dir(tmp_path: Path) -> Path:
    """覆写 conftest 单行形：2000 bar 连续 4h 数据（真实回测可跑）。"""
    d = tmp_path / "data"
    d.mkdir()
    rng = np.random.default_rng(7)
    idx = pd.date_range("2022-10-01", periods=2000, freq="4h", tz="UTC",
                        name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 110, 2000))
    pd.DataFrame({"open": close, "high": close + 60, "low": close - 60,
                  "close": close, "volume": rng.uniform(100, 800, 2000),
                  "trades": 40}, index=idx).to_parquet(d / "BTCUSDT_4h.parquet")
    return d


@pytest.fixture()
def hypothesis_id(db_path) -> str:
    return register_hypothesis(
        db_path,
        conditions={
            "when_conditions": [{"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt", "target": 35}],
            "then_conditions": [{"subject": "close", "action": "gt", "target": 0}],
        },
        statement="s",
        data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                      "start": "2023-01-01T00:00:00Z",
                      "end": "2023-02-01T00:00:00Z"}],
        strategy_params={"stop_loss": 0.05})["hypothesis_id"]


@pytest.fixture()
def strategy_saved(db_path) -> str:
    dna = StrategyDNA(
        signal_genes=[_RSI_GENE(), _EXIT_GENE()],
        layers=[TimeframeLayer(timeframe="4h",
                               signal_genes=[_RSI_GENE(), _EXIT_GENE()],
                               logic_genes=LogicGenes())],
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes())
    save_strategy(db_path, strategy_id="st_detail", dna_json=dna.to_json(),
                  symbol="BTCUSDT", timeframe="4h")
    return "st_detail"


@pytest.fixture()
def svc_db(db_path):
    """服务层直跑用的同库路径别名（与 api_client 同库——真实链路写、
    HTTP 读）。"""
    return db_path


class TestTwoStates:
    """B2-1/B2-7：404 与 200+false 两义分立可区分。"""

    def test_unknown_record_404(self, api_client):
        r = api_client.get("/api/research/validation-records/rec_none/detail")
        assert r.status_code == 404
        body = r.json()["detail"]  # HTTPException detail 载荷（先例形制）
        assert body["code"] == "RESEARCH_RECORD_NOT_FOUND"
        assert body["detail"] is None

    def test_record_without_detail_200_empty_skeleton(
            self, api_client, db_path, hypothesis_id):
        """记录存在而明细行不存在（历史形）→ 200 空骨架：三键 null
        如实缺失、元数据正常、不回填不重跑。"""
        from tests.helpers.vr_factory import insert_validation_record
        insert_validation_record(
            db_path, record_id="rec_hist_api", hypothesis_id=hypothesis_id,
            strategy_id="st_x", level="isolated_backtest", params={},
            result={}, verdict="pass", range_hash="rh_api1")
        r = api_client.get(
            "/api/research/validation-records/rec_hist_api/detail")
        assert r.status_code == 200
        body = r.json()
        assert body["detail_available"] is False
        assert body["executions"] == []
        assert body["init_cash"] is None and body["fee"] is None \
            and body["slippage"] is None
        assert body["level"] == "isolated_backtest"
        assert body["verdict"] == "pass"
        assert body["hypothesis_id"] == hypothesis_id

    def test_two_states_distinguishable(self, api_client, db_path,
                                        hypothesis_id):
        """两义判别键：404（记录不存在）≠ 200+false（明细未落盘）。"""
        r404 = api_client.get(
            "/api/research/validation-records/rec_ghost_zz/detail")
        assert r404.status_code == 404
        from tests.helpers.vr_factory import insert_validation_record
        insert_validation_record(
            db_path, record_id="rec_hist_api2", hypothesis_id=hypothesis_id,
            strategy_id="st_x", level="isolated_backtest", params={},
            result={}, verdict="fail", range_hash="rh_api2")
        r200 = api_client.get(
            "/api/research/validation-records/rec_hist_api2/detail")
        assert r200.status_code == 200
        assert r200.json()["detail_available"] is False


class TestSandboxPositive:
    """B2 正向（真实落盘链路）：sandbox 单份结构+幂等 GET（K1 后
    沙盒记录携带明细——retire-legacy 前该面由 isolated 链承载）。"""

    def test_sandbox_detail_via_real_pipeline(
            self, api_client, svc_db, tmp_data_dir, hypothesis_id,
            strategy_saved):
        api_client.app.state.db_path = svc_db
        api_client.app.state.data_dir = tmp_data_dir
        out = run_sandbox_backtest(
            svc_db, tmp_data_dir, strategy_id=strategy_saved,
            hypothesis_id=hypothesis_id, symbol="BTCUSDT", timeframe="4h",
            start="2023-03-01T00:00:00Z", end="2023-04-01T00:00:00Z")
        r = api_client.get(
            f"/api/research/validation-records/{out['record_id']}/detail")
        assert r.status_code == 200
        body = r.json()
        assert body["detail_available"] is True
        assert body["init_cash"] == 100000.0 and body["fee"] == 0.001 \
            and body["slippage"] == 0.0005, "B2-7：三键=实际生效值"
        assert len(body["executions"]) == 1
        ex = body["executions"][0]
        assert ex["exec_ref"] == "main" and ex["exec_index"] == 0
        assert ex["range_start"] == "2023-03-01T00:00:00Z"
        # B2-9：逐笔笔数==metrics.total_trades（对账锚）
        assert len(ex["trades"]) == out["metrics"]["total_trades"]
        for t in ex["trades"]:
            # decision 键退役（retire-vr-decision-json——键不在场断言）
            assert set(t) == {"seq", "entry_time", "exit_time",
                              "entry_price", "exit_price", "size",
                              "direction", "pnl"}
            assert t["direction"] in ("Long", "Short")
        # 紧凑形制透传（fixture 数据连续无洞）
        assert ex["equity"]["grid_aligned"] is True
        assert ex["equity"]["timestamps"] is None
        assert ex["equity"]["length"] == len(ex["equity"]["values"])
        # 纯 GET 幂等：重复调用同响应
        r2 = api_client.get(
            f"/api/research/validation-records/{out['record_id']}/detail")
        assert r2.json() == body


class TestOrphanTradesTolerance:
    """读取面孤儿 trades 容错（equity 行缺失而 trades 行在——四写
    原子性下理论不可达，仓储层兜底分支由直插触达锚定）：孤儿份挂入
    executions、range 空串如实呈现不丢弃。"""

    def test_orphan_trade_rows_surfaced(self, api_client, db_path,
                                        hypothesis_id):
        from tests.helpers.vr_factory import insert_validation_record
        insert_validation_record(
            db_path, record_id="rec_orphan", hypothesis_id=hypothesis_id,
            strategy_id="st_x", level="isolated_backtest", params={},
            result={}, verdict="pass", range_hash="rh_orp")
        from core.persistence.orm.engine import engine_for
        from core.persistence.orm.models import VrTradeDetail
        from sqlalchemy import insert
        with engine_for(db_path).begin() as conn:
            conn.execute(insert(VrTradeDetail).values(
                record_id="rec_orphan", exec_ref="main", exec_index=0,
                range_start="2023-03-01T00:00:00Z",
                range_end="2023-04-01T00:00:00Z", seq=0,
                entry_time="2023-03-02T00:00:00Z", exit_time=None,
                entry_price=100.0, exit_price=105.0, size=1.0,
                direction="Long", pnl=5.0))
        r = api_client.get(
            "/api/research/validation-records/rec_orphan/detail")
        assert r.status_code == 200
        body = r.json()
        assert body["detail_available"] is True, "有明细行即可复核"
        assert len(body["executions"]) == 1
        ex = body["executions"][0]
        assert ex["exec_ref"] == "main" and ex["exec_index"] == 0
        assert len(ex["trades"]) == 1
        assert ex["trades"][0]["pnl"] == 5.0
        # 空曲线骨架在场（不静默丢逐笔证据——防御分支的完整承诺）
        assert ex["equity"] == {"grid_aligned": True, "timeframe": "",
                                "start": None, "length": 0,
                                "timestamps": None, "values": []}


class TestDegradedPassthrough:
    """读取面降级形制透传（允许 ORM 直插降级行证如实透传——不重建
    时间轴）。"""

    def test_degraded_equity_timestamps_passthrough(
            self, api_client, db_path, hypothesis_id):
        from tests.helpers.vr_factory import insert_validation_record
        insert_validation_record(
            db_path, record_id="rec_degraded", hypothesis_id=hypothesis_id,
            strategy_id="st_x", level="isolated_backtest",
            params={"init_cash": 1.0, "fee": 2.0, "slippage": 3.0},
            result={}, verdict="pass", range_hash="rh_deg")
        from core.persistence.orm.engine import engine_for
        from core.persistence.orm.models import VrEquityCurve
        from sqlalchemy import insert
        ts = ["2023-03-01 00:00:00+00:00", "2023-03-01 08:00:00+00:00",
              "2023-03-01 20:00:00+00:00"]  # 中间有洞的显式数组
        with engine_for(db_path).begin() as conn:
            conn.execute(insert(VrEquityCurve).values(
                record_id="rec_degraded", exec_ref="main", exec_index=0,
                range_start="s", range_end="e", grid_aligned=0,
                timeframe="4h", start=None, length=None,
                timestamps=json.dumps(ts),
                values=json.dumps([100.0, 101.0, 102.0])))
        r = api_client.get(
            "/api/research/validation-records/rec_degraded/detail")
        assert r.status_code == 200
        body = r.json()
        ex = body["executions"][0]
        eq = ex["equity"]
        assert eq["grid_aligned"] is False
        assert eq["timestamps"] == ts, "降级 timestamps 原样返回不重建"
        assert eq["values"] == [100.0, 101.0, 102.0]
        assert len(eq["timestamps"]) == len(eq["values"]), \
            "B2-9 降级形态对账：len(timestamps)==len(values)"
        # 直插 params 三键读取侧如实呈现（值任取——读取面无值域约束）
        assert body["init_cash"] == 1.0 and body["fee"] == 2.0


class TestStoredJsonCorruptRejected:
    """repay-vr-review-debts TD-02：库内 JSON 列损坏 → 422
    RESEARCH_STORED_DATA_INVALID 结构化拒绝（点位 1-4：equity
    timestamps/values + params/result），不裸穿 500。"""

    def _seed_record(self, db_path, hypothesis_id, *, params="{}",
                     result="{}"):
        from tests.helpers.vr_factory import insert_validation_record
        insert_validation_record(
            db_path, record_id="rec_corrupt", hypothesis_id=hypothesis_id,
            strategy_id="st_x", level="isolated_backtest",
            params=params if isinstance(params, str) else params,
            result=result if isinstance(result, str) else result,
            verdict="pass", range_hash="rh_corrupt")

    def _seed_equity(self, db_path, timestamps, values):
        from core.persistence.orm.engine import engine_for
        from core.persistence.orm.models import VrEquityCurve
        from sqlalchemy import insert
        with engine_for(db_path).begin() as conn:
            conn.execute(insert(VrEquityCurve).values(
                record_id="rec_corrupt", exec_ref="main", exec_index=0,
                range_start="s", range_end="e", grid_aligned=0,
                timeframe="4h", start=None, length=None,
                timestamps=timestamps, values=values))

    def _get(self, api_client):
        return api_client.get(
            "/api/research/validation-records/rec_corrupt/detail")

    @staticmethod
    def _assert_422(r, source_field, extra_keys):
        assert r.status_code == 422, r.text
        body = r.json()["detail"]
        assert body["code"] == "RESEARCH_STORED_DATA_INVALID"
        assert source_field in body["message"], body["message"]
        assert body["detail"]["record_id"] == "rec_corrupt"
        for k in extra_keys:
            assert k in body["detail"]
        assert "parse_error" in body["detail"]

    def test_corrupt_params_rejected(self, api_client, db_path,
                                     hypothesis_id):
        self._seed_record(db_path, hypothesis_id,
                          params='{"init_cash": 1.0,', result="{}")
        self._assert_422(self._get(api_client),
                         "validation_records.params", [])

    def test_corrupt_result_rejected(self, api_client, db_path,
                                     hypothesis_id):
        self._seed_record(db_path, hypothesis_id, params="{}",
                          result='{"metrics": {"trades": 3}')
        self._assert_422(self._get(api_client),
                         "validation_records.result", [])

    def test_corrupt_equity_timestamps_rejected(self, api_client, db_path,
                                                hypothesis_id):
        self._seed_record(db_path, hypothesis_id)
        self._seed_equity(db_path, '["2023-03-01", "2023-03', '[100.0]')
        self._assert_422(self._get(api_client),
                         "vr_equity_curve.timestamps",
                         ["exec_ref", "exec_index"])

    def test_corrupt_equity_values_rejected(self, api_client, db_path,
                                            hypothesis_id):
        self._seed_record(db_path, hypothesis_id)
        self._seed_equity(db_path,
                          '["2023-03-01T00:00:00+00:00"]', '[100.0, 101')
        self._assert_422(self._get(api_client),
                         "vr_equity_curve.values",
                         ["exec_ref", "exec_index"])
