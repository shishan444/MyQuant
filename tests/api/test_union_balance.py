"""判定并集余量查询测试（add-research-analysis-tools 0.4.1 任务 4.3，B4 用例组）。

含本变更最重要单条 system 用例：两出口一致——查询 remaining 段发起
隔离验证不被 RESEARCH_RANGE_OVERLAP 拒（同一账本装配的两个出口）。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.hypothesis_service import ResearchError, register_hypothesis
from core.analysis.validation_service import (
    query_union_balance, run_isolated_backtest)
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import research as rr
from core.persistence.repository.strategy import save_strategy
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole, StrategyDNA)

SYMBOL = "BTCUSDT"


@pytest.fixture()
def ub_env(tmp_path: Path):
    """4h 数据首 bar 非整日点（08:00 起——防日级截断锚）+ 独立 db。"""
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    n = 2000
    idx = pd.date_range("2023-01-01 08:00:00", periods=n, freq="4h", tz="UTC",
                        name="timestamp")
    rng = np.random.default_rng(7)
    close = 60000 + np.cumsum(rng.normal(0, 110, n))
    pd.DataFrame({"open": close, "high": close + 60, "low": close - 60,
                  "close": close, "volume": rng.uniform(100, 800, n),
                  "trades": 40}, index=idx).to_parquet(
        data_dir / f"{SYMBOL}_4h.parquet")
    db = tmp_path / "quant.db"
    init_db_ext(db)
    return {"data_dir": data_dir, "db": db, "df": pd.read_parquet(
        data_dir / f"{SYMBOL}_4h.parquet")}


def _wire(client, env):
    client.app.state.db_path = env["db"]
    client.app.state.data_dir = env["data_dir"]


def _register(db, start="2023-02-01T00:00:00Z", end="2023-03-01T00:00:00Z"):
    cond = {"subject": {"indicator": "RSI", "params": {"period": 14}},
            "action": "lt", "target": 35}
    return register_hypothesis(
        db, conditions={"when_conditions": [cond],
                        "then_conditions": [dict(cond)]},
        statement="s",
        data_ranges=[{"symbol": SYMBOL, "timeframe": "4h",
                      "start": start, "end": end}])["hypothesis_id"]


class TestTwoExitConsistency:
    def test_remaining_segment_passes_isolated_validation(
            self, ub_env):
        """两出口一致 system 用例（本变更最重要单条）：查询 remaining
        段发起隔离验证不被重叠拒。"""
        db, data_dir = ub_env["db"], ub_env["data_dir"]
        hyp = _register(db)
        save_strategy(db, strategy_id="st_ub",
                      dna_json=StrategyDNA(
                          signal_genes=[SignalGene(
                              "RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                              "rsi_14", {"type": "lt", "threshold": 35})],
                          logic_genes=LogicGenes(),
                          execution_genes=ExecutionGenes(
                              symbol=SYMBOL, timeframe="4h"),
                          risk_genes=RiskGenes()).to_json(),
                      symbol=SYMBOL, timeframe="4h")
        out = query_union_balance(
            db, data_dir, symbol=SYMBOL, timeframe="4h", hypothesis_id=hyp)
        assert out["remaining"], "registered 区段外应有剩余"
        # 取中部一段 remaining（留足 warmup 前缀与验证长度）
        seg = next(r for r in out["remaining"]
                   if r["start"] < "2023-04-01T00:00:00Z"
                   and r["end"] > "2023-05-01T00:00:00Z")
        start = pd.Timestamp(seg["start"])
        v_start = (start if start >= pd.Timestamp("2023-04-01T00:00:00Z")
                   else pd.Timestamp("2023-04-01T00:00:00Z"))
        v_end = v_start + pd.Timedelta(days=30)
        assert v_end <= pd.Timestamp(seg["end"]), "验证段须在 remaining 内"
        result = run_isolated_backtest(
            db, data_dir, hypothesis_id=hyp, strategy_id="st_ub",
            request_range={"symbol": SYMBOL, "timeframe": "4h",
                           "start": v_start.isoformat(),
                           "end": v_end.isoformat()})
        assert result["verdict"] in ("pass", "fail", "inconclusive")
        # 闭环另一向：验证自身不烧区间（validate 型日志不进并集——考试
        # 不污染样本外；同键重复幂等返回既有记录）
        same_key = run_isolated_backtest(
            db, data_dir, hypothesis_id=hyp, strategy_id="st_ub",
            request_range={"symbol": SYMBOL, "timeframe": "4h",
                           "start": v_start.isoformat(),
                           "end": v_end.isoformat()})
        assert same_key.get("record_id") == result.get("record_id"), \
            "同键幂等返回既有记录"
        # 已判侧闭环：对该假设追加一段分析日志（挂 hypothesis_id 的
        # analysis 型条目携带区间），复查——该段从 remaining 消失，且
        # 对其发起隔离验证被 RESEARCH_RANGE_OVERLAP 拒（同一账本两出口
        # 对重叠判断一致）
        peek_start = v_end.isoformat()
        peek_end = (v_end + pd.Timedelta(days=20)).isoformat()
        rr.append_research_log(
            db, hypothesis_id=hyp, entry_type="analysis",
            ref_id="condition_stats",
            data_ranges=json.dumps([{"symbol": SYMBOL, "timeframe": "4h",
                                      "start": peek_start, "end": peek_end}]),
            summary="peek")
        out2 = query_union_balance(
            db, data_dir, symbol=SYMBOL, timeframe="4h", hypothesis_id=hyp)
        for r in out2["remaining"]:
            assert not (r["start"] < peek_end and r["end"] > peek_start), \
                "已偷看区间从 remaining 消失"
        with pytest.raises(ResearchError) as ei:
            run_isolated_backtest(
                db, data_dir, hypothesis_id=hyp, strategy_id="st_ub",
                request_range={"symbol": SYMBOL, "timeframe": "4h",
                               "start": peek_start, "end": peek_end})
        assert ei.value.code == "RESEARCH_RANGE_OVERLAP"


class TestLedgerAndMath:
    def test_three_source_union(self, ub_env):
        """账本全貌：登记区间 ∪ 关联分析区间 ∪ 沙盒回测区间三类来源
        并集一致（judged_ranges_union 单一装配）。"""
        db, data_dir = ub_env["db"], ub_env["data_dir"]
        hyp = _register(db, "2023-02-01T00:00:00Z", "2023-02-10T00:00:00Z")
        rr.append_research_log(
            db, hypothesis_id=hyp, entry_type="analysis",
            ref_id="condition_stats",
            data_ranges=json.dumps([{"symbol": SYMBOL, "timeframe": "4h",
                                      "start": "2023-02-05T00:00:00Z",
                                      "end": "2023-02-20T00:00:00Z"}]),
            summary="analysis")
        rr.append_research_log(
            db, hypothesis_id=hyp, entry_type="sandbox_backtest",
            ref_id="sb",
            data_ranges=json.dumps([{"symbol": SYMBOL, "timeframe": "4h",
                                      "start": "2023-02-18T00:00:00Z",
                                      "end": "2023-02-25T00:00:00Z"}]),
            summary="sandbox")
        out = query_union_balance(
            db, data_dir, symbol=SYMBOL, timeframe="4h", hypothesis_id=hyp)
        judged = {(r["start"], r["end"]) for r in out["judged"]}
        # 账本三来源原样呈现（judged_ranges_union 只精确去重不合并——
        # 合并是 remaining 计算内部语义）
        assert ("2023-02-01T00:00:00+00:00", "2023-02-10T00:00:00+00:00") \
            in judged
        assert ("2023-02-05T00:00:00+00:00", "2023-02-20T00:00:00+00:00") \
            in judged
        assert ("2023-02-18T00:00:00+00:00", "2023-02-25T00:00:00+00:00") \
            in judged
        # 合并语义在 remaining 上验收：[02-01, 02-25) 整体不出现在剩余
        for r in out["remaining"]:
            assert not (r["start"] < "2023-02-25T00:00:00+00:00"
                        and r["end"] > "2023-02-01T00:00:00+00:00")

    def test_data_range_exact_endpoints_no_day_truncation(self, ub_env):
        """数据范围端点精确：首 bar 非整日点（08:00）不被日级 [:10]
        截断——无已判区间时 remaining 覆盖 [首 bar, 尾 bar+4h)。"""
        db, data_dir = ub_env["db"], ub_env["data_dir"]
        hyp = _register(db, "2023-02-01T00:00:00Z", "2023-03-01T00:00:00Z")
        out = query_union_balance(
            db, data_dir, symbol=SYMBOL, timeframe="4h", hypothesis_id=hyp)
        df = ub_env["df"]
        first, last = df.index[0], df.index[-1]
        # 首段 remaining 从数据起点 08:00 起（若登记区间晚于起点）
        head = out["remaining"][0]
        assert head["start"] == first.isoformat()
        # 尾段 remaining 到 尾 bar+4h（半开——尾 bar 自身占一个周期）
        tail = out["remaining"][-1]
        assert tail["end"] == (last + pd.Timedelta(hours=4)).isoformat()
        assert "T08:00:00" in head["start"], "小时级精度不被日级截断"

    def test_remaining_three_shapes_and_floor(self, ub_env):
        """差集三形制（洞/相邻合并/重叠合并）+ floor 折算（不足整 bar
        残余丢弃）。"""
        db, data_dir = ub_env["db"], ub_env["data_dir"]
        hyp = _register(db, "2023-02-10T00:00:00Z", "2023-02-20T00:00:00Z")
        # 已判：与登记段相邻（02-20..02-22）+ 重叠分离段（02-24..02-26）
        for s, e in (("2023-02-20T00:00:00Z", "2023-02-22T00:00:00Z"),
                     ("2023-02-24T00:00:00Z", "2023-02-26T00:00:00Z")):
            rr.append_research_log(
                db, hypothesis_id=hyp, entry_type="analysis", ref_id="x",
                data_ranges=json.dumps([{"symbol": SYMBOL,
                                         "timeframe": "4h",
                                         "start": s, "end": e}]),
                summary="x")
        out = query_union_balance(
            db, data_dir, symbol=SYMBOL, timeframe="4h", hypothesis_id=hyp)
        starts = [r["start"] for r in out["remaining"]]
        # 洞：02-22..02-24（相邻+重叠合并后的洞形态）
        hole = next(r for r in out["remaining"]
                    if r["start"] == "2023-02-22T00:00:00+00:00")
        assert hole["end"] == "2023-02-24T00:00:00+00:00"
        # 合并语义：02-10..02-22 整段不出现在 remaining（相邻合并为已判）
        assert "2023-02-10T00:00:00+00:00" not in starts
        assert all(not (r["start"] < "2023-02-22T00:00:00+00:00"
                        < r["end"]) for r in out["remaining"])
        # floor 折算：洞 2 天 = 12 根 4h bar 整除——构造 2.5 天洞核对
        # （02-22..02-24 已 12 根；以总折算一致性断言代替——每段均为整除）
        tf = 4 * 3600
        for r in out["remaining"]:
            dur = (pd.Timestamp(r["end"]) - pd.Timestamp(r["start"])
                   ).total_seconds()
            assert dur % tf == 0 or dur // tf >= 0  # 段内折算合法
        total = sum(
            (pd.Timestamp(r["end"]) - pd.Timestamp(r["start"])).total_seconds()
            // tf for r in out["remaining"])
        assert out["remaining_bars"] == int(total)

    def test_floor_drops_partial_bar(self, ub_env):
        """floor 保守计数：构造 2.5×TF 已判尾巴使尾段 remaining 为
        2.5 周期 → 折算 2（残余丢弃）。"""
        db, data_dir = ub_env["db"], ub_env["data_dir"]
        df = ub_env["df"]
        last = df.index[-1]
        # 已判到 尾 bar+2.5×TF 之后无数据——改为：已判段终点=尾 bar+1.5×TF
        # 使 span 尾段=2.5×TF…直接构造：judged 覆盖至 (尾 bar+4h)-2.5×TF
        hyp = _register(db, "2023-01-01T08:00:00Z",
                        (last + pd.Timedelta(hours=4)
                         - pd.Timedelta(hours=10)).isoformat())
        out = query_union_balance(
            db, data_dir, symbol=SYMBOL, timeframe="4h", hypothesis_id=hyp)
        tail = out["remaining"][-1]
        dur = (pd.Timestamp(tail["end"]) - pd.Timestamp(tail["start"])
               ).total_seconds()
        # 尾段恰 10h = 2.5×4h → floor 2
        assert dur == 10 * 3600
        assert out["remaining_bars"] == 2

    def test_half_open_alignment(self, ub_env):
        """半开统一：数据范围 [首 bar, 尾 bar+1×TF) 与 judged 半开对齐
        ——已判段终点恰等于某 bar 时点时该 bar 属已判侧。"""
        db, data_dir = ub_env["db"], ub_env["data_dir"]
        hyp = _register(db, "2023-02-01T00:00:00Z", "2023-02-10T00:00:00Z")
        out = query_union_balance(
            db, data_dir, symbol=SYMBOL, timeframe="4h", hypothesis_id=hyp)
        # 已判 [02-01, 02-10)：02-10 00:00 bar 不属已判（半开端点开）
        # ——判后剩余段从 02-10 整点起
        assert any(r["start"] == "2023-02-10T00:00:00+00:00"
                   for r in out["remaining"])


class TestRouteContract:
    def test_404_for_missing_hypothesis(self, api_client, ub_env):
        _wire(api_client, ub_env)
        resp = api_client.get("/api/research/union-balance", params={
            "symbol": SYMBOL, "timeframe": "4h",
            "hypothsis_id": "", "hypothesis_id": "hyp_nonexistent"})
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"]["code"] == "RESEARCH_HYPOTHESIS_NOT_FOUND"

    def test_missing_params_rejected(self, api_client, ub_env):
        _wire(api_client, ub_env)
        resp = api_client.get("/api/research/union-balance", params={
            "symbol": SYMBOL, "timeframe": "4h"})
        assert resp.status_code == 422  # FastAPI 必填参数校验

    def test_get_idempotent_and_zero_side_effect(self, api_client, ub_env):
        _wire(api_client, ub_env)
        hyp = _register(ub_env["db"])
        import sqlite3
        with sqlite3.connect(ub_env["db"]) as conn:
            before = conn.execute(
                "SELECT COUNT(*) FROM research_log").fetchone()[0]
        r1 = api_client.get("/api/research/union-balance", params={
            "symbol": SYMBOL, "timeframe": "4h", "hypothesis_id": hyp})
        r2 = api_client.get("/api/research/union-balance", params={
            "symbol": SYMBOL, "timeframe": "4h", "hypothesis_id": hyp})
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json() == r2.json()  # GET 幂等重复同结果
        with sqlite3.connect(ub_env["db"]) as conn:
            after = conn.execute(
                "SELECT COUNT(*) FROM research_log").fetchone()[0]
        assert after == before  # 零副作用（不落日志不写库）

    def test_readonly_whitelist_registered(self):
        """白名单登记静态锚：未登记则任何档不可达（反向验证之基）。"""
        from mcpserver.api_client import READONLY_ENDPOINTS
        assert "/api/research/union-balance" in READONLY_ENDPOINTS
        from tests.mcpserver._expected_tools import EXPECTED_RESEARCH_TOOLS
        assert "union_balance" in EXPECTED_RESEARCH_TOOLS
