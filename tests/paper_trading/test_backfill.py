"""启动建库（FixedKeyBackfill/backfill_key）测试（change-1 任务 5）。

覆盖：无文件全量回补（回溯窗口锚）/有文件尾戳补齐/尾戳新鲜不拉/
REST 异常记 failed 重试/全键 done 状态面/幂等续跑（第二轮 done 跳过）。
全合成驱动（rest_fetcher 注入替身——零真实网络）。
"""
from __future__ import annotations

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.data.fapi.stream import FIXED_SUBSCRIBE_KEYS
from core.data.storage import load_parquet
from core.data.timeframes import TF_SECONDS
from core.paper_trading.runtime.feed import (
    FixedKeyBackfill,
    backfill_key,
)
from tests.helpers.data_factory import make_ohlcv

_NOW = pd.Timestamp("2026-10-28T00:00:00Z")


class _ScriptedRest:
    """脚本化 REST（按 (symbol, timeframe) 返回帧或异常；调用记录）。"""

    def __init__(self, frames: dict | None = None, error_keys=None):
        self.frames = frames or {}
        self.error_keys = set(error_keys or ())
        self.calls: list[tuple[str, str, pd.Timestamp, pd.Timestamp]] = []

    def __call__(self, symbol, timeframe, start_ts, end_ts):
        self.calls.append((symbol, timeframe, start_ts, end_ts))
        if (symbol, timeframe) in self.error_keys:
            raise ConnectionError("scripted: 建库断网")
        return self.frames.get((symbol, timeframe), pd.DataFrame())


def _clock(now: pd.Timestamp = _NOW):
    return lambda: now


class TestBackfillKey:
    """单键建库：无文件回补/有文件补齐/尾戳新鲜。"""

    def test_no_file_full_backfill_window(self, tmp_path):
        """无文件：回溯 2 年全量拉取落盘（窗口锚=start=now-2y）。"""
        frame = make_ohlcv(n=50, freq="4h", seed=3)
        rest = _ScriptedRest({("BTCUSDT", "4h"): frame})
        backfill_key(
            tmp_path, "BTCUSDT", "4h",
            rest_fetcher=rest, now_fn=_clock())
        assert len(rest.calls) == 1
        symbol, timeframe, start, end = rest.calls[0]
        assert (symbol, timeframe) == ("BTCUSDT", "4h")
        assert end == _NOW
        assert start == _NOW - pd.DateOffset(years=2)  # 回溯窗口锚
        assert load_parquet(tmp_path / "BTCUSDT_4h.parquet").equals(frame)

    def test_existing_file_tail_gap_backfilled(self, tmp_path):
        """有文件尾戳旧：缺口段补齐（keep-first 合并无重行）。"""
        from core.data.storage import save_parquet

        base = make_ohlcv(n=30, freq="4h", seed=5)
        base.index = base.index.tz_convert("UTC")
        save_parquet(base, tmp_path / "BTCUSDT_4h.parquet")
        last = base.index[-1]
        tf = pd.Timedelta(seconds=TF_SECONDS["4h"])
        # 尾段缺口两根（第 2、3 根已闭合：now >= last+3tf 且 last+3tf+4h<=now）
        segment = pd.DataFrame(
            {"open": [100.0, 100.0], "high": [101.0, 101.0],
             "low": [99.0, 99.0], "close": [100.5, 100.5],
             "volume": [10.0, 10.0]},
            index=pd.DatetimeIndex(
                [last + tf, last + 2 * tf], name="timestamp"))
        rest = _ScriptedRest({("BTCUSDT", "4h"): segment})
        now = last + 3 * tf + tf  # 两根均闭合
        backfill_key(
            tmp_path, "BTCUSDT", "4h",
            rest_fetcher=rest, now_fn=_clock(now))
        assert len(rest.calls) == 1
        assert rest.calls[0][2] == last + tf  # 补拉起点=尾戳+周期
        merged = load_parquet(tmp_path / "BTCUSDT_4h.parquet")
        assert len(merged) == 32
        assert not merged.index.duplicated().any()

    def test_existing_file_fresh_tail_no_fetch(self, tmp_path):
        """尾戳新鲜（末根+2×周期>now）：不拉（幂等续跑零请求）。"""
        from core.data.storage import save_parquet

        base = make_ohlcv(n=30, freq="4h", seed=5)
        save_parquet(base, tmp_path / "BTCUSDT_4h.parquet")
        last = base.index[-1]
        tf = pd.Timedelta(seconds=TF_SECONDS["4h"])
        rest = _ScriptedRest()
        backfill_key(
            tmp_path, "BTCUSDT", "4h",
            rest_fetcher=rest, now_fn=_clock(last + tf))  # 无缺口
        assert rest.calls == []


class TestNotReadySingleMeaning:
    """CARD-03 done=「数据就绪」单义：三空路径一律上抛（run_once 记
    failed 下轮重试）——空态视为暂时性（固定 12 键两年窗必有数据，
    「端点确认无数据」现行键集不可达）。"""

    def test_no_file_empty_response_raises(self, tmp_path):
        """无文件+端点空响应→RuntimeError（原假 done 主路径）。"""
        rest = _ScriptedRest()  # 全键空帧
        with pytest.raises(RuntimeError, match="无文件且端点空响应"):
            backfill_key(
                tmp_path, "BTCUSDT", "4h",
                rest_fetcher=rest, now_fn=_clock())

    def test_empty_existing_file_raises(self, tmp_path):
        """有文件但 0 行→RuntimeError（空文件非数据就绪）。"""
        from core.data.storage import save_parquet

        save_parquet(pd.DataFrame(), tmp_path / "BTCUSDT_4h.parquet")
        rest = _ScriptedRest()
        with pytest.raises(RuntimeError, match="文件空"):
            backfill_key(
                tmp_path, "BTCUSDT", "4h",
                rest_fetcher=rest, now_fn=_clock())

    def test_incremental_empty_response_raises(self, tmp_path):
        """缺口成立（now>=last+2×周期）+增量空响应→RuntimeError
        （单义推论第三条：既非尾戳新鲜亦非增量落盘——不上抛则单义
        不成立）。"""
        from core.data.storage import save_parquet

        base = make_ohlcv(n=30, freq="4h", seed=5)
        save_parquet(base, tmp_path / "BTCUSDT_4h.parquet")
        last = base.index[-1]
        tf = pd.Timedelta(seconds=TF_SECONDS["4h"])
        rest = _ScriptedRest()  # 增量空帧
        with pytest.raises(RuntimeError, match="缺口区间空响应"):
            backfill_key(
                tmp_path, "BTCUSDT", "4h",
                rest_fetcher=rest,
                now_fn=_clock(last + 6 * tf))  # 缺口成立

    def test_empty_response_failed_then_recovered_next_round(
            self, tmp_path):
        """空响应键经 run_once 记 failed（无未捕获逃逸）+次轮脚本换
        帧恢复 done（暂时性空态的重试语义）。"""
        frame = make_ohlcv(n=10, freq="1h", seed=3)
        rest = _ScriptedRest()
        backfill = FixedKeyBackfill(
            tmp_path, rest_fetcher=rest, now_fn=_clock())
        backfill.run_once()
        assert backfill.status()["BTCUSDT_1m"] == "failed"
        rest.frames[("BTCUSDT", "1m")] = frame
        backfill.run_once()
        assert backfill.status()["BTCUSDT_1m"] == "done"


class TestFixedKeyBackfill:
    """12 键编排：状态面/failed 重试/幂等续跑/后台线程。"""

    def test_run_once_all_keys_done(self, tmp_path):
        """单轮全键：12 键逐键建库、状态面全 done。"""
        frame = make_ohlcv(n=10, freq="1h", seed=3)
        rest = _ScriptedRest({
            (symbol, timeframe): frame
            for symbol, timeframe in FIXED_SUBSCRIBE_KEYS})
        backfill = FixedKeyBackfill(
            tmp_path, rest_fetcher=rest, now_fn=_clock())
        backfill.run_once()
        assert backfill.status() == {
            f"{symbol}_{timeframe}": "done"
            for symbol, timeframe in FIXED_SUBSCRIBE_KEYS}
        assert len(rest.calls) == len(FIXED_SUBSCRIBE_KEYS)  # 12 键全拉
        # 12 键全部落盘
        for symbol, timeframe in FIXED_SUBSCRIBE_KEYS:
            assert (tmp_path / f"{symbol}_{timeframe}.parquet").exists()

    def test_failed_key_retried_next_round(self, tmp_path):
        """failed 键下一轮重试：首轮失败→脚本换帧→次轮 done。"""
        frame = make_ohlcv(n=10, freq="1h", seed=3)
        rest = _ScriptedRest(
            {k: frame for k in FIXED_SUBSCRIBE_KEYS if k != ("BTCUSDT", "1m")},
            error_keys={("BTCUSDT", "1m")})
        backfill = FixedKeyBackfill(
            tmp_path, rest_fetcher=rest, now_fn=_clock())
        backfill.run_once()
        assert backfill.status()["BTCUSDT_1m"] == "failed"
        # 故障恢复：脚本换正常帧→次轮仅 failed 键重拉（done 键跳过）
        rest.error_keys.clear()
        rest.frames[("BTCUSDT", "1m")] = frame
        calls_before = len(rest.calls)
        backfill.run_once()
        assert backfill.status()["BTCUSDT_1m"] == "done"
        assert len(rest.calls) == calls_before + 1  # 只重试失败键

    def test_background_loop_exits_when_all_done(self, tmp_path):
        """后台线程：全键 done 后自动退出（不空转轮询）+线程计数
        回落基线（建库线程退出后无残留）。"""
        import threading
        import time

        frame = make_ohlcv(n=10, freq="1h", seed=3)
        rest = _ScriptedRest({
            k: frame for k in FIXED_SUBSCRIBE_KEYS})
        backfill = FixedKeyBackfill(
            tmp_path, rest_fetcher=rest, now_fn=_clock())
        baseline = threading.active_count()  # 建库线程启动前基线
        backfill.start_background()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if not backfill._thread.is_alive():  # noqa: SLF001
                break
            time.sleep(0.02)
        assert not backfill._thread.is_alive()  # noqa: SLF001——全 done 退出
        assert all(v == "done" for v in backfill.status().values())
        # 线程数回落启动前基线（建库线程退出无残留——原 active_count
        # >=1 恒真断言改基线对照）
        assert threading.active_count() == baseline
