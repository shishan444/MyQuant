"""回放回测端点测试（add-replay-backtest-mcp 任务 1.1）。

POST /api/research/backtest-replay：薄封装 ReplayRunner（进程内
直调），装载失败经 ResearchError 单源转结构化 4xx。
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.api]

from core.data.storage import save_parquet
from core.persistence.db_ext import init_db_ext
from core.persistence.repository.strategy import save_strategy
from tests.helpers.data_factory import make_dna, make_ohlcv


@pytest.fixture()
def replay_env(tmp_path: Path):
    """合成数据+临时库+策略行+TestClient（create_app 参数化装配——
    沿 test_sandbox_validation_records_api 形制）。"""
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    df = make_ohlcv(n=600, freq="4h", seed=7, base_price=100.0)
    save_parquet(df, data_dir / "BTCUSDT_4h.parquet")
    db = tmp_path / "paper.db"
    init_db_ext(db)
    dna = make_dna()
    save_strategy(db, strategy_id="st_replay_api",
                  dna_json=dna.to_json(), symbol="BTCUSDT", timeframe="4h")
    from api.app import create_app
    from fastapi.testclient import TestClient

    app = create_app(db_path=db, data_dir=data_dir)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield {"client": c, "data_dir": data_dir, "db": db, "df": df,
               "strategy_id": "st_replay_api"}


class TestBacktestReplayEndpoint:
    """端点冒烟+错误通道。"""

    def test_replay_returns_summary(self, replay_env):
        idx = replay_env["df"].index
        resp = replay_env["client"].post("/api/research/backtest-replay", json={
            "strategy_id": replay_env["strategy_id"],
            "start": idx[-300].isoformat(), "end": idx[-1].isoformat(),
            "task_id": "api-replay-01"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["task_id"] == "api-replay-01"
        assert body["status"] in ("completed", "stopped")
        assert body["bars_processed"] > 0
        assert body["symbol"] == "BTCUSDT"
        assert "total_pnl" in body and "final_balance" in body
        # level=replay 记录写入（任务 3.1）：record_id 回显+表内可查
        assert body["record_id"].startswith("rec_")
        from core.persistence.repository import research as rr

        rec = rr.get_validation_record_by_id(
            replay_env["db"], body["record_id"])
        assert rec["level"] == "replay"
        assert rec["verdict"] == ""  # 不参与三态
        import json as _json

        result_json = _json.loads(rec["result"])
        assert result_json["replay"]["task_id"] == "api-replay-01"

    def test_repeat_runs_independent_records(self, replay_env):
        """同策略同区间同参数连续两次：两行独立 record（时间流即
        晋级史，互不覆盖互不幂等合并——spec 场景「重复执行独立成行」
        的 replay 入口承载）。"""
        idx = replay_env["df"].index
        payload = {
            "strategy_id": replay_env["strategy_id"],
            "start": idx[-100].isoformat(), "end": idx[-1].isoformat()}
        ids = []
        for _ in range(2):
            resp = replay_env["client"].post(
                "/api/research/backtest-replay", json=payload)
            assert resp.status_code == 200, resp.text
            ids.append(resp.json()["record_id"])
        assert ids[0] != ids[1], "重复执行独立成行（record_id 恒新）"
        from core.persistence.repository import research as rr

        for rid in ids:
            rec = rr.get_validation_record_by_id(replay_env["db"], rid)
            assert rec["level"] == "replay"

    def test_batch_strategy_ids_results(self, replay_env):
        """并发组端到端（P2-CARD-01）：strategy_ids 3 路→results
        列表 3 项、序保持、每路 record_id/task_id 独立、task 行与
        validation_records 每路一行互不串。"""
        from tests.helpers.data_factory import make_dna as _make

        sids = [replay_env["strategy_id"]]
        for i in (2, 3):
            save_strategy(replay_env["db"], strategy_id=f"st_batch_{i}",
                          dna_json=_make().to_json(), symbol="BTCUSDT",
                          timeframe="4h", status="draft")
            sids.append(f"st_batch_{i}")
        idx = replay_env["df"].index
        resp = replay_env["client"].post("/api/research/backtest-replay", json={
            "strategy_ids": sids,
            "start": idx[-100].isoformat(), "end": idx[-1].isoformat(),
            "max_workers": 3})
        assert resp.status_code == 200, resp.text
        results = resp.json()["results"]
        assert [r["strategy_id"] for r in results] == sids, "结果序保持"
        assert len({r["task_id"] for r in results}) == 3
        record_ids = [r["record_id"] for r in results]
        assert len(set(record_ids)) == 3, "每路独立 record"
        from core.persistence.repository import paper as pp
        from core.persistence.repository import research as rr

        for r in results:
            task = pp.get_task(replay_env["db"], r["task_id"])
            assert task["strategy_id"] == r["strategy_id"], "落库互不串"
            rec = rr.get_validation_record_by_id(
                replay_env["db"], r["record_id"])
            assert rec["level"] == "replay", "每路一行记录"

    def test_single_element_strategy_ids_flat_response(self, replay_env):
        """单值并发组退化直调：平铺响应（无 results 包裹——与
        strategy_id 缺省路径同构）。"""
        idx = replay_env["df"].index
        resp = replay_env["client"].post("/api/research/backtest-replay", json={
            "strategy_ids": [replay_env["strategy_id"]],
            "start": idx[-100].isoformat(), "end": idx[-1].isoformat()})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "results" not in body, "单值不进池（平铺形制）"
        assert body["strategy_id"] == replay_env["strategy_id"]
        assert body["bars_processed"] > 0

    def test_batch_with_task_id_rejected(self, replay_env):
        """矛盾拒绝：并发组+task_id 同传→422 RESEARCH_PARAM_INVALID
        （防静默丢参——每路独立 task_id 语义不可共享固定值）。"""
        idx = replay_env["df"].index
        resp = replay_env["client"].post("/api/research/backtest-replay", json={
            "strategy_ids": [replay_env["strategy_id"], "st_x"],
            "start": idx[-100].isoformat(), "end": idx[-1].isoformat(),
            "task_id": "fixed-01"})
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["code"] == "RESEARCH_PARAM_INVALID"

    @pytest.mark.parametrize("leverage", [5, 0, -1], ids=["超上限", "零", "负"])
    def test_leverage_out_of_range_rejected(self, replay_env, leverage):
        """任务级杠杆越域（spec「参数域封顶拒绝」）：[1,3] 外三态
        422 附 legal_range（单源援引 sandbox 的 require_task_cap_params
        ——P2-CARD-02 补面）。"""
        idx = replay_env["df"].index
        resp = replay_env["client"].post("/api/research/backtest-replay", json={
            "strategy_id": replay_env["strategy_id"],
            "start": idx[-50].isoformat(), "end": idx[-1].isoformat(),
            "leverage": leverage})
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_PARAM_INVALID"
        assert detail["detail"]["legal_range"] == "[1,3]"

    def test_leverage_boundary_three_passes(self, replay_env):
        """合法域边界值 3（上界含）直通（下界 1=缺省由既有用例
        全量承载）。"""
        idx = replay_env["df"].index
        resp = replay_env["client"].post("/api/research/backtest-replay", json={
            "strategy_id": replay_env["strategy_id"],
            "start": idx[-50].isoformat(), "end": idx[-1].isoformat(),
            "leverage": 3})
        assert resp.status_code == 200, resp.text

    def test_halt_info_absent_when_unconfigured(self, replay_env):
        """halted 三分态1：未配置 halt_drawdown_pct→响应无 halt_info
        键（不写键非 false——与 sandbox halt_info 形制一致）。"""
        idx = replay_env["df"].index
        resp = replay_env["client"].post("/api/research/backtest-replay", json={
            "strategy_id": replay_env["strategy_id"],
            "start": idx[-50].isoformat(), "end": idx[-1].isoformat()})
        assert resp.status_code == 200, resp.text
        assert "halt_info" not in resp.json()

    def test_halt_info_false_when_configured_not_triggered(self, replay_env):
        """halted 三分态2：配置（0.5 宽阈）未触发→halt_info={"halted":
        false}（键在、值为 false）。"""
        idx = replay_env["df"].index
        resp = replay_env["client"].post("/api/research/backtest-replay", json={
            "strategy_id": replay_env["strategy_id"],
            "start": idx[-50].isoformat(), "end": idx[-1].isoformat(),
            "halt_drawdown_pct": 0.5})
        assert resp.status_code == 200, resp.text
        assert resp.json()["halt_info"] == {"halted": False}

    def test_halt_info_true_when_triggered(self, replay_env):
        """halted 三分态3：微阈值（0.0001——开仓费用即破峰值回撤线）
        触发→halt_info={"halted": true, halted_at, halt_reason}。语义
        注意：halt 只停新开仓不终止回放——status 仍 completed（触发
        后区间跑完，非 stopped）。"""
        idx = replay_env["df"].index
        resp = replay_env["client"].post("/api/research/backtest-replay", json={
            "strategy_id": replay_env["strategy_id"],
            "start": idx[-50].isoformat(), "end": idx[-1].isoformat(),
            "halt_drawdown_pct": 0.0001})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        info = body["halt_info"]
        assert info["halted"] is True
        assert info["halted_at"], "触发时刻在案"
        assert info["halt_reason"] == \
            "equity_drawdown_from_peak_ge_halt_drawdown_pct"
        assert body["status"] == "completed", "halt 不终止回放"
        assert body["halted_at"] == info["halted_at"], "平铺兼容键同源"

    def test_strategy_not_found_structured_4xx(self, replay_env):
        idx = replay_env["df"].index
        resp = replay_env["client"].post("/api/research/backtest-replay", json={
            "strategy_id": "st_missing",
            "start": idx[-100].isoformat(), "end": idx[-1].isoformat()})
        assert resp.status_code == 404, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "STRATEGY_NOT_FOUND"

    def test_series_mode_rejected(self, replay_env):
        import dataclasses

        dna = make_dna()
        series_dna = dataclasses.replace(
            dna, risk_genes=dataclasses.replace(
                dna.risk_genes, sl_mode="series",
                sl_series_source={"indicator": "EMA",
                                  "params": {"period": 20}}))
        save_strategy(replay_env["db"], strategy_id="st_series_api",
                      dna_json=series_dna.to_json(), symbol="BTCUSDT",
                      timeframe="4h", status="draft")
        idx = replay_env["df"].index
        resp = replay_env["client"].post("/api/research/backtest-replay", json={
            "strategy_id": "st_series_api",
            "start": idx[-100].isoformat(), "end": idx[-1].isoformat()})
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["code"] == "TRADING_INCOMPATIBLE"
