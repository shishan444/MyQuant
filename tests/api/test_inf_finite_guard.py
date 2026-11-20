"""inf/NaN 穿透封堵测试（fix-replay-list-channel CARD-03/D-1、D-2）。

三层特征网：①schema 上界 422——六 float 字段（replay 通道
initial_cash/fee/slippage+sandbox 通道 init_cash/fee/slippage）×
inf/nan 两形态参数化，Pydantic ge/le 对非有限值恒为假 → 422 且
无落库；②落库前有限性守卫——require_finite_values 单元直测
（嵌套路径点名/列表元素/正常直通）+replay 落库点
_guarded_replay_payload 真函数（坏值拒绝不落行/好值直通带
total_return 计算键）；③合法上界真实链路——恰上界值
（cash 1e12/fee 1.0）两通道 200 不回归。
"""
from __future__ import annotations

import json as _json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient

from core.analysis.errors import ResearchError, require_finite_values
from core.analysis.executor import ensure_research_tables
from core.persistence.db_ext import init_db_ext
from core.persistence.repository.strategy import save_strategy
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
    StrategyDNA)

INF = float("inf")
NAN = float("nan")
SID = "st_inf_guard"
# warmup 需 200 bar——start 距数据起点（2024-01-01）≥45 天（270 bar）
_START, _END = "2024-02-15", "2024-02-25"


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    """模块级环境：库+500 bar 合成行情+策略+app（轻量——上界真实
    链路各用例独立发请求）。"""
    tmp = tmp_path_factory.mktemp("inf_guard")
    db = tmp / "q.db"
    init_db_ext(db)
    ensure_research_tables(db)
    d = tmp / "market"
    d.mkdir()
    rng = np.random.default_rng(7)
    idx = pd.date_range("2024-01-01", periods=500, freq="4h", tz="UTC",
                        name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 90, 500))
    pd.DataFrame({"open": close, "high": close + 50, "low": close - 50,
                  "close": close, "volume": rng.uniform(100, 800, 500),
                  "trades": 40}, index=idx).to_parquet(
        d / "BTCUSDT_4h.parquet")
    dna = StrategyDNA(
        signal_genes=[SignalGene("RSI", {"period": 14},
                                 SignalRole.ENTRY_TRIGGER, "rsi_14",
                                 {"type": "lt", "threshold": 35}),
                      SignalGene("RSI", {"period": 14},
                                 SignalRole.EXIT_TRIGGER, "rsi_14",
                                 {"type": "gt", "threshold": 1e9})],
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes())
    save_strategy(db, strategy_id=SID, dna_json=dna.to_json(),
                  symbol="BTCUSDT", timeframe="4h")
    from api.app import create_app
    app = create_app(db_path=db, data_dir=d)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _post(c: TestClient, url: str, body: dict):
    """统一走 content=json.dumps（默认 allow_nan=True 产出 Infinity/
    NaN 字面量）——httpx json= 参数客户端侧 allow_nan=False 直接
    ValueError 发不出请求；字面量形态即 D-1 威胁模型（非标准客户端
    发非法 JSON 字面量，服务端 json.loads 宽容解析回 inf 进校验）。"""
    return c.post(url, content=_json.dumps(body),
                  headers={"Content-Type": "application/json"})


def _replay_body(**over) -> dict:
    body = {"strategy_id": SID, "start": _START, "end": _END}
    body.update(over)
    return body


def _sandbox_body(**over) -> dict:
    body = {"strategy_id": SID, "symbol": "BTCUSDT", "timeframe": "4h",
            "start": _START, "end": _END}
    body.update(over)
    return body


def _total(c: TestClient) -> int:
    return c.get("/api/research/validation-records",
                 params={"limit": 1}).json()["total"]


class TestSchemaUpperBound422:
    """D-1：schema le 上界——inf/nan 对任何比较为假 → 422+零落库。"""

    @pytest.mark.parametrize(("field", "value"), [
        pytest.param("initial_cash", INF, id="replay.initial_cash=inf"),
        pytest.param("initial_cash", NAN, id="replay.initial_cash=nan"),
        pytest.param("fee", INF, id="replay.fee=inf"),
        pytest.param("fee", NAN, id="replay.fee=nan"),
        pytest.param("slippage", INF, id="replay.slippage=inf"),
        pytest.param("slippage", NAN, id="replay.slippage=nan"),
    ])
    def test_replay_inf_nan_422_no_row(self, env, field, value):
        before = _total(env)
        r = _post(env, "/api/research/backtest-replay",
                  _replay_body(**{field: value}))
        assert r.status_code == 422, r.text
        assert _total(env) == before, "422 拒绝不得落库"

    @pytest.mark.parametrize(("field", "value"), [
        pytest.param("init_cash", INF, id="sandbox.init_cash=inf"),
        pytest.param("init_cash", NAN, id="sandbox.init_cash=nan"),
        pytest.param("fee", INF, id="sandbox.fee=inf"),
        pytest.param("fee", NAN, id="sandbox.fee=nan"),
        pytest.param("slippage", INF, id="sandbox.slippage=inf"),
        pytest.param("slippage", NAN, id="sandbox.slippage=nan"),
    ])
    def test_sandbox_inf_nan_422_no_row(self, env, field, value):
        before = _total(env)
        r = _post(env, "/api/research/backtest-sandbox",
                  _sandbox_body(**{field: value}))
        assert r.status_code == 422, r.text
        assert _total(env) == before, "422 拒绝不得落库"

class TestResponseBodyForm:
    """422 出站体形态（CARD-03/D-1 修复面特征锚）：非有限 input
    净化为字符串形（出站体可解析——修复前兜底 500 纯文本）；普通
    越界 detail 数值原样（复刻默认形制无行为漂移）。"""

    def test_inf_input_sanitized_in_detail(self, env):
        r = _post(env, "/api/research/backtest-replay",
                  _replay_body(fee=INF))
        assert r.status_code == 422
        body = r.json()  # 修复前此处 json 解析失败（500 纯文本）
        assert isinstance(body["detail"], list)
        err0 = body["detail"][0]
        assert list(err0["loc"]) == ["body", "fee"]
        assert err0["input"] == "inf"  # 净化字符串形（排障信息保留）

    def test_plain_overbound_detail_unchanged(self, env):
        r = _post(env, "/api/research/backtest-replay",
                  _replay_body(fee=2.0))
        assert r.status_code == 422
        err0 = r.json()["detail"][0]
        assert list(err0["loc"]) == ["body", "fee"]
        assert err0["input"] == 2.0  # 有限值原样数值


class TestRequireFiniteValues:
    """D-2 写侧守卫单元直测：递归遍历+首坏值路径点名+错误四元组。"""

    def test_nested_single_bad_value_rejected_with_path(self):
        # 唯一坏值形态——field 路径与 value 字符串形确定性断言
        with pytest.raises(ResearchError) as ei:
            require_finite_values(
                {"replay": {"task_id": "t1", "final_balance": NAN,
                            "final_equity": 99100.0}},
                source="validation_records.result",
                detail={"record_id": "rec_x"})
        err = ei.value
        assert err.code == "RESEARCH_RESULT_NON_FINITE"
        assert err.status == 422
        assert err.detail["field"] == "replay.final_balance"
        assert err.detail["value"] == "nan"
        assert err.detail["record_id"] == "rec_x"  # 附加上下文合并保留

    def test_list_element_rejected_with_index_path(self):
        with pytest.raises(ResearchError) as ei:
            require_finite_values(
                {"metrics": [0.1, -2.0, INF]}, source="t.f")
        assert ei.value.detail["field"] == "metrics[2]"
        assert ei.value.detail["value"] == "inf"

    def test_finite_values_pass(self):
        # 正常混合结构直通（嵌套 dict/list/边界值 0/负数/大值）
        require_finite_values(
            {"replay": {"total_pnl": -1.5, "ratios": [0.0, 1e308]},
             "n": 3, "s": "inf", "none": None, "flag": True},
            source="t.f")  # 不抛即通过

    def test_non_float_scalars_pass(self):
        # int 任意大/str/None/bool 无非有限形态直通（设计声明）
        require_finite_values(
            {"big": 10**400, "lit": "nan", "b": False}, source="t.f")

class TestSanitizePurity:
    """出站净化函数纯性面（阶段四属性补强）：幂等+结构保持+有限值
    恒等——纯函数无状态可任意重放。"""

    @staticmethod
    def _sanitize(obj):
        from api.app import _sanitize_non_finite
        return _sanitize_non_finite(obj)

    def test_idempotent(self):
        # sanitize(sanitize(x)) == sanitize(x)：字符串形二次净化原样
        x = {"a": INF, "b": [NAN, -INF, 1.5], "c": {"d": "inf"}}
        once = self._sanitize(x)
        assert self._sanitize(once) == once

    def test_structure_preserved_only_floats_stringified(self):
        x = {"replay": {"equity": INF, "pnl": -2.0}, "rows": [NAN, 0]}
        out = self._sanitize(x)
        assert set(out) == {"replay", "rows"}  # 键结构保持
        assert isinstance(out["rows"], list) and len(out["rows"]) == 2
        assert out["replay"]["equity"] == "inf"
        assert out["replay"]["pnl"] == -2.0   # 有限值恒等（数值原样）
        assert out["rows"] == ["nan", 0]

    def test_scalars_passthrough(self):
        # str/None/bool/int 树原样（jsonable_encoder 产物的旁支形态）
        x = {"s": "Infinity", "n": None, "b": True, "i": 10**30}
        assert self._sanitize(x) == x


class TestGuardedReplayPayload:
    """D-2 replay 落库点守卫（真函数直调——引擎溢出形态注入）。"""

    @staticmethod
    def _result(**over):
        base = {"task_id": "t_g", "status": "completed",
                "stop_reason": "end_of_data", "bars_processed": 90,
                "total_trades": 5, "total_pnl": -120.0,
                "final_balance": 99880.0, "final_equity": 99880.0}
        base.update(over)
        return SimpleNamespace(**base)

    @staticmethod
    def _payload():
        from api.schemas import ReplayBacktestRequest
        return ReplayBacktestRequest(
            strategy_id=SID, start=_START, end=_END,
            initial_cash=100000.0)

    def test_non_finite_rejected(self):
        from api.routes.research.validation import _guarded_replay_payload
        with pytest.raises(ResearchError) as ei:
            _guarded_replay_payload(
                self._payload(),
                self._result(final_balance=NAN, final_equity=99000.0),
                "rec_g1")
        err = ei.value
        assert err.code == "RESEARCH_RESULT_NON_FINITE"
        assert err.status == 422
        # 唯一坏值（final_balance=nan；total_return 由好值算出有限）
        assert err.detail["field"] == "replay.final_balance"
        assert err.detail["record_id"] == "rec_g1"

    def test_inf_equity_rejected_any_first_path(self):
        # final_equity=inf 连带 total_return=inf 两坏值——契约承诺
        # 「拒绝+点名首坏值路径」，命中哪个是实现遍历细节，断言集合
        from api.routes.research.validation import _guarded_replay_payload
        with pytest.raises(ResearchError) as ei:
            _guarded_replay_payload(
                self._payload(), self._result(final_equity=INF), "rec_g2")
        assert ei.value.detail["field"] in {
            "replay.final_equity", "replay.total_return"}

    def test_finite_result_passes_with_calc_key(self):
        # 好值直通：九键载荷+total_return 计算键（守卫不误伤正常路径）
        from api.routes.research.validation import _guarded_replay_payload
        payload = _guarded_replay_payload(
            self._payload(), self._result(), "rec_g3")
        rp = payload["replay"]
        assert set(rp) == {"task_id", "status", "stop_reason",
                           "bars_processed", "total_trades", "total_pnl",
                           "final_balance", "final_equity", "total_return"}
        assert rp["total_return"] == pytest.approx(99880.0 / 100000.0 - 1)


class TestUpperBoundAccepted:
    """正常路径不回归：恰上界值两通道 200（右闭边界）。"""

    def test_replay_upper_bound_200(self, env):
        r = _post(env, "/api/research/backtest-replay",
                  _replay_body(initial_cash=1e12, fee=1.0,
                               slippage=1.0))
        assert r.status_code == 200, r.text
        assert r.json()["record_id"].startswith("rec_")

    def test_sandbox_upper_bound_200(self, env):
        r = _post(env, "/api/research/backtest-sandbox",
                  _sandbox_body(init_cash=1e12, fee=1.0,
                                slippage=1.0))
        assert r.status_code == 200, r.text
        assert r.json()["record_id"].startswith("rec_")
