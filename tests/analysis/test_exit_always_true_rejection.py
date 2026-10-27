"""恒真出场硬拒绝测试（fix-executor-column-and-exit-guard D3 / C 组锚）。

Q1 终裁 A 分层的高档分支：then_conditions 在登记区间内全 bar 触发
（恒真出场）→ 422 RESEARCH_EXIT_ALWAYS_TRUE 硬拒绝——恒真出场下同
bar 开平互斥清洗必然抹杀全部进场信号（BUG-2 实录：exits=499/749、
全程零进场无提示），恒真 then 无合法策略用例。判定只在 then_coverage
数据成功求得时生效（fail-open 于数据缺失、fail-close 于恒真实证）。

≥90% 非恒真落 D4 警告档（E 组锚在 test_hypothesis_service.py）。
【Q1 若终裁改 B 档：本文件整体摘除，撤销记录入任务 5.4。】
"""
from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.column_ref import BASE_SUBJECT_COLUMNS  # noqa: F401
from core.analysis.errors import ResearchError
from core.analysis.executor import ensure_research_tables
from core.analysis.hypothesis_service import register_hypothesis
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import research as rr
from tests.helpers.data_factory import make_ohlcv

SYMBOL = "BTCUSDT"


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    ensure_research_tables(d)
    return d


def _data_env(tmp_path, closes):
    """确定性数据目录（1h）+ 尾段请求区间。

    closes 长度 401；区间=idx[300, 400)（半开恰含 100 bar）。
    """
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="1h",
                        tz="UTC")
    close = pd.Series(closes, index=idx, dtype=float)
    df = pd.DataFrame({
        "open": close, "high": close + 1.0, "low": close - 1.0,
        "close": close, "volume": 100.0,
    })
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    df.to_parquet(data_dir / f"{SYMBOL}_1h.parquet")
    rng = {"symbol": SYMBOL, "timeframe": "1h",
           "start": df.index[300].isoformat(),
           "end": df.index[400].isoformat()}
    return data_dir, rng


def _conds(then_target=60):
    """when=RSI(14) lt 35（合法低触发）；then=close gt target。"""
    return {
        "when_conditions": [
            {"subject": {"indicator": "RSI", "params": {"period": 14}},
             "action": "lt", "target": 35}],
        "then_conditions": [
            {"subject": "close", "action": "gt", "target": then_target}],
    }


def _register(db, tmp_path, closes, then_target=60, ranges=None):
    data_dir, rng = _data_env(tmp_path, closes)
    return register_hypothesis(
        db, conditions=_conds(then_target), statement="s",
        data_ranges=ranges or [rng], precheck_data_dir=data_dir, strategy_params={"stop_loss": 0.05})


class TestExitAlwaysTrueRejection:
    """C1-C4：恒真 then 硬拒绝（独立 try 域，raise 直达调用方）。"""

    def test_c1_always_true_rejected_422_full_detail(self, db, tmp_path):
        """C1：恒真形（区间全 100、then close gt 60）→ 422 + detail
        全键 + 库无写入 + 重登同码。"""
        closes = [100.0] * 401
        data_dir, rng = _data_env(tmp_path, closes)
        with pytest.raises(ResearchError) as ei:
            register_hypothesis(
                db, conditions=_conds(), statement="s",
                data_ranges=[rng], precheck_data_dir=data_dir, strategy_params={"stop_loss": 0.05})
        err = ei.value
        assert err.code == "RESEARCH_EXIT_ALWAYS_TRUE"
        assert err.status == 422
        d = err.detail
        assert d["field"] == "then_conditions"
        assert d["coverage"] == 1.0
        assert d["hit_bars"] == d["stat_bars"] == 100
        assert d["range"] == {"start": rng["start"], "end": rng["end"]}
        assert "slow_path" in d and d["slow_path"]
        # 拒绝即登记失败：库无新写入
        assert rr.count_hypotheses(db) == 0
        # 重登同形同码（非幂等命中——首次未落库）
        with pytest.raises(ResearchError) as ei2:
            register_hypothesis(
                db, conditions=_conds(), statement="s2",
                data_ranges=[rng], precheck_data_dir=data_dir, strategy_params={"stop_loss": 0.05})
        assert ei2.value.code == "RESEARCH_EXIT_ALWAYS_TRUE"
        assert rr.count_hypotheses(db) == 0

    def test_c2_95_percent_not_rejected(self, db, tmp_path):
        """C2：95% 形不触发 D3（落 D4 警告档）——登记成功。"""
        closes = [100.0] * 300 + [100.0] * 95 + [50.0] * 5 + [100.0]
        out = _register(db, tmp_path, closes)
        assert out["duplicated"] is False
        assert rr.count_hypotheses(db) == 1
        # D4 警告档承接（95% ≥ 0.9）
        assert "exit_coverage_warnings" in out

    def test_c3_precheck_degraded_always_true_form_registers(
            self, db, tmp_path, monkeypatch):
        """C3：4.6 求值降级（run_registration_precheck 抛）+恒真条件形
        → 登记成功（fail-open 于数据缺失——无数据不拒绝）。"""
        import importlib
        closes = [100.0] * 401
        data_dir, rng = _data_env(tmp_path, closes)
        pk = importlib.import_module(
            "core.analysis.templates.precheck_hypothesis")

        def boom(*a, **kw):
            raise RuntimeError("装载失败形")

        monkeypatch.setattr(pk, "run_registration_precheck", boom)
        out = register_hypothesis(
            db, conditions=_conds(), statement="s",
            data_ranges=[rng], precheck_data_dir=data_dir, strategy_params={"stop_loss": 0.05})
        assert out["duplicated"] is False
        assert "precheck_warnings" not in out
        assert "exit_coverage_warnings" not in out

    def test_c4_multi_range_any_always_true_rejects(self, db, tmp_path):
        """C4：多区间任一恒真即拒（区间 A 恒真、区间 B 50%）。"""
        # 600 bar：区间 A=idx[300,400) 全 100（恒真）；区间 B=idx[400,500)
        # 50 热 50 冷（50%）
        closes = ([100.0] * 400
                  + [100.0, 50.0] * 50
                  + [100.0] * 100)
        idx = pd.date_range("2024-01-01", periods=len(closes), freq="1h",
                            tz="UTC")
        close = pd.Series(closes, index=idx, dtype=float)
        df = pd.DataFrame({
            "open": close, "high": close + 1.0, "low": close - 1.0,
            "close": close, "volume": 100.0,
        })
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        df.to_parquet(data_dir / f"{SYMBOL}_1h.parquet")
        rng_a = {"symbol": SYMBOL, "timeframe": "1h",
                 "start": df.index[300].isoformat(),
                 "end": df.index[400].isoformat()}
        rng_b = {"symbol": SYMBOL, "timeframe": "1h",
                 "start": df.index[400].isoformat(),
                 "end": df.index[500].isoformat()}
        with pytest.raises(ResearchError) as ei:
            register_hypothesis(
                db, conditions=_conds(), statement="s",
                data_ranges=[rng_a, rng_b],
                precheck_data_dir=data_dir, strategy_params={"stop_loss": 0.05})
        err = ei.value
        assert err.code == "RESEARCH_EXIT_ALWAYS_TRUE"
        # detail 锚定首个恒真区间（A）
        assert err.detail["range"]["start"] == rng_a["start"]
        assert err.detail["hit_bars"] == 100
        assert rr.count_hypotheses(db) == 0
