"""0.3.3 G12 衍生品边界声明（df.attrs 通道）特征网。

覆盖三态中的 gap / probe_failed（absent 由既有 skipped_indicators
通道承载）+ attrs 存活链（compute_all_indicators 后仍非空——实施
必验项）+ 装配 helper 挂键条件 + 对象级隔离（并发互染回归锚——
模块级全局会在 ThreadPoolExecutor 下互染，df.attrs 不会）。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.data.mtf_loader import _try_merge_derivatives
from tests.helpers.data_factory import make_ohlcv

SYMBOL = "BTCUSDT"
TF = "1h"


def _write(df: pd.DataFrame, path: Path) -> None:
    df.to_parquet(path, engine="pyarrow")


def _make_env(tmp_path: Path, funding_bars: int | None, oi_bars: int | None):
    """主 df 900 根；衍生品按 funding_bars/oi_bars 根写文件（None=不写）。"""
    base = make_ohlcv(n=900, freq="1h", start="2024-01-01", seed=3)
    if funding_bars is not None:
        _write(base.iloc[:funding_bars], tmp_path / f"{SYMBOL}_funding.parquet")
    if oi_bars is not None:
        _write(base.iloc[:oi_bars], tmp_path / f"{SYMBOL}_oi_{TF}.parquet")
    return base


class TestTryMergeCoverage:
    def test_gap_declared_when_coverage_shorter_than_request(self, tmp_path):
        """缺口显式声明：funding 只覆盖前 300 根、请求 900 根 →
        funding.gap=True 且四界值准确（数据不存在≠市场没信号）。"""
        base = _make_env(tmp_path, funding_bars=300, oi_bars=None)
        merged = _try_merge_derivatives(tmp_path, SYMBOL, TF, base)
        cov = merged.attrs["derivatives_coverage"]
        assert set(cov) == {"funding"}
        entry = cov["funding"]
        assert entry["gap"] is True
        assert entry["available_end"] == base.index[299].isoformat()
        assert entry["requested_end"] == base.index[899].isoformat()

    def test_no_gap_when_fully_covered(self, tmp_path):
        """全覆盖临界：available == requested 两侧 → gap=False。"""
        base = _make_env(tmp_path, funding_bars=900, oi_bars=900)
        merged = _try_merge_derivatives(tmp_path, SYMBOL, TF, base)
        cov = merged.attrs["derivatives_coverage"]
        assert cov["funding"]["gap"] is False
        assert cov["oi"]["gap"] is False

    def test_probe_failure_degrades_without_blocking(self, tmp_path,
                                                     monkeypatch):
        """探测异常降级 probe_failed 且主装载照常完成（架构 A1——
        披露是伴随物不是闸门）。"""
        base = _make_env(tmp_path, funding_bars=300, oi_bars=None)

        class BoomIndex(pd.DatetimeIndex):
            def min(self):
                raise RuntimeError("probe boom")

        bad = base.set_axis(BoomIndex(base.index))
        merged = _try_merge_derivatives(tmp_path, SYMBOL, TF, bad)
        assert merged.attrs["derivatives_coverage"] == {"status": "probe_failed"}
        assert len(merged) == 900  # 主数据完好

    def test_no_derivatives_no_attrs_key(self, tmp_path):
        """文件均不存在（absent 形）→ 不写 attrs、不挂键——absent 由
        既有 skipped_indicators 通道承载（spec 场景集对齐）。"""
        base = _make_env(tmp_path, funding_bars=None, oi_bars=None)
        merged = _try_merge_derivatives(tmp_path, SYMBOL, TF, base)
        assert "derivatives_coverage" not in merged.attrs


class TestAttrsSurvival:
    def test_coverage_survives_compute_all_indicators(self, tmp_path):
        """存活锚（实施必验项）：merged 流经 compute_all_indicators
        （append as columns）后 attrs 仍非空——装配点才能读到。"""
        from core.features.indicators import compute_all_indicators

        base = _make_env(tmp_path, funding_bars=300, oi_bars=None)
        merged = _try_merge_derivatives(tmp_path, SYMBOL, TF, base)
        enhanced = compute_all_indicators(merged)
        assert "derivatives_coverage" in enhanced.attrs
        assert enhanced.attrs["derivatives_coverage"]["funding"]["gap"] is True


class TestResultAttachment:
    def test_helper_attaches_only_when_attrs_present(self, tmp_path):
        """装配 helper：有 attrs 挂键（拷贝不改原 dict）、无 attrs 不挂。"""
        from api.routes.research.analysis import _with_derivatives_coverage

        base = _make_env(tmp_path, funding_bars=300, oi_bars=None)
        merged = _try_merge_derivatives(tmp_path, SYMBOL, TF, base)
        result = {"payload": 1}
        out = _with_derivatives_coverage(result, merged)
        assert "derivatives_coverage" in out and out["payload"] == 1
        assert "derivatives_coverage" not in result  # 原 dict 不被改

        out2 = _with_derivatives_coverage({"payload": 1}, base)
        assert "derivatives_coverage" not in out2  # 无 attrs 不挂


class TestConcurrentIsolation:
    def test_two_threads_keep_own_coverage(self, tmp_path):
        """并发互染回归锚：线程池语义下两个请求各取各的 coverage
        （对象级传播——模块级全局在此场景会互相覆盖）。"""
        import concurrent.futures

        d1, d2 = tmp_path / "a", tmp_path / "b"
        d1.mkdir(); d2.mkdir()
        _make_env(d1, funding_bars=300, oi_bars=None)
        _make_env(d2, funding_bars=900, oi_bars=None)
        base = make_ohlcv(n=900, freq="1h", start="2024-01-01", seed=3)

        def load(d):
            return _try_merge_derivatives(d, SYMBOL, TF, base.copy())

        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            m1, m2 = pool.submit(load, d1).result(), pool.submit(load, d2).result()
        assert m1.attrs["derivatives_coverage"]["funding"]["gap"] is True
        assert m2.attrs["derivatives_coverage"]["funding"]["gap"] is False


class TestBoundaryDynamic:
    def test_boundary_reflects_rebackfilled_data(self, tmp_path):
        """边界动态锚：衍生品回补后（funding 覆盖延长）再装载，披露
        边界反映回补后的实际覆盖——无硬编码日期残留（spec 场景
        「边界随数据文件动态」的回归锚）。"""
        base = _make_env(tmp_path, funding_bars=300, oi_bars=None)
        first = _try_merge_derivatives(tmp_path, SYMBOL, TF, base)
        assert first.attrs["derivatives_coverage"]["funding"][
            "available_end"] == base.index[299].isoformat()

        # 回补：funding 文件延长至 600 根后重载
        full = make_ohlcv(n=900, freq="1h", start="2024-01-01", seed=3)
        _write(full.iloc[:600], tmp_path / f"{SYMBOL}_funding.parquet")
        second = _try_merge_derivatives(tmp_path, SYMBOL, TF, base)
        cov = second.attrs["derivatives_coverage"]["funding"]
        assert cov["available_end"] == base.index[599].isoformat()
        assert cov["gap"] is True  # 仍短于请求区间，但边界已随文件变化


class TestRouteWiring:
    """特征网：装配接线——_with_derivatives_coverage 挂键经 run_analysis
    路由真实到达模板结果（helper 本身有锚，此处锚「真实模板分支的
    结果携带披露」——spec 场景「结果声明各衍生品列的实际边界」的
    出站面）。distribution_compare 走 _load_df 直装载链
    （load_and_prepare_df → _try_merge_derivatives → attrs → 装配挂键）。
    """

    @staticmethod
    def _route_env(tmp_path: Path, funding_bars: int | None) -> pd.DataFrame:
        """路由级环境：主文件落盘（完整装载链需要）+ 可选 funding。"""
        base = make_ohlcv(n=900, freq="1h", start="2024-01-01", seed=3)
        _write(base, tmp_path / f"{SYMBOL}_{TF}.parquet")
        if funding_bars is not None:
            _write(base.iloc[:funding_bars],
                   tmp_path / f"{SYMBOL}_funding.parquet")
        return base

    @staticmethod
    def _payload(base: pd.DataFrame) -> "AnalysisRunRequest":
        from api.schemas import AnalysisRunRequest, DataRangeItem
        return AnalysisRunRequest(
            template="distribution_compare",
            params={"column": "close",
                    "group_a": {"start": base.index[0].isoformat(),
                                "end": base.index[449].isoformat()},
                    "group_b": {"start": base.index[450].isoformat(),
                                "end": base.index[899].isoformat()}},
            data_range=DataRangeItem(
                symbol=SYMBOL, timeframe=TF,
                start=base.index[0].isoformat(),
                end=base.index[-1].isoformat()))

    def test_run_analysis_result_carries_coverage(self, tmp_path):
        from api.routes.research.analysis import run_analysis
        from core.persistence.db_ext import init_db_ext

        base = self._route_env(tmp_path, funding_bars=300)
        db = tmp_path / "q.db"
        init_db_ext(db)
        result = run_analysis(self._payload(base), db_path=db,
                              data_dir=tmp_path)
        # 模板自身结果在场（装配不吞模板 payload）
        assert "payload" in result
        cov = result["derivatives_coverage"]
        assert cov["funding"]["gap"] is True
        assert cov["funding"]["available_end"] == base.index[299].isoformat()
        assert cov["funding"]["requested_end"] == base.index[899].isoformat()

    def test_run_analysis_no_derivatives_no_key(self, tmp_path):
        """无衍生品文件时结果不挂 coverage 键（absent 形不产生空披露
        ——通道语义与 helper 锚一致，此处锚路由级出站）。"""
        from api.routes.research.analysis import run_analysis
        from core.persistence.db_ext import init_db_ext

        base = self._route_env(tmp_path, funding_bars=None)
        db = tmp_path / "q.db"
        init_db_ext(db)
        result = run_analysis(self._payload(base), db_path=db,
                              data_dir=tmp_path)
        assert "derivatives_coverage" not in result


class TestWarmupPrefixGapSemantics:
    """CARD-02 锚：warmup 前扩装载下 gap 判定基准 = 用户请求区间。

    主数据 2023-12-01 起 1500 根 1h；funding 仅覆盖 [2024-01-01,
    2024-01-20]。走 load_with_warmup（RSI 前扩 200 bar 至 ~2023-12-27，
    切片边界早于 funding 起点）——修复前 requested 取前扩切片边界，
    前扩窗内衍生品起点被误报为请求段缺口；修复后 requested=用户区间。
    """

    @staticmethod
    def _env(tmp_path: Path) -> pd.DataFrame:
        base = make_ohlcv(n=1500, freq="1h", start="2023-12-01", seed=3)
        _write(base, tmp_path / f"{SYMBOL}_{TF}.parquet")
        funding = base.loc["2024-01-01":"2024-01-20"]
        _write(funding, tmp_path / f"{SYMBOL}_funding.parquet")
        return base

    def test_prefix_window_no_false_gap(self, tmp_path):
        """判别场景：请求段全落 funding 覆盖内 → gap=False（修复前
        误报 True），requested_* = 用户区间而非前扩边界。"""
        from core.analysis.warmup_loader import load_with_warmup

        base = self._env(tmp_path)
        df, _ = load_with_warmup(
            tmp_path, SYMBOL, TF,
            "2024-01-05T00:00:00", "2024-01-15T00:00:00", ["RSI"])
        cov = df.attrs["derivatives_coverage"]["funding"]
        assert cov["requested_start"] == pd.Timestamp(
            "2024-01-05T00:00:00").tz_localize("UTC").isoformat()
        assert cov["requested_end"] == pd.Timestamp(
            "2024-01-15T00:00:00").tz_localize("UTC").isoformat()
        assert cov["gap"] is False

    def test_real_gap_still_reported(self, tmp_path):
        """真缺口不漏报：请求 end 超出 funding 实际覆盖 → gap=True，
        available_end 披露实际边界（缺口区间显式标注语义保持）。"""
        from core.analysis.warmup_loader import load_with_warmup

        base = self._env(tmp_path)
        df, _ = load_with_warmup(
            tmp_path, SYMBOL, TF,
            "2024-01-05T00:00:00", "2024-01-31T00:00:00", ["RSI"])
        cov = df.attrs["derivatives_coverage"]["funding"]
        assert cov["gap"] is True
        assert cov["available_end"] == base.loc[
            "2024-01-01":"2024-01-20"].index[-1].isoformat()

    def test_boundary_request_start_equals_coverage_start(self, tmp_path):
        """边界临界：请求起点恰=funding 起点 → gap=False（严格比较
        无边界误报）。"""
        from core.analysis.warmup_loader import load_with_warmup

        self._env(tmp_path)
        df, _ = load_with_warmup(
            tmp_path, SYMBOL, TF,
            "2024-01-01T00:00:00", "2024-01-15T00:00:00", ["RSI"])
        cov = df.attrs["derivatives_coverage"]["funding"]
        assert cov["gap"] is False
