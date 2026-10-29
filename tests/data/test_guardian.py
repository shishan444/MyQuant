"""数据保鲜守护器测试（add-data-freshness-guardian 契约 1——
VS-1.1~1.6/1.10/1.12②/1.16）。

覆盖面：六态键路径（guard_kline_once 单轮直调+替身注入）、首轮启动
即扫、双计时独立节拍（now_fn 注入推进）、derivs 轮 funding/OI
（guard_derivs_once 直调）、线程韧性、依赖方向（AST 断言零纸盘域
import）、深缺口逐轮收敛（start 锚定判定性断言——end 锚定缺陷实现
必红）、守护器重叠段 keep="first" 语义、双面语义（不碰 feed 内存+
保鲜成果可达装载面）。全部替身驱动零真实网络。
"""
from __future__ import annotations

import ast
import time
from pathlib import Path

import pandas as pd
import pytest

from core.data.fapi.injection import ScriptedRestClient
from core.data.guardian import (
    DERIVS_GUARD_INTERVAL_SECONDS,
    KLINE_GUARD_INTERVAL_SECONDS,
    DataFreshnessGuardian,
)
from core.data.storage import load_parquet, save_parquet
from core.data.timeframes import DEFAULT_TF_FALLBACK_SECONDS, TF_SECONDS
from tests.helpers.data_factory import make_ohlcv

_T0 = pd.Timestamp("2030-01-01T00:00:00Z")
_TF_1M = pd.Timedelta(seconds=TF_SECONDS["1m"])


class FakeClock:
    """注入时钟（推进守护器双计时判定）。"""

    def __init__(self, now: pd.Timestamp):
        self.now = now

    def __call__(self) -> pd.Timestamp:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + pd.Timedelta(seconds=seconds)


class GapFillingRest:
    """深缺口替身：按 start_ts 从全量帧返回从起点起的窗口行。"""

    def __init__(self, full_frame: pd.DataFrame):
        self.frame = full_frame
        self.calls: list[dict] = []

    def fetch_closed_bars(
        self, symbol, timeframe, start_ts, end_ts, *,
        max_bars=None,
    ) -> pd.DataFrame:
        self.calls.append(
            {"start": start_ts, "end": end_ts, "max_bars": max_bars})
        sub = self.frame[
            (self.frame.index >= pd.Timestamp(start_ts))
            & (self.frame.index + _TF_1M <= pd.Timestamp(end_ts))]
        if max_bars is not None:
            return sub.head(max_bars)
        return sub


def _write_kline(data_dir: Path, n: int, *, freq: str = "1min",
                 seed: int = 7) -> pd.DataFrame:
    """写 {BTCUSDT_1m}.parquet 基线帧并返回（index 自 _T0 起）。"""
    frame = make_ohlcv(n=n, freq=freq, seed=seed, start=str(_T0))
    save_parquet(frame, data_dir / "BTCUSDT_1m.parquet")
    return frame


# ── VS-1.1 六态键路径 ─────────────────────────────────────────

class TestKlineSixStates:
    """guard_kline_once 单轮直调：六态逐键断言（status+rest 调用记录
    双向）。"""

    def test_fresh_no_rest_call(self, tmp_path):
        """fresh：尾戳在 now<last+2×周期 内——rest 零调用。"""
        (tmp_path / "d").mkdir()
        frame = _write_kline(tmp_path / "d", n=50)
        rest = ScriptedRestClient()
        clock = FakeClock(frame.index[-1] + _TF_1M)  # now=last+60s<+120s
        guardian = DataFreshnessGuardian(
            tmp_path / "d", rest_client=rest, now_fn=clock)
        results = guardian.guard_kline_once()
        assert results["BTCUSDT_1m"] == "fresh"
        assert rest.closed_bars_calls == []
        # 其余 11 键无文件——skipped_no_file 留痕（不调 rest）
        no_file = [k for k, v in results.items() if v == "skipped_no_file"]
        assert len(no_file) == 11
        assert guardian.status()["kline"]["keys"]["BTCUSDT_1m"] == "fresh"

    @pytest.mark.parametrize("offset_seconds,expected", [
        (119, "fresh"),   # 临界内侧：now=last+119s 仍新鲜
        (120, "ok"),      # 临界：now==last+2×周期 走缺口分支（空帧→ok）
    ])
    def test_fresh_boundary(self, tmp_path, offset_seconds, expected):
        """新鲜判定临界两侧参数化：恰界时刻缺口成立。"""
        (tmp_path / "d").mkdir()
        frame = _write_kline(tmp_path / "d", n=50)
        rest = ScriptedRestClient()  # 脚本空=空帧
        clock = FakeClock(
            frame.index[-1] + pd.Timedelta(seconds=offset_seconds))
        guardian = DataFreshnessGuardian(
            tmp_path / "d", rest_client=rest, now_fn=clock)
        results = guardian.guard_kline_once()
        assert results["BTCUSDT_1m"] == expected

    def test_filled_n_tail_advances(self, tmp_path):
        """filled:N：替身返回 N 行落盘+尾戳推进+rest 调用带窗口。"""
        from core.data.fapi.rest import RUNTIME_BACKFILL_MAX_BARS

        (tmp_path / "d").mkdir()
        frame = _write_kline(tmp_path / "d", n=50)
        last = frame.index[-1]
        seg = make_ohlcv(n=2, freq="1min", seed=9,
                         start=str(last + _TF_1M))
        rest = ScriptedRestClient(closed_bars=[seg])
        clock = FakeClock(last + 10 * _TF_1M)  # 缺口成立
        guardian = DataFreshnessGuardian(
            tmp_path / "d", rest_client=rest, now_fn=clock)
        results = guardian.guard_kline_once()
        assert results["BTCUSDT_1m"] == "filled:2"
        assert len(rest.closed_bars_calls) == 1
        call = rest.closed_bars_calls[0]
        assert call["symbol"] == "BTCUSDT" and call["timeframe"] == "1m"
        assert call["max_bars"] == RUNTIME_BACKFILL_MAX_BARS
        assert call["start_ts"] == last + _TF_1M  # 缺口起点=末根+周期
        merged = load_parquet(tmp_path / "d" / "BTCUSDT_1m.parquet")
        assert merged.index[-1] == seg.index[-1]  # 尾戳推进

    def test_ok_empty_frame(self, tmp_path):
        """ok：缺口成立但端点空响应（窗口内无可得闭合 bar）。"""
        (tmp_path / "d").mkdir()
        frame = _write_kline(tmp_path / "d", n=50)
        rest = ScriptedRestClient()  # 脚本空=空帧
        clock = FakeClock(frame.index[-1] + 10 * _TF_1M)
        guardian = DataFreshnessGuardian(
            tmp_path / "d", rest_client=rest, now_fn=clock)
        assert guardian.guard_kline_once()["BTCUSDT_1m"] == "ok"
        assert len(rest.closed_bars_calls) == 1  # 发起了拉取（空响应）

    def test_skipped_corrupt_no_rest(self, tmp_path):
        """skipped_corrupt：损坏 parquet 字节流——不调 rest 留痕续键。"""
        (tmp_path / "d").mkdir()
        (tmp_path / "d" / "BTCUSDT_1m.parquet").write_bytes(b"garbage")
        rest = ScriptedRestClient()
        clock = FakeClock(_T0 + 100 * _TF_1M)
        guardian = DataFreshnessGuardian(
            tmp_path / "d", rest_client=rest, now_fn=clock)
        results = guardian.guard_kline_once()
        assert results["BTCUSDT_1m"] == "skipped_corrupt"
        assert rest.closed_bars_calls == []  # 不发起拉取

    def test_failed_continues_other_keys(self, tmp_path):
        """failed：替身抛异常记 failed——同轮其余键照常（多键组合）。"""
        (tmp_path / "d").mkdir()
        _write_kline(tmp_path / "d", n=50, seed=7)
        # ETHUSDT_1m 同为 1m 周期基线（缺口同刻成立）
        frame_eth = make_ohlcv(n=50, freq="1min", seed=8,
                               start=str(_T0))
        save_parquet(frame_eth, tmp_path / "d" / "ETHUSDT_1m.parquet")
        # 脚本序=键扫描序（sorted：BTCUSDT_1m 先于 ETHUSDT_1m）——
        # BTCUSDT 异常，ETHUSDT 正常
        rest = ScriptedRestClient(
            closed_bars=[ConnectionError("scripted: 断网"),
                         make_ohlcv(n=1, freq="1min", seed=9,
                                    start=str(_T0 + 100 * _TF_1M))])
        clock = FakeClock(_T0 + 100 * _TF_1M)
        guardian = DataFreshnessGuardian(
            tmp_path / "d", rest_client=rest, now_fn=clock)
        results = guardian.guard_kline_once()
        assert results["BTCUSDT_1m"] == "failed"
        assert results["ETHUSDT_1m"] == "filled:1"  # 同轮其余键照常
        # 守护器对象可用（线程韧性在 VS-1.5 专测）——下轮重试成功
        rest.closed_bars_script = [make_ohlcv(
            n=1, freq="1min", seed=9, start=str(_T0 + 50 * _TF_1M))]
        results2 = guardian.guard_kline_once()
        assert results2["BTCUSDT_1m"] == "filled:1"  # 下轮重试成功


# ── VS-1.2 首轮启动即扫 / VS-1.3 双计时独立 ───────────────────

class TestTimers:
    """守护线程双计时：last_run=None 视为到期（首轮即扫）；两节拍
    独立推进。"""

    def test_first_round_immediate(self, tmp_path):
        """VS-1.2：start_background 后首轮已执行（不等首个 300s）。"""
        (tmp_path / "d").mkdir()
        clock = FakeClock(_T0)
        guardian = DataFreshnessGuardian(
            tmp_path / "d", rest_client=ScriptedRestClient(),
            funding_refresher=lambda **k: pd.DataFrame(),
            oi_refresher=lambda **k: {},
            now_fn=clock)
        try:
            guardian.start_background()
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and (
                    guardian.status()["kline"]["last_run"] is None):
                time.sleep(0.05)
            assert guardian.status()["kline"]["last_run"] is not None
            assert guardian.status()["derivs"]["last_run"] is not None
        finally:
            guardian.stop()

    def test_dual_timers_independent_beats(self, tmp_path):
        """VS-1.3：推进 300s 触发第二轮 K 线轮而 derivs 轮不动；
        推进满 3600s 触发 derivs 轮（derivs 每轮=两币种各一调用）。"""
        (tmp_path / "d").mkdir()
        clock = FakeClock(_T0)
        derivs_calls: list[int] = []

        def spy_funding(**kwargs):
            derivs_calls.append(1)
            return pd.DataFrame()

        guardian = DataFreshnessGuardian(
            tmp_path / "d", rest_client=ScriptedRestClient(),
            funding_refresher=spy_funding,
            oi_refresher=lambda **k: {},
            now_fn=clock)
        try:
            guardian.start_background()
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and len(derivs_calls) < 2:
                time.sleep(0.05)
            assert len(derivs_calls) == 2, (
                "首轮 derivs 即扫（None 到期——两币种各一调用）")
            # 推进 300s：K 线第二轮触发、derivs 不触发（<3600）
            clock.advance(KLINE_GUARD_INTERVAL_SECONDS)
            deadline = time.monotonic() + 5.0
            first_kline_run = guardian.status()["kline"]["last_run"]
            while time.monotonic() < deadline and (
                    guardian.status()["kline"]["last_run"]
                    == first_kline_run):
                time.sleep(0.05)
            assert guardian.status()["kline"]["last_run"] != first_kline_run, (
                "K 线节拍须触发第二轮")
            assert len(derivs_calls) == 2, (
                "K 线节拍推进不得触发 derivs 轮（双计时独立）")
            # 推进满 derivs 节拍（累计 3900s>=3600s）：derivs 第二轮触发
            clock.advance(DERIVS_GUARD_INTERVAL_SECONDS)
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and len(derivs_calls) < 4:
                time.sleep(0.05)
            assert len(derivs_calls) == 4
        finally:
            guardian.stop()


# ── VS-1.4 derivs 轮 ──────────────────────────────────────────

class TestDerivsRound:
    """guard_derivs_once 直调：逐 symbol 显式 data_dir+异常续行。"""

    def test_calls_with_symbol_and_data_dir(self, tmp_path):
        """两 refresher 对 FAPI_SYMBOLS 两币种逐个被调且显式传
        symbol 与 data_dir（调用记录双字段断言）。"""
        (tmp_path / "d").mkdir()
        fund_calls: list[dict] = []
        oi_calls: list[dict] = []

        guardian = DataFreshnessGuardian(
            tmp_path / "d",
            funding_refresher=lambda **k: fund_calls.append(dict(k)),
            oi_refresher=lambda **k: oi_calls.append(dict(k)),
            now_fn=FakeClock(_T0))
        results = guardian.guard_derivs_once()
        assert [c["symbol"] for c in fund_calls] == [
            "BTCUSDT", "ETHUSDT"]
        assert [c["symbol"] for c in oi_calls] == ["BTCUSDT", "ETHUSDT"]
        assert all(c["data_dir"] == tmp_path / "d" for c in fund_calls)
        assert all(c["data_dir"] == tmp_path / "d" for c in oi_calls)
        assert results == {
            "BTCUSDT": {"funding": "ok", "oi": "ok"},
            "ETHUSDT": {"funding": "ok", "oi": "ok"},
        }
        status = guardian.status()["derivs"]
        assert status["last_run"] is not None

    def test_single_symbol_failure_continues(self, tmp_path):
        """ETHUSDT funding 抛异常→该面记 failed；其 OI 与 BTCUSDT
        两面照常 ok（不中断同轮）。"""
        (tmp_path / "d").mkdir()

        def flaky_funding(**kwargs):
            if kwargs.get("symbol") == "ETHUSDT":
                raise ConnectionError("scripted: 归档断网")
            return pd.DataFrame()

        guardian = DataFreshnessGuardian(
            tmp_path / "d",
            funding_refresher=flaky_funding,
            oi_refresher=lambda **k: {},
            now_fn=FakeClock(_T0))
        results = guardian.guard_derivs_once()
        assert results["BTCUSDT"] == {"funding": "ok", "oi": "ok"}
        assert results["ETHUSDT"] == {"funding": "failed", "oi": "ok"}


# ── VS-1.5 线程韧性 ──────────────────────────────────────────

class TestThreadResilience:
    """守护线程永不因单轮异常退出（异常续轮）。"""

    def test_round_exception_thread_survives(self, tmp_path):
        """单轮整体异常后线程 alive+下轮重试成功。"""
        (tmp_path / "d").mkdir()
        guardian = DataFreshnessGuardian(
            tmp_path / "d", rest_client=ScriptedRestClient(),
            funding_refresher=lambda **k: pd.DataFrame(),
            oi_refresher=lambda **k: {},
            now_fn=FakeClock(_T0))
        original = guardian.guard_kline_once
        calls: list[int] = []

        def flaky_round():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("首轮守护单轮异常")
            return original()

        guardian.guard_kline_once = flaky_round  # noqa: SLF001——注入面
        try:
            guardian.start_background()
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and len(calls) < 2:
                time.sleep(0.05)
            assert len(calls) >= 2, "首轮异常后续轮（线程不退出）"
            assert guardian._thread is not None  # noqa: SLF001
            assert guardian._thread.is_alive()  # noqa: SLF001
            assert guardian.status()["kline"]["last_run"] is not None
        finally:
            guardian.stop()


# ── VS-1.6 依赖方向 ──────────────────────────────────────────

def test_dependency_direction_zero_paper_import():
    """AST 断言 guardian.py import 面零 core.paper_trading 子树。"""
    source = Path("core/data/guardian.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    banned: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            banned.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            banned.append(node.module)
    offenders = [m for m in banned if m.startswith("core.paper_trading")]
    assert offenders == [], f"守护器禁纸盘域 import：{offenders}"
    # 同时零字符串引用面（防延迟 import 字符串绕行）
    assert "paper_trading" not in source


def test_default_tf_fallback_single_source():
    """CARD-05：未知周期兜底秒数三写收敛单源——三消费方
    （guardian/feed/scheduler）与 timeframes 绑定同一对象（from
    import 不复制——收敛的动态证明）+值 14400=4h 秒数（沿旧
    runner 形制语义锚）。"""
    import core.data.timeframes as timeframes_mod
    from core.paper_trading.runtime import feed as feed_mod
    from core.paper_trading.runtime import scheduler as scheduler_mod

    single = timeframes_mod.DEFAULT_TF_FALLBACK_SECONDS
    assert single == 14400
    assert single == TF_SECONDS["4h"]  # 兜底取 4h 秒数（语义自洽锚）
    assert DEFAULT_TF_FALLBACK_SECONDS is single  # guardian 消费面
    assert feed_mod.DEFAULT_TF_FALLBACK_SECONDS is single
    assert scheduler_mod.DEFAULT_TF_FALLBACK_SECONDS is single


# ── VS-1.10 深缺口逐轮收敛 ────────────────────────────────────

class TestDeepGapConvergence:
    """start 锚定判定性断言：60 根 1m 缺口经 6 轮×10 根收敛，收敛后
    序列完整覆盖缺口全区间——end 锚定缺陷实现（只补尾部即 fresh）在
    「全区间覆盖+逐轮时间戳唯一」断言下必红。"""

    def test_sixty_bar_gap_six_rounds(self, tmp_path):
        (tmp_path / "d").mkdir()
        full = make_ohlcv(n=160, freq="1min", seed=5, start=str(_T0))
        # 文件初=T0..T99（100 根）；缺口=T100..T159（60 根闭合缺口）
        save_parquet(full.iloc[:100], tmp_path / "d" / "BTCUSDT_1m.parquet")
        # 全闭合边界：now=T160（T159 收盘时刻）
        clock = FakeClock(_T0 + 160 * _TF_1M)
        rest = GapFillingRest(full)
        guardian = DataFreshnessGuardian(
            tmp_path / "d", rest_client=rest, now_fn=clock)

        round_results: list[str] = []
        for _ in range(6):
            round_results.append(
                guardian.guard_kline_once()["BTCUSDT_1m"])
        # 逐轮 ≤10 根（窗口上限）；6 轮恰补 60 根
        assert round_results == ["filled:10"] * 6
        # 每轮起点=上一轮末+周期（逐轮推进——start 锚定）
        starts = [c["start"] for c in rest.calls]
        assert starts[0] == _T0 + 100 * _TF_1M
        for i in range(1, len(starts)):
            assert starts[i] > starts[i - 1]
        assert all(c["max_bars"] == 10 for c in rest.calls)
        # 收敛后 fresh（尾戳新鲜）
        assert guardian.guard_kline_once()["BTCUSDT_1m"] == "fresh"
        # 判定性断言：收敛后序列完整覆盖缺口全区间（60 根全部补齐、
        # 无中段空洞）+时间戳唯一连续（无丢行无重行）
        merged = load_parquet(tmp_path / "d" / "BTCUSDT_1m.parquet")
        assert len(merged) == 160
        assert not merged.index.has_duplicates
        diffs = merged.index.to_series().diff().dropna()
        assert (diffs == _TF_1M).all()  # 逐分钟连续无空洞
        assert merged.index[0] == _T0
        assert merged.index[-1] == _T0 + 159 * _TF_1M


# ── VS-1.12② 守护器重叠段 merge 语义 ─────────────────────────

class TestOverlapKeepFirst:
    """文件已有 T1-T5+替身返 T3-T8→落盘 T1-T8 无重行且 T3-T5 取
    既有值（keep="first"——守护器与 feed 双写者重叠段语义）。"""

    def test_overlap_existing_rows_win(self, tmp_path):
        (tmp_path / "d").mkdir()
        existing = make_ohlcv(n=5, freq="1min", seed=3, start=str(_T0))
        path = tmp_path / "d" / "BTCUSDT_1m.parquet"
        save_parquet(existing, path)
        # 重叠帧=T3..T8（与既有 T3..T5 重叠、T6..T8 新增）
        overlap = make_ohlcv(n=6, freq="1min", seed=99,
                             start=str(_T0 + 2 * _TF_1M))
        rest = ScriptedRestClient(closed_bars=[overlap])
        clock = FakeClock(_T0 + 20 * _TF_1M)  # 缺口成立
        guardian = DataFreshnessGuardian(
            tmp_path / "d", rest_client=rest, now_fn=clock)
        assert guardian.guard_kline_once()["BTCUSDT_1m"] == "filled:6"
        merged = load_parquet(path)
        assert len(merged) == 8  # T1..T8 无重行
        # T3..T5 取既有值（seed=3 的 close，非替身 seed=99 值）
        existing_close = dict(zip(
            existing.index, existing["close"]))
        for ts in existing.index[2:]:
            row_close = float(merged.loc[ts, "close"])
            assert row_close == float(existing_close[ts]), (
                f"{ts} 须为既有值（keep=first）")


# ── VS-1.16 双面语义（文件面 vs 内存面）──────────────────────

class TestDualFaceSemantics:
    """守护器只写文件不碰 feed 内存；保鲜成果可达装载面。"""

    def test_guardian_does_not_touch_feed_memory(self, tmp_path):
        """①守护器单轮执行前后活跃 feed 内存序列引用不变。"""
        from core.paper_trading.runtime.feed import (
            FeedRegistry,
            FeedSubscription,
        )

        (tmp_path / "d").mkdir()
        _write_kline(tmp_path / "d", n=50)
        registry = FeedRegistry(
            tmp_path / "d",
            rest_fetcher=lambda *a, **k: pd.DataFrame(),
            funding_refresher=lambda **k: pd.DataFrame())
        registry.acquire(
            "BTCUSDT", "1m",
            FeedSubscription(on_frames=lambda df, idx: None))
        feed = registry.get("BTCUSDT", "1m")
        before = feed.latest_df
        seg = make_ohlcv(n=1, freq="1min", seed=9,
                         start=str(before.index[-1] + _TF_1M))
        rest = ScriptedRestClient(closed_bars=[seg])
        clock = FakeClock(before.index[-1] + 10 * _TF_1M)
        guardian = DataFreshnessGuardian(
            tmp_path / "d", rest_client=rest, now_fn=clock)
        result = guardian.guard_kline_once()
        assert result["BTCUSDT_1m"] == "filled:1"
        after = feed.latest_df
        assert after is before, "守护器不得触碰 feed 内存序列引用"

    def test_freshness_reaches_loading(self, tmp_path):
        """②保鲜成果可达决策面：守护器 filled 落盘→新 feed acquire
        时 ensure_loaded 从 parquet 装载得新行。"""
        from core.paper_trading.runtime.feed import (
            FeedRegistry,
            FeedSubscription,
        )

        (tmp_path / "d").mkdir()
        frame = _write_kline(tmp_path / "d", n=50)
        seg = make_ohlcv(n=2, freq="1min", seed=9,
                         start=str(frame.index[-1] + _TF_1M))
        rest = ScriptedRestClient(closed_bars=[seg])
        clock = FakeClock(frame.index[-1] + 10 * _TF_1M)
        guardian = DataFreshnessGuardian(
            tmp_path / "d", rest_client=rest, now_fn=clock)
        assert guardian.guard_kline_once()["BTCUSDT_1m"] == "filled:2"
        # 新 feed 装载（纸盘停止后再次 acquire 的等价面）
        registry = FeedRegistry(
            tmp_path / "d",
            rest_fetcher=lambda *a, **k: pd.DataFrame(),
            funding_refresher=lambda **k: pd.DataFrame())
        feed = registry.acquire(
            "BTCUSDT", "1m",
            FeedSubscription(on_frames=lambda df, idx: None))
        loaded = feed.latest_df
        assert len(loaded) == 52  # 装载得守护器补齐的新行
        assert loaded.index[-1] == seg.index[-1]

    def test_concurrent_feed_reconcile_and_guardian_same_key(self, tmp_path):
        """③真实两组件并发（VS-1.16③，与 VS-1.11 互补）：feed 活跃
        落盘与守护器同键增量并发写同一 parquet——经互斥单点串行化，
        行集并集完整落盘（无丢行无重行）。屏障同步两替身取帧点：帧
        已到手、同相位进入各自 merge 序列（读-改-写重叠最大化）。"""
        import threading

        from core.paper_trading.runtime.feed import (
            FeedRegistry,
            FeedSubscription,
        )

        (tmp_path / "d").mkdir()
        _write_kline(tmp_path / "d", n=50)  # 文件基线 T0..T49
        # feed 补 T50..T54、守护器补 T55..T59——两段不相交，并集应 60 行
        feed_seg = make_ohlcv(n=5, freq="1min", seed=21,
                              start=str(_T0 + 50 * _TF_1M))
        guard_seg = make_ohlcv(n=5, freq="1min", seed=22,
                               start=str(_T0 + 55 * _TF_1M))
        barrier = threading.Barrier(2)
        errs: list[BaseException] = []

        def feed_fetcher(symbol, timeframe, start_ts, end_ts, *,
                         max_bars=None):
            barrier.wait(timeout=5.0)  # 取帧点同相位——落盘序列并发窗口
            return feed_seg

        class GuardRest:
            def fetch_closed_bars(self, symbol, timeframe, start_ts, end_ts,
                                  *, max_bars=None):
                barrier.wait(timeout=5.0)
                return guard_seg

        clock = FakeClock(_T0 + 61 * _TF_1M)  # 两面缺口同刻成立
        registry = FeedRegistry(
            tmp_path / "d",
            rest_fetcher=feed_fetcher,
            funding_refresher=lambda **k: pd.DataFrame(),
            now_fn=clock)
        registry.acquire(
            "BTCUSDT", "1m",
            FeedSubscription(on_frames=lambda df, idx: None))
        feed = registry.get("BTCUSDT", "1m")
        guardian = DataFreshnessGuardian(
            tmp_path / "d", rest_client=GuardRest(), now_fn=clock)

        def run_feed():
            try:
                feed.reconcile()
            except BaseException as exc:  # noqa: BLE001——线程面异常回收
                errs.append(exc)

        def run_guardian():
            try:
                guardian.guard_kline_once()
            except BaseException as exc:  # noqa: BLE001
                errs.append(exc)

        t_feed = threading.Thread(target=run_feed)
        t_guard = threading.Thread(target=run_guardian)
        for t in (t_feed, t_guard):
            t.start()
        for t in (t_feed, t_guard):
            t.join(timeout=10.0)
        assert not any(t.is_alive() for t in (t_feed, t_guard))
        assert errs == [], "真实两组件并发不得异常"
        # 判定性断言：并集完整（基线 50+feed 5+守护器 5=60 行）连续
        # 无重行——互斥失效（一方覆盖另一方）或 merge 语义错在此红
        merged = load_parquet(tmp_path / "d" / "BTCUSDT_1m.parquet")
        assert len(merged) == 60
        assert not merged.index.has_duplicates
        diffs = merged.index.to_series().diff().dropna()
        assert (diffs == _TF_1M).all()
        assert merged.index[-1] == _T0 + 59 * _TF_1M
