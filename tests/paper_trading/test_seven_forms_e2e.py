"""七形态端到端测试（rebuild-paper-trading 任务 8.2）。

design §8.2 七形态（总体方案验收「七种策略形态」的权威定义）：
①base=long+pct ②short ③mixed ④atr ⑤trail ⑥scaled（REDUCE+ADD+
三参数显式）⑦timed（max_hold+time_exit_utc 双有）。每形态全链：
上盘（仓库直建任务行——design §11 偏差 2 测试通道）→运行
（process_frames 回放注入，沿 test_executor.py 形制）→出报告
（build_report 滚动+finalize_report 结题固化+get_final_report 取版）。

形态定性断言（design §8.2 形态定性修正）：
- ⑤trail/⑥scaled 新链必跑（完整跑通出报告——旧链拒载对照面随旧
  交易域退役摘除，frontend-switch-and-retire R.2）；
- ④atr 断言面=守卫强化（装载前置拦截+单 bar ATR NaN 静默不回退
  pct）；
- 各形态对应忠实性断言分派（_FORM_CHECKS 映射）。
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.data.storage import save_parquet
from core.features.indicators import compute_all_indicators
from core.paper_trading.reporting import (
    build_report,
    finalize_report,
    get_final_report,
)
from core.paper_trading.runtime.executor import PaperTaskExecutor
from core.paper_trading.runtime.feed import FeedRegistry, FeedSubscription
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import paper
from tests.helpers.data_factory import (
    make_dna,
    make_mixed_dna,
    make_ohlcv,
    make_scaled_dna,
)

_N_BARS = 240
_SEED = 7
_WINDOW = 200
_REPLAY_ALL = "2000-01-01T00:00:00+00:00"
_SLIP = 0.0005
_TF = pd.Timedelta("4h")

_FORMS = ("base", "short", "mixed", "atr", "trail", "scaled", "timed")


def _form_dna(form: str):
    """七形态构造（design §8.2 权威定义逐字对应）。"""
    if form == "base":
        return make_dna()  # long+pct（缺省 stop 0.05/tp 0.10）
    if form == "short":
        return make_dna(direction="short")
    if form == "mixed":
        return make_mixed_dna()
    if form == "atr":
        dna = make_dna()
        return replace(dna, risk_genes=replace(
            dna.risk_genes, sl_mode="atr", stop_loss=2.0, take_profit=4.0))
    if form == "trail":
        dna = make_dna()
        return replace(dna, risk_genes=replace(
            dna.risk_genes, sl_mode="trail", stop_loss=0.05,
            take_profit=None))
    if form == "scaled":
        return make_scaled_dna()  # REDUCE+ADD+三参数显式 0.3/0.2/1.0
    if form == "timed":
        dna = make_dna()
        return replace(dna, risk_genes=replace(
            dna.risk_genes, max_hold=3, time_exit_utc=600))
    raise ValueError(form)


class _FormHarness:
    """七形态端到端装配（沿 test_executor._Harness 形制）。"""

    def __init__(self, tmp_path: Path, dna=None, *, window=_WINDOW,
                 mutate=None):
        self.data_dir = tmp_path / "market"
        self.data_dir.mkdir(parents=True)
        self.df = make_ohlcv(n=_N_BARS, freq="4h", seed=_SEED,
                             base_price=100.0)
        save_parquet(self.df, self.data_dir / "BTCUSDT_4h.parquet")
        enhanced = compute_all_indicators(self.df)
        self.enhanced = mutate(enhanced) if mutate else enhanced

        self.db = tmp_path / "paper.db"
        init_db_ext(self.db)
        self.dna = dna if dna is not None else _form_dna("base")
        self.task_id = paper.create_task(
            self.db, strategy_id="st-1",
            dna_snapshot_json=self.dna.to_json(),
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified",
            initial_cash=10_000.0, leverage=1, window_bars=window)
        self.registry = FeedRegistry(
            self.data_dir, fetcher=lambda **k: self.df,
            funding_refresher=lambda **k: pd.DataFrame(
                {"funding_rate": []},
                index=pd.DatetimeIndex([], name="timestamp")))
        self._dummy = FeedSubscription(on_frames=lambda df, idx: None)
        self.feed = self.registry.acquire("BTCUSDT", "4h", self._dummy)
        self.feed.refresh()

    def run_task(self) -> PaperTaskExecutor:
        ex = PaperTaskExecutor(
            self.db, paper.get_task(self.db, self.task_id),
            registry=self.registry,
            push=lambda tid, p: None,
            on_concluded=lambda tid, st: None,
            start_after=_REPLAY_ALL)
        assert ex.prepare() is True, self.task()["error_detail"]
        # 模拟拾取写 running（回放通道跳过 run()——迁移链不断：
        # pending→running→completed，默认守门时代直跳会被拒；沿
        # test_executor.py 全链回放先例）
        paper.update_task(
            self.db, self.task_id, status="running",
            started_at="2026-10-16T00:00:00+00:00")
        ex.process_frames(self.enhanced, 0)
        return ex

    def task(self) -> dict:
        return paper.get_task(self.db, self.task_id)

    def fills(self) -> list[dict]:
        return paper.list_fills(self.db, self.task_id, limit=1_000_000)


# ── 形态忠实性断言分派（每形态对应面） ──


def _exit_entry_pairs(fills):
    entry = None
    pairs = []
    for f in fills:
        if f["kind"] == "open":
            entry = f
        elif f["kind"] in ("close", "exit_sl", "exit_tp", "exit_max_hold",
                           "exit_time", "exit_liquidation") \
                and entry is not None:
            pairs.append((f, entry))
            entry = None
    return pairs


def _check_base(h):
    """base：首笔 fixed 定仓 margin=权益×ps+SL 触发价=entry×(1−sl)。"""
    fills = h.fills()
    first = next(f for f in fills if f["kind"] == "open")
    open_px = float(h.enhanced.loc[first["bar_time"], "open"])
    assert first["quantity"] == pytest.approx(
        10_000.0 * 0.5 / open_px, rel=1e-9)
    sl = next((f, e) for f, e in _exit_entry_pairs(fills)
              if f["kind"] == "exit_sl")
    assert sl[0]["price"] / (1 - _SLIP) == pytest.approx(
        sl[1]["price"] * 0.95, rel=1e-6)


def _check_short(h):
    """short：空头开仓+TP 对称式（触发价=entry×(1−tp)、买入平仓）。"""
    fills = h.fills()
    first = next(f for f in fills if f["kind"] == "open")
    assert first["side"] == "short"
    tp = next((f, e) for f, e in _exit_entry_pairs(fills)
              if f["kind"] == "exit_tp")
    assert tp[0]["price"] / (1 + _SLIP) == pytest.approx(
        tp[1]["price"] * 0.90, rel=1e-6)


def _check_mixed(h):
    """mixed：DIRECTION 基因驱动双向开仓。"""
    sides = {f["side"] for f in h.fills() if f["kind"] == "open"}
    assert sides == {"long", "short"}, sides


def _check_atr(h):
    """atr：SL 触发价=entry−sl_mult×ATR(判定 bar)（ATR 语义）。"""
    fills = h.fills()
    sl = next((f, e) for f, e in _exit_entry_pairs(fills)
              if f["kind"] == "exit_sl")
    judge_ts = pd.Timestamp(sl[0]["bar_time"])  # 即时类判定 bar=当根
    atr = float(h.enhanced.loc[judge_ts, "atr_14"])
    assert sl[0]["price"] / (1 - _SLIP) == pytest.approx(
        sl[1]["price"] - 2.0 * atr, rel=1e-6)


def _check_trail(h):
    """trail：追踪止损=峰值×(1−sl)（新链原生支持）。"""
    fills = h.fills()
    sls = [(f, e) for f, e in _exit_entry_pairs(fills)
           if f["kind"] == "exit_sl"]
    assert sls, "trail 新链须有追踪止损出场"
    for f, entry in sls:
        peak = float(
            h.enhanced.loc[entry["bar_time"]:f["bar_time"], "high"].max())
        assert f["price"] / (1 - _SLIP) == pytest.approx(
            peak * 0.95, rel=1e-6)


def _check_scaled(h):
    """scaled：减仓 30% 即 30%+加仓权益比例（新链原生支持分批）。"""
    fills = h.fills()
    kinds = {f["kind"] for f in fills}
    assert {"reduce", "add"} <= kinds
    qty = 0.0
    for f in fills:
        if f["kind"] == "open":
            qty = f["quantity"]
        elif f["kind"] == "add":
            qty += f["quantity"]
        elif f["kind"] == "reduce":
            assert f["quantity"] == pytest.approx(qty * 0.3, rel=1e-9), f
            qty -= f["quantity"]
        elif f["kind"] in ("close", "exit_sl", "exit_tp", "exit_max_hold",
                           "exit_time", "exit_liquidation"):
            qty = 0.0


def _check_timed(h):
    """timed：两类时间出场各自出现+出场全为时间族（双有形态）。"""
    kinds = {f["kind"] for f in h.fills()}
    assert "exit_time" in kinds and "exit_max_hold" in kinds
    exit_kinds = {k for k in kinds if k.startswith("exit_")} | \
        ({"close"} if "close" in kinds else set())
    assert exit_kinds <= {"exit_time", "exit_max_hold"}, exit_kinds


_FORM_CHECKS = {
    "base": _check_base,
    "short": _check_short,
    "mixed": _check_mixed,
    "atr": _check_atr,
    "trail": _check_trail,
    "scaled": _check_scaled,
    "timed": _check_timed,
}


def _assert_no_verdict_fields(obj) -> None:
    """报告结构无 verdict/feasible/recommendation 自动判定字段。"""
    banned = ("verdict", "feasible", "recommendation")
    if isinstance(obj, dict):
        for key, value in obj.items():
            assert key not in banned, key
            _assert_no_verdict_fields(value)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _assert_no_verdict_fields(item)


class TestSevenFormsEndToEnd:
    """七形态上盘→运行→出报告全链（design §8.2 权威定义）。"""

    @pytest.mark.parametrize("form", _FORMS)
    def test_form_runs_to_report(self, form, tmp_path):
        """每形态：completed 全程+报告三层+结题固化 v1 可取+对应
        忠实性断言分派。"""
        h = _FormHarness(tmp_path, _form_dna(form))
        h.run_task()

        task = h.task()
        assert task["status"] == "completed", (form, task["error_detail"])
        assert task["bars_processed"] == _WINDOW
        assert h.fills(), f"{form} 须产出成交"

        # 滚动报告（实时计算——任意状态可取）
        report = build_report(h.db, h.data_dir, h.task_id,
                              generated_at="2026-10-16T00:00:00+00:00")
        assert set(report) >= {
            "task_id", "strategy_id", "status", "generated_at",
            "performance", "deviation", "evidence"}
        assert report["status"] == "completed"
        assert report["evidence"]["bars_run"] == _WINDOW
        assert report["evidence"]["research_status"] == "unverified"
        assert set(report["deviation"]) >= {
            "backtest", "diff", "unavailable_reason", "attribution_notes"}
        _assert_no_verdict_fields(report)

        # 结题固化 version=1（终态固化落 pt_report）+缺省最新版可取
        final = finalize_report(h.db, h.data_dir, h.task_id,
                                generated_at="2026-10-16T00:01:00+00:00")
        assert final["performance"] == report["performance"]
        stored = get_final_report(h.db, h.task_id)
        assert stored is not None
        assert stored["report_version"] == 1
        assert stored["as_of"] == "2026-10-16T00:01:00+00:00"

        # 形态对应忠实性断言分派
        _FORM_CHECKS[form](h)

    def test_trail_and_scaled_run_full_chain(self, tmp_path):
        """trail/scaled 两形态新链完整跑通出报告（新链必跑——形态
        定性修正；旧链拒载对照面随旧域退役摘除）。"""
        for form in ("trail", "scaled"):
            dna = _form_dna(form)
            h = _FormHarness(tmp_path / form, dna)
            h.run_task()
            assert h.task()["status"] == "completed"
            report = build_report(h.db, h.data_dir, h.task_id)
            assert report["status"] == "completed"
            assert h.fills(), f"{form} 新链必跑"

    def test_atr_guard_nan_silent_no_pct_fallback(self, tmp_path):
        """atr 形态：守卫强化——单 bar ATR NaN 该 bar 无 SL/TP 意图
        （静默不回退 pct）、次根恢复后防线恢复（旧链放行对照面随旧
        域退役摘除）。"""
        dna = _form_dna("atr")

        # 正常版：首个 SL 触发 bar 定位（守卫用例的对照锚）
        plain = _FormHarness(tmp_path / "plain", dna)
        plain.run_task()
        sls = [f for f in plain.fills() if f["kind"] == "exit_sl"]
        assert sls, "atr 形态须有 SL 出场"
        nan_bar = sls[0]["bar_time"]

        # NaN 版：该 bar 的 ATR 置 NaN——守卫=无出场意图不回退 pct
        def mutate(enhanced):
            e = enhanced.copy()
            e.loc[pd.Timestamp(nan_bar), "atr_14"] = float("nan")
            return e

        guarded = _FormHarness(tmp_path / "guarded", dna, mutate=mutate)
        guarded.run_task()
        fills = guarded.fills()
        assert guarded.task()["status"] == "completed"
        # NaN bar 无止损止盈判定（该 bar 无 SL/TP 成交）
        assert not [f for f in fills
                    if f["kind"] in ("exit_sl", "exit_tp")
                    and f["bar_time"] == nan_bar], "NaN bar 无防线意图"
        # 次根恢复后防线恢复（后续 ATR 有效 bar 正常触发出场）
        later = [f for f in fills
                 if f["kind"] in ("exit_sl", "exit_tp")
                 and f["bar_time"] > nan_bar]
        assert later, "ATR 恢复后防线自动恢复"

    def test_atr_loader_rejects_when_column_unavailable(self, tmp_path):
        """atr 形态守卫·装载前置拦截：ATR 列整体缺失且现算不可得
        （数据短于周期）→error(atr_missing) 不进 running（与旧链
        「载入即拒」等价——对照组 3.2 已载，此处形态 DNA 串联锚）。"""
        dna = make_dna()
        dna = replace(dna, risk_genes=replace(
            dna.risk_genes, sl_mode="atr", stop_loss=2.0, take_profit=4.0,
            atr_period=20))
        h = _FormHarness(tmp_path, dna)
        # 覆盖为 15 根短数据（<atr_period 20——RSI(14) 可算而 ATR(20)
        # 现算不可得：装载拦截在 indicators_unavailable 之前不触发）
        short = make_ohlcv(n=15, freq="4h", seed=_SEED, base_price=100.0)
        save_parquet(short, h.data_dir / "BTCUSDT_4h.parquet")
        ex = PaperTaskExecutor(
            h.db, paper.get_task(h.db, h.task_id), registry=h.registry,
            push=lambda tid, p: None,
            on_concluded=lambda tid, st: None,
            start_after=_REPLAY_ALL)
        assert ex.prepare() is False
        task = h.task()
        assert task["status"] == "error"
        assert "atr_missing" in task["error_detail"]
        assert task["started_at"] is None  # 不进 running
