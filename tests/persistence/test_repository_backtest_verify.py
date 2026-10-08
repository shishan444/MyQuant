"""repository/backtest_result.py + verify_session.py 特征测试网。

现状记录语义（B3b.1 迁移轨当前行为；strategies 路由现役通道仍是
db_ext 同名函数，repository 轨为并行等价实现）：
- backtest_result：save 裸 INSERT（win_rate None 归一 0.0——DB 列
  NOT NULL，读侧以 total_trades==0 识别缺失）；list 过滤
  （strategy_id/symbol/run_source/session_id）+ getattr 限定排序 +
  limit（无 offset）；
- verify_session：save 默认 status=running；update 仅写显式非 None
  字段、全 None 为 no-op；list 按 created_at 倒序 + limit。
"""

import pytest

pytestmark = [pytest.mark.unit]

from core.persistence.db_ext import init_db_ext
from core.persistence.repository import backtest_result as br
from core.persistence.repository import verify_session as vs


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "bv.db"
    init_db_ext(path)
    return path


def _result(db, rid, **kw):
    kw.setdefault("strategy_id", "s1")
    kw.setdefault("symbol", "BTCUSDT")
    kw.setdefault("timeframe", "4h")
    kw.setdefault("data_start", "2025-01-01")
    kw.setdefault("data_end", "2025-06-01")
    br.save_backtest_result(db, result_id=rid, **kw)


class TestBacktestResultSaveGet:
    """save 往返与缺失值归一。"""

    def test_往返与win_rate缺失归一(self, db):
        _result(db, "r1", win_rate=None, total_trades=0, total_score=55.5,
                run_source="verify", session_id="sess-1")
        row = br.get_backtest_result(db, "r1")
        assert row is not None
        assert row["win_rate"] == 0.0, "None 入库归一 0.0（读侧以 total_trades==0 识别）"
        assert row["total_trades"] == 0
        assert row["run_source"] == "verify"
        assert row["session_id"] == "sess-1"

    def test_显式win_rate原样保留(self, db):
        _result(db, "r2", win_rate=0.625, total_trades=8)
        assert br.get_backtest_result(db, "r2")["win_rate"] == 0.625

    def test_缺失返回None(self, db):
        assert br.get_backtest_result(db, "ghost") is None


class TestBacktestResultList:
    """list：四维过滤 + 排序 + limit。"""

    @pytest.fixture(autouse=True)
    def _seed(self, db):
        # created_at 为 save 时 _now() 实时值，seed 顺序 r1..r4 递增
        _result(db, "r1", strategy_id="s1", symbol="BTCUSDT", run_source="lab")
        _result(db, "r2", strategy_id="s2", symbol="ETHUSDT", run_source="verify",
                session_id="sess-1")
        _result(db, "r3", strategy_id="s1", symbol="ETHUSDT", run_source="lab")
        _result(db, "r4", strategy_id="s2", symbol="BTCUSDT", run_source="verify",
                session_id="sess-1")

    def test_默认created_at倒序(self, db):
        assert [r["result_id"] for r in br.list_backtest_results(db)] == \
            ["r4", "r3", "r2", "r1"]

    def test_四维过滤各自生效(self, db):
        assert [r["result_id"] for r in
                br.list_backtest_results(db, strategy_id="s1")] == ["r3", "r1"]
        assert {r["result_id"] for r in
                br.list_backtest_results(db, symbol="ETHUSDT")} == {"r2", "r3"}
        assert [r["result_id"] for r in
                br.list_backtest_results(db, run_source="verify")] == ["r4", "r2"]
        assert [r["result_id"] for r in
                br.list_backtest_results(db, session_id="sess-1")] == ["r4", "r2"]

    def test_过滤组合与limit(self, db):
        rows = br.list_backtest_results(db, symbol="BTCUSDT", strategy_id="s2",
                                        limit=1)
        assert [r["result_id"] for r in rows] == ["r4"]

    def test_排序列与双向(self, db):
        _result(db, "r5", total_score=10.0)
        _result(db, "r6", total_score=90.0)
        asc = br.list_backtest_results(db, sort_by="total_score",
                                       sort_order="asc", limit=2)
        assert [r["total_score"] for r in asc] == [0.0, 0.0], \
            "未传 score 的默认 0.0 排最前"

    def test_非法排序列_AttributeError(self, db):
        """getattr 限定列名（旧轨动态拼列的注入面消除）：非法名以
        AttributeError 结构化拒绝，不再进入 SQL。"""
        with pytest.raises(AttributeError):
            br.list_backtest_results(db, sort_by="1=1; DROP TABLE x")


class TestVerifySession:
    """verify_session：默认值/逐字段更新/倒序列表。"""

    def test_默认running与往返(self, db):
        vs.save_verify_session(db, session_id="v1", strategy_ids='["s1"]',
                               data_ranges='["2025-01-01:2025-06-01"]')
        row = vs.get_verify_session(db, "v1")
        assert row["status"] == "running"
        assert row["init_cash"] == 100000.0
        assert vs.get_verify_session(db, "ghost") is None

    def test_更新仅写显式字段(self, db):
        vs.save_verify_session(db, session_id="v1", strategy_ids="[]",
                               data_ranges="[]")
        vs.update_verify_session(db, "v1", status="completed",
                                 total_results=3)
        row = vs.get_verify_session(db, "v1")
        assert row["status"] == "completed"
        assert row["total_results"] == 3
        # 计数列建表带 server_default=0（未写落库 0）；
        # summary_json 无默认，保持 NULL
        assert row["total_strategies"] == 0
        assert row["summary_json"] is None

    def test_全None_noop(self, db):
        vs.save_verify_session(db, session_id="v1", strategy_ids="[]",
                               data_ranges="[]", status="running")
        vs.update_verify_session(db, "v1")
        assert vs.get_verify_session(db, "v1")["status"] == "running"

    def test_失败路径回写error与completed_at(self, db):
        vs.save_verify_session(db, session_id="v1", strategy_ids="[]",
                               data_ranges="[]")
        vs.update_verify_session(db, "v1", status="failed",
                                 error_message="boom",
                                 completed_at="2026-01-01T00:00:00+00:00")
        row = vs.get_verify_session(db, "v1")
        assert row["error_message"] == "boom"
        assert row["completed_at"] == "2026-01-01T00:00:00+00:00"

    def test_列表倒序limit(self, db):
        for i in range(3):
            vs.save_verify_session(db, session_id=f"v{i}", strategy_ids="[]",
                                   data_ranges="[]")
        rows = vs.list_verify_sessions(db, limit=2)
        assert [r["session_id"] for r in rows] == ["v2", "v1"], \
            "created_at 倒序——最新在前"
