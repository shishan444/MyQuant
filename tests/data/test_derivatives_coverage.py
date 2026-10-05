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
