"""单因子形态矩阵与组合锚测试（rebuild-paper-trading 任务 8.2）。

design §8.1 特征维度全集——B0 基线起每维偏移一次的逐维行为锚（每维
一专断言）+§8.2 组合锚（工程裁决交集）。回放通道沿 test_executor.py
形制（process_frames 注入——合成 DataFrame 逐根驱动，不经真实时钟与
feed 轮询）；任务行经仓库直建（design §11 偏差 2：测试场景经
repository 直建任务行）。

断言形制：不变量断言为主（从 fills/pt_equity/enhanced 帧独立重算，
被测实现的中间状态不参与期望构造）；关键锚（risk_budget 恒等式）由
手算链独立承载。即时类出场（exit_sl/exit_tp/exit_liquidation）判定
bar=成交 bar 当根，其余意图判定 bar=成交 bar 前一根（次根执行）。

覆盖：
- 单因子偏移族：sl_mode（atr/trail）、分批角色（REDUCE+ADD/含
  ADD_INHIBIT）、分批参数形态（缺省物化）、时间出场（max_hold/
  time_exit_utc/双有）、direction（short/mixed）、杠杆>1、take_profit
  无、反手场景、position_mode（risk_budget）。MTF 维因实现缺陷在案
  阻塞（PaperTaskExecutor.prepare 装载信号试算对 MTF DNA 恒传
  dfs_by_timeframe=None → 装载即拒 error(indicators_unavailable)，
  已上报待修；修复后 xfail(strict) 显形转绿）。
- 组合锚：trail×分批（旧链双拒交集新链首跑）/max_hold×REDUCE（减仓
  不重置计数）/杠杆>1×atr（清算资金费独立锚——design §8.4 不等价层
  两维不整曲线比对、各自独立锚）/risk_budget×atr、risk_budget×
  trail、risk_budget×max_exposure（cap 截断与 ADD 敞口检查两层并存
  交互）。mixed×MTF 同缺陷阻塞。
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.data.storage import save_parquet
from core.features.indicators import compute_all_indicators
from core.paper_trading.kernel.ledger import liquidation_price
from core.paper_trading.runtime.executor import PaperTaskExecutor
from core.paper_trading.runtime.feed import FeedRegistry, FeedSubscription
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import paper
from core.strategy.executor import dna_to_signal_set
from core.strategy.validator import validate_trading_compatibility
from tests.helpers.data_factory import (
    make_dna,
    make_mixed_dna,
    make_ohlcv,
    make_scaled_dna,
)

# 合成数据基线（seed 固定=信号分布确定：与 test_executor.py 同源——
# RSI 首次 <30 于 i=20、入场后连续 add 邻段、RSI 回升段触发 reduce）
_N_BARS = 240
_SEED = 7
# 全回放边界（合成帧历史时刻均早于它——旁路 created_at 起始过滤）
_REPLAY_ALL = "2000-01-01T00:00:00+00:00"
# 任务费滑缺省（create_task 缺省 0.001/0.0005——成交价偏移断言消费）
_FEE = 0.001
_SLIP = 0.0005
_TF = pd.Timedelta("4h")

# 即时类（判定 bar=成交 bar 当根；其余意图次根执行——判定 bar=前一根）
_IMMEDIATE = frozenset({"exit_sl", "exit_tp", "exit_liquidation"})

# 全平族（持仓段终界；reduce 部分出场不算段终）
_FULL_EXIT = frozenset(
    {"close", "exit_sl", "exit_tp", "exit_max_hold", "exit_time",
     "exit_liquidation"})


class _MatrixHarness:
    """形态矩阵回放装配（沿 test_executor._Harness 形制）。"""

    def __init__(self, tmp_path: Path, dna=None, *, window=200, leverage=1,
                 cash=10_000.0, fund_times=(), mutate=None,
                 aux_frames=None):
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.data_dir = tmp_path / "market"
        self.data_dir.mkdir()
        self.df = make_ohlcv(n=_N_BARS, freq="4h", seed=_SEED,
                             base_price=100.0)
        save_parquet(self.df, self.data_dir / "BTCUSDT_4h.parquet")
        self.aux_frames = aux_frames or {}
        for tf, aux_df in self.aux_frames.items():
            save_parquet(aux_df, self.data_dir / f"BTCUSDT_{tf}.parquet")
        enhanced = compute_all_indicators(self.df)
        self.enhanced = mutate(enhanced) if mutate else enhanced
        self.cash = cash

        self.db = tmp_path / "paper.db"
        init_db_ext(self.db)
        self.dna = dna if dna is not None else make_dna()
        self.task_id = paper.create_task(
            self.db, strategy_id="st-1",
            dna_snapshot_json=self.dna.to_json(),
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified",
            initial_cash=cash, leverage=leverage, window_bars=window)

        def _fund(**kwargs):
            idx = pd.DatetimeIndex(
                pd.to_datetime(list(fund_times), utc=True),
                name="timestamp")
            return pd.DataFrame(
                {"funding_rate": [0.0001] * len(idx)}, index=idx)

        self.registry = FeedRegistry(
            self.data_dir, fetcher=lambda **k: self.df,
            funding_refresher=_fund)
        self._dummy = FeedSubscription(on_frames=lambda df, idx: None)
        self.feed = self.registry.acquire("BTCUSDT", "4h", self._dummy)
        for tf in self.aux_frames:
            self.registry.acquire("BTCUSDT", tf, self._dummy)
        for feed in [self.feed] + [
                self.registry.get("BTCUSDT", tf)
                for tf in self.aux_frames]:
            feed.refresh()

    def run_task(self) -> "PaperTaskExecutor":
        ex = PaperTaskExecutor(
            self.db, paper.get_task(self.db, self.task_id),
            registry=self.registry,
            push=lambda tid, p: None,
            on_concluded=lambda tid, st: None,
            start_after=_REPLAY_ALL)
        # MTF 辅周期帧注入（生产路径经辅周期 feed 广播写入 _aux_frames；
        # 直驱回放通道等效注入——_assemble_mtf_frames 消费面）
        ex._aux_frames.update(self.aux_frames)
        assert ex.prepare() is True, self.task()["error_detail"]
        ex.process_frames(self.enhanced, 0)
        return ex

    def task(self) -> dict:
        return paper.get_task(self.db, self.task_id)

    def fills(self) -> list[dict]:
        return paper.list_fills(self.db, self.task_id, limit=1_000_000)

    def equities(self) -> list[dict]:
        return paper.list_equity_snapshots(
            self.db, self.task_id, limit=1_000_000)

    def fundings(self) -> list[dict]:
        return paper.list_funding_records(
            self.db, self.task_id, limit=1_000_000)


# ── 从表行独立重算的辅助（期望构造不消费被测实现中间状态） ──


def _bar_index(h: _MatrixHarness, bar_time: str) -> int:
    """fill bar_time → enhanced 帧行号。"""
    ts = pd.Timestamp(bar_time)
    return int(h.enhanced.index.get_loc(ts))


def _row(h: _MatrixHarness, idx: int) -> pd.Series:
    return h.enhanced.iloc[idx]


def _judge_idx(h: _MatrixHarness, fill: dict) -> int:
    """成交 fill → 判定 bar 行号（即时类当根/其余次根=前一根）。"""
    i = _bar_index(h, fill["bar_time"])
    return i if fill["kind"] in _IMMEDIATE else i - 1


def _prev_balance(h: _MatrixHarness, bar_time: str) -> float:
    """执行 bar 前最后权益快照的 balance（空仓期 equity=balance 精确；
    首笔前回退 initial_cash）。"""
    prev = [e for e in h.equities() if e["bar_time"] < bar_time]
    return float(prev[-1]["balance"]) if prev else h.cash


def _equity_then(h: _MatrixHarness, fill: dict, entry_px: float,
                 qty_before: float, margin_before: float) -> float:
    """意图执行时点权益（逐仓总权益=balance+仓位保证金+未实现盈亏——
    open 价估值，与 Ledger.equity(exec_bar.open) 同口径的独立重算；
    margin 项计回是 P0 修复后的正确契约，此处独立承载不消费实现）。"""
    open_px = float(_row(h, _bar_index(h, fill["bar_time"]))["open"])
    sign = 1.0 if (fill["side"] or "long") == "long" else -1.0
    return (_prev_balance(h, fill["bar_time"]) + margin_before
            + sign * qty_before * (open_px - entry_px))


def _exit_entry_pairs(fills: list[dict]) -> list[tuple[dict, dict]]:
    """(出场 fill, 同笔交易入场 fill) 列表——持仓段重演（出场重置，
    防跨段错配 next-early 匹配到上一笔入场）。"""
    entry = None
    pairs: list[tuple[dict, dict]] = []
    for f in fills:
        if f["kind"] == "open":
            entry = f
        elif f["kind"] in _FULL_EXIT and entry is not None:
            pairs.append((f, entry))
            entry = None
    return pairs


# ── B0 基线（偏移基准存在性） ──


class TestBaselineB0:
    """B0：pct+无分批+无时间出场+long+单周期+杠杆 1+有 TP+参数显式
    +fixed 档（design §8.2——受控等价主锚归 8.1，此处为偏移基准）。"""

    def test_b0_runs_to_completion_and_first_sizing(self, tmp_path):
        """B0 全程 completed+首笔定仓 margin=权益×position_size。"""
        h = _MatrixHarness(tmp_path)
        h.run_task()
        task = h.task()
        assert task["status"] == "completed"
        assert task["bars_processed"] == 200
        first = next(f for f in h.fills() if f["kind"] == "open")
        i = _bar_index(h, first["bar_time"])
        open_px = float(_row(h, i)["open"])
        # fixed 档：qty=权益×ps×lev/开盘价（首笔权益=initial_cash）
        assert first["quantity"] == pytest.approx(
            h.cash * 0.5 * 1 / open_px, rel=1e-9)
        assert first["price"] == pytest.approx(
            open_px * (1 + _SLIP), rel=1e-9)
        assert first["side"] == "long"


# ── 单因子偏移族（每维一专断言） ──


class TestSingleFactorOffsets:
    """design §8.1 每维度代表值一次偏移——逐维行为锚。"""

    def test_sl_mode_atr_stop_distance_is_atr_multiples(self, tmp_path):
        """sl_mode=atr：SL/TP 触发价=entry∓sl_mult×ATR(判定 bar)
        （ATR 语义而非 pct 距离）。"""
        dna = make_dna()
        dna = replace(dna, risk_genes=replace(
            dna.risk_genes, sl_mode="atr", stop_loss=2.0, take_profit=4.0))
        h = _MatrixHarness(tmp_path, dna)
        h.run_task()
        fills = h.fills()
        exits = [(ex, entry) for ex, entry in _exit_entry_pairs(fills)
                 if ex["kind"] in ("exit_sl", "exit_tp")]
        assert exits, "atr 档须有防线出场"
        for ex, entry in exits[:2]:
            atr = float(_row(h, _judge_idx(h, ex))["atr_14"])
            mult = 2.0 if ex["kind"] == "exit_sl" else 4.0
            trigger = ex["price"] / (1 - _SLIP)  # 多头卖出=触发价×(1−slip)
            assert trigger == pytest.approx(
                entry["price"] - mult * atr, rel=1e-6), (ex, entry)

    def test_sl_mode_trail_stop_follows_peak(self, tmp_path):
        """sl_mode=trail：SL 触发价=持仓期峰值×(1−sl)（峰值回撤移动
        止损，非入场价固定距离）。"""
        dna = make_dna()
        dna = replace(dna, risk_genes=replace(
            dna.risk_genes, sl_mode="trail", stop_loss=0.05,
            take_profit=None))
        h = _MatrixHarness(tmp_path, dna)
        h.run_task()
        sls = [(sl, entry) for sl, entry
               in _exit_entry_pairs(h.fills())
               if sl["kind"] == "exit_sl"]
        assert sls, "trail 档须有追踪止损出场"
        peak_moved = False
        for sl, entry in sls:
            span = h.enhanced.loc[entry["bar_time"]:sl["bar_time"], "high"]
            peak = float(span.max())
            trigger = sl["price"] / (1 - _SLIP)
            assert trigger == pytest.approx(peak * 0.95, rel=1e-6), sl
            peak_moved = peak_moved or peak > entry["price"] * 1.005
        assert peak_moved, "追踪线须随峰值上移（至少一笔 peak>入场价）"

    def test_scaling_roles_execute_declared_fractions(self, tmp_path):
        """分批角色 REDUCE+ADD：减仓量=exit_fraction×当时持仓、加仓
        保证金=add_fraction×当时权益（显式声明 0.3/0.2）。"""
        h = _MatrixHarness(tmp_path, make_scaled_dna(
            exit_fraction=0.3, add_fraction=0.2, max_exposure=1.0))
        h.run_task()
        fills = h.fills()
        assert [f for f in fills if f["kind"] == "reduce"], \
            "分批角色须产生 reduce 成交"
        adds = [f for f in fills if f["kind"] == "add"]
        assert adds, "分批角色须产生 add 成交"
        # 减仓：逐笔核对减量=0.3×前持仓（持仓链重演）
        qty = 0.0
        for f in fills:
            if f["kind"] == "open":
                qty = f["quantity"]
            elif f["kind"] == "add":
                qty += f["quantity"]
            elif f["kind"] == "reduce":
                assert f["quantity"] == pytest.approx(
                    qty * 0.3, rel=1e-9), f
                qty -= f["quantity"]
            elif f["kind"] in _FULL_EXIT:
                qty = 0.0
        # 加仓：qty_add=add_fraction×当时权益×lev/开盘价（权益独立重算）
        add = adds[0]
        first_open = next(f for f in fills if f["kind"] == "open")
        eq = _equity_then(h, add, first_open["price"],
                          first_open["quantity"],
                          first_open["quantity"] * first_open["price"])
        open_px = float(_row(h, _bar_index(h, add["bar_time"]))["open"])
        assert add["quantity"] == pytest.approx(
            0.2 * eq * 1 / open_px, rel=1e-6)

    def test_add_inhibit_suppresses_inhibited_bars(self, tmp_path):
        """含 ADD_INHIBIT：抑制剂条件成立的 bar 不加仓（RSI<32 抑制——
        信号面为无抑制版的严格子集且成交信号 bar 均在 [32,35)）。"""
        dna_plain = make_scaled_dna()
        dna_inhib = make_scaled_dna(add_inhibit=True)
        h = _MatrixHarness(tmp_path, dna_inhib)
        h.run_task()
        sig_plain = dna_to_signal_set(dna_plain, h.enhanced)
        sig_inhib = dna_to_signal_set(dna_inhib, h.enhanced)
        # 信号面：抑制剔除部分 add bar（数量严格少）
        assert int(sig_inhib.adds.sum()) < int(sig_plain.adds.sum())
        # 成交面：每个 add 成交的信号 bar（执行 bar 前一根）RSI∈[32,35)
        for f in h.fills():
            if f["kind"] != "add":
                continue
            rsi = float(_row(h, _bar_index(h, f["bar_time"]) - 1)["rsi_14"])
            assert 32.0 <= rsi < 35.0, (f, rsi)

    def test_scaling_defaults_materialize_engine_values(self, tmp_path):
        """分批参数缺省物化：不声明三参数时 reduce=0.5、add=0.1×权益
        （与回测引擎装配同值的执行侧锚）。"""
        h = _MatrixHarness(tmp_path, make_scaled_dna(
            exit_fraction=None, add_fraction=None, max_exposure=None))
        h.run_task()
        fills = h.fills()
        qty = 0.0
        reduced = added = False
        for f in fills:
            if f["kind"] == "open":
                qty, first_open = f["quantity"], f
            elif f["kind"] == "add":
                if not added:
                    eq = _equity_then(h, f, first_open["price"], qty,
                                      first_open["quantity"]
                                      * first_open["price"])
                    open_px = float(
                        _row(h, _bar_index(h, f["bar_time"]))["open"])
                    assert f["quantity"] == pytest.approx(
                        0.1 * eq * 1 / open_px, rel=1e-6)
                    added = True
                qty += f["quantity"]
            elif f["kind"] == "reduce":
                assert f["quantity"] == pytest.approx(
                    qty * 0.5, rel=1e-9), f
                reduced = True
                qty -= f["quantity"]
            elif f["kind"] in _FULL_EXIT:
                qty = 0.0
        assert reduced, "缺省 exit_fraction=0.5 须生效"

    def test_max_hold_forces_exit_at_declared_bars(self, tmp_path):
        """时间出场 max_hold=3：到期必平+出场执行 bar−入场执行 bar
        =3 根（入场成交 bar 计第 1 根）。"""
        dna = make_dna()
        dna = replace(dna, risk_genes=replace(dna.risk_genes, max_hold=3))
        h = _MatrixHarness(tmp_path, dna)
        h.run_task()
        fills = h.fills()
        mh = [(ex, entry) for ex, entry in _exit_entry_pairs(fills)
              if ex["kind"] == "exit_max_hold"]
        assert mh, "max_hold 到期必平"
        for ex, entry in mh:
            bars = int((pd.Timestamp(ex["bar_time"])
                        - pd.Timestamp(entry["bar_time"])) / _TF)
            assert bars == 3, (ex, entry)

    def test_time_exit_forces_close_at_declared_minute(self, tmp_path):
        """time_exit_utc=600：时刻必平+判定 bar 开盘区间覆盖 10:00
        UTC（区间覆盖式，非字面相等）。"""
        dna = make_dna()
        dna = replace(dna, risk_genes=replace(
            dna.risk_genes, time_exit_utc=600))
        h = _MatrixHarness(tmp_path, dna)
        h.run_task()
        fills = h.fills()
        te = [f for f in fills if f["kind"] == "exit_time"]
        assert te, "time_exit 时刻必平"
        for ex in te:
            judge = _row(h, _judge_idx(h, ex))
            minute = judge.name.hour * 60 + judge.name.minute
            assert (600 - minute) % 1440 < 240, ex

    def test_time_exit_and_max_hold_coexist(self, tmp_path):
        """时间出场双有：max_hold=3 与 time_exit_utc=600 两机制交替
        先手触发（两类出场各自出现）。"""
        dna = make_dna()
        dna = replace(dna, risk_genes=replace(
            dna.risk_genes, max_hold=3, time_exit_utc=600))
        h = _MatrixHarness(tmp_path, dna)
        h.run_task()
        kinds = {f["kind"] for f in h.fills()}
        assert "exit_time" in kinds
        assert "exit_max_hold" in kinds

    def test_short_direction_symmetric_stops(self, tmp_path):
        """direction=short：空头开仓+SL/TP 触发价对称式
        （SL=entry×(1+sl)、TP=entry×(1−tp)——方向翻转）。"""
        dna = make_dna(direction="short")
        h = _MatrixHarness(tmp_path, dna)
        h.run_task()
        fills = h.fills()
        first = next(f for f in fills if f["kind"] == "open")
        assert first["side"] == "short"
        tp = [(f, entry) for f, entry in _exit_entry_pairs(fills)
              if f["kind"] == "exit_tp"]
        sl = [(f, entry) for f, entry in _exit_entry_pairs(fills)
              if f["kind"] == "exit_sl"]
        assert tp, "空头 TP 须触发（对称式锚）"
        # 空头 TP=买入平仓：价=触发价×(1+slip)；触发价=entry×(1−0.10)
        f, entry = tp[0]
        assert f["price"] / (1 + _SLIP) == pytest.approx(
            entry["price"] * 0.90, rel=1e-6)
        if sl:
            f, entry = sl[0]
            # 空头 SL 同为买入平仓：价=触发价×(1+slip)
            assert f["price"] / (1 + _SLIP) == pytest.approx(
                entry["price"] * 1.05, rel=1e-6)

    def test_mixed_direction_opens_both_sides(self, tmp_path):
        """direction=mixed：DIRECTION 基因驱动双向开仓
        （side 集合={long,short}）。"""
        h = _MatrixHarness(tmp_path, make_mixed_dna())
        h.run_task()
        sides = {f["side"] for f in h.fills() if f["kind"] == "open"}
        assert sides == {"long", "short"}, sides

    def test_mtf_dimension_runs_with_aux_timeframe(self, tmp_path):
        """MTF=多周期：1d(structure)+4h(execution) legacy AND 组合上盘
        运行（辅周期 parquet 就位+装载校验通过+成交产出）。"""
        from core.strategy.dna import (
            LogicGenes, SignalRole, StrategyDNA, TimeframeLayer,
        )
        from tests.helpers.data_factory import make_signal_gene

        def layer_gene(role, cond, val):
            return make_signal_gene(
                role=role, condition_type=cond, condition_value=val)

        dna = StrategyDNA(
            signal_genes=[],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
            execution_genes=make_dna().execution_genes,
            risk_genes=make_dna().risk_genes,
            layers=[
                TimeframeLayer(
                    timeframe="1d",
                    signal_genes=[layer_gene(SignalRole.ENTRY_TRIGGER,
                                             "lt", 40),
                                  layer_gene(SignalRole.EXIT_TRIGGER,
                                             "gt", 70)],
                    logic_genes=LogicGenes(entry_logic="AND",
                                           exit_logic="AND")),
                TimeframeLayer(
                    timeframe="4h",
                    signal_genes=[layer_gene(SignalRole.ENTRY_TRIGGER,
                                             "lt", 30),
                                  layer_gene(SignalRole.EXIT_TRIGGER,
                                             "gt", 70)],
                    logic_genes=LogicGenes(entry_logic="AND",
                                           exit_logic="AND")),
            ],
            cross_layer_logic="AND")
        h = _MatrixHarness(tmp_path, dna, aux_frames={
            "1d": compute_all_indicators(make_ohlcv(
                n=150, freq="1d", seed=_SEED + 1, base_price=100.0))})
        h.run_task()
        assert h.fills(), "MTF 任务须产出成交"

    def test_leverage_above_one_scales_quantity(self, tmp_path):
        """杠杆>1：定仓 qty=权益×ps×lev/开盘价（名义随杠杆放大）。"""
        dna = make_dna()
        dna = replace(dna, risk_genes=replace(dna.risk_genes, leverage=5))
        h = _MatrixHarness(tmp_path, dna, leverage=5)
        h.run_task()
        first = next(f for f in h.fills() if f["kind"] == "open")
        open_px = float(_row(h, _bar_index(h, first["bar_time"]))["open"])
        assert first["quantity"] == pytest.approx(
            h.cash * 0.5 * 5 / open_px, rel=1e-9)

    def test_no_take_profit_disables_tp_line(self, tmp_path):
        """take_profit 无：全程无 exit_tp 成交（未声明不启用）。"""
        dna = make_dna()
        dna = replace(dna, risk_genes=replace(
            dna.risk_genes, take_profit=None))
        h = _MatrixHarness(tmp_path, dna)
        h.run_task()
        kinds = [f["kind"] for f in h.fills()]
        assert "exit_tp" not in kinds
        assert "exit_sl" in kinds, "SL 防线照常"

    def test_reverse_closes_then_reopens_opposite(self, tmp_path):
        """反手场景：方向翻转触发两步反手（本根 close reason=reverse、
        次根反向 open——bar 内平后立开不存在）。"""
        h = _MatrixHarness(tmp_path, make_mixed_dna())
        h.run_task()
        fills = h.fills()
        reverses = [f for f in fills if f["reason"] == "reverse"]
        assert reverses, "mixed 方向翻转须出现反手平仓"
        rv = reverses[0]
        after = [f for f in fills if f["bar_time"] > rv["bar_time"]]
        assert after, "反手后次根须再开仓"
        nxt = after[0]
        assert nxt["kind"] == "open"
        assert nxt["side"] != rv["side"], "反手=方向翻转再开仓"

    def test_position_mode_risk_budget_identity(self, tmp_path):
        """position_mode=risk_budget：每笔入场 qty×(价×sl)=权益×
        risk_fraction（未截断恒等式手算锚——预算恒定金额浮动）。"""
        dna = make_dna()
        dna = replace(dna, risk_genes=replace(
            dna.risk_genes, position_mode="risk_budget",
            risk_fraction=0.01, stop_loss=0.02, take_profit=None))
        h = _MatrixHarness(tmp_path, dna)
        h.run_task()
        for f in h.fills():
            if f["kind"] != "open":
                continue
            equity = _prev_balance(h, f["bar_time"])  # 空仓期=balance
            base = float(_row(h, _bar_index(h, f["bar_time"]))["open"])
            # 定仓距离按滑点前基准价（size_open 消费 base_price 口径）
            assert f["quantity"] * (base * 0.02) == pytest.approx(
                equity * 0.01, rel=1e-6), f


# ── 组合锚（工程裁决交集，design §8.2） ──


class TestCombinationAnchors:
    """组合锚七件（mixed×MTF 因 MTF 缺陷阻塞，见单因子 MTF 维）。"""

    def test_trail_times_scaling_first_run_on_new_chain(self, tmp_path):
        """trail×分批：旧链双拒交集（validate_trading_compatibility
        两判据齐拒）新链首跑（add/reduce/追踪止损齐产出）。"""
        dna = make_scaled_dna(sl_mode="trail", stop_loss=0.05,
                              take_profit=None)
        reasons = validate_trading_compatibility(dna, "paper")
        joined = " ".join(reasons)
        assert reasons and "trail" in joined, reasons
        assert "分批" in joined, reasons
        h = _MatrixHarness(tmp_path, dna)
        h.run_task()
        assert h.task()["status"] == "completed"
        kinds = {f["kind"] for f in h.fills()}
        assert {"add", "reduce", "exit_sl"} <= kinds

    def test_max_hold_times_reduce_does_not_reset_count(self, tmp_path):
        """max_hold×REDUCE：减仓不重置持有计数——reduce 发生后
        exit_max_hold 仍按入场 bar 起算 32 根到期（非从减仓 bar 重数）。"""
        h = _MatrixHarness(tmp_path, make_scaled_dna(max_hold=32))
        h.run_task()
        fills = h.fills()
        # 找一段「open→（add*）→reduce+→exit_max_hold」的交易
        entry = None
        seg_reduce = 0
        target = None
        for f in fills:
            if f["kind"] == "open":
                entry, seg_reduce = f, 0
            elif f["kind"] == "reduce" and entry is not None:
                seg_reduce += 1
            elif f["kind"] == "exit_max_hold" and entry is not None:
                if seg_reduce > 0:
                    target = (entry, f)
                    break
                entry = None
            elif f["kind"] in _FULL_EXIT:
                entry = None
        assert target is not None, "须存在 reduce 后 max_hold 到期的段"
        entry, ex = target
        bars = int((pd.Timestamp(ex["bar_time"])
                    - pd.Timestamp(entry["bar_time"])) / _TF)
        assert bars == 32, "减仓不重置计数——到期恒为入场起 32 根"

    def test_leverage_atr_liquidation_and_funding_independent_anchors(
            self, tmp_path):
        """杠杆>1×atr：清算与资金费两维独立锚（design §8.4 不等价层
        ——不整曲线比对）——清算：跳空 bar 触发+触发价=market_model 解式
        （seq=1 判定序优先 SL）；资金费：逐点对账（rate=parquet 原值、
        金额=qty×结算价×rate×方向符号）。"""
        dna = make_dna()
        dna = replace(dna, risk_genes=replace(
            dna.risk_genes, sl_mode="atr", stop_loss=5.0, take_profit=None,
            leverage=10, position_size=1.0))

        def gap(enhanced: pd.DataFrame) -> pd.DataFrame:
            # 开仓次根 bar 跳空：low 压到清算价下方（同 bar 也击穿 SL
            # 触发价——判定序清算优先的对照构造；ATR 列保持原值）
            e = enhanced.copy()
            e.loc[pd.Timestamp("2024-01-04 16:00:00+00:00"), "low"] = 83.0
            return e

        h = _MatrixHarness(tmp_path, dna, leverage=10, cash=5_000.0,
                           fund_times=["2024-01-04 16:00:00.001"],
                           mutate=gap)
        h.run_task()
        fills = h.fills()
        liq = [f for f in fills if f["kind"] == "exit_liquidation"]
        assert liq, "跳空 bar 须触发清算"
        assert liq[0]["seq"] == 1, "清算=八步序第 1 步（优先 SL）"
        # 触发价独立重算（liquidation_price 单源——输入全部从表行/帧读）
        first_open = next(f for f in fills if f["kind"] == "open")
        entry, qty = first_open["price"], first_open["quantity"]
        margin = qty * entry / 10
        funded = sum(f["amount"] for f in h.fundings()
                     if f["status"] == "settled"
                     and f["bar_time"] < liq[0]["bar_time"])
        balance = h.cash - margin - first_open["fee"] - funded
        mark = float(_row(h, _bar_index(h, liq[0]["bar_time"]))["close"])
        p_liq = liquidation_price("long", entry, qty, margin, balance, mark)
        assert liq[0]["price"] / (1 - _SLIP) == pytest.approx(
            p_liq, rel=1e-6), "清算成交价=触发价（market_model 解式）"
        # 资金费独立锚：settled 行 rate=注入原值+金额=qty×结算价×rate
        settled = [f for f in h.fundings() if f["status"] == "settled"]
        assert settled
        for f in settled:
            assert f["rate"] == 0.0001
            assert f["amount"] == pytest.approx(
                f["position_quantity"] * f["settle_price"] * f["rate"],
                rel=1e-9), f

    def test_mixed_times_mtf_runs(self, tmp_path):
        """mixed×MTF：mixed 方向+多周期层组合上盘运行出成交
        （MTF 的 mixed 方向走结构层角色条件推断——1d structure 层
        price_above EMA20 定方向、4h 执行层 RSI<30 定触发，AND 门控）。"""
        from core.strategy.dna import (
            LogicGenes, SignalRole, StrategyDNA, TimeframeLayer,
        )
        from tests.helpers.data_factory import make_signal_gene

        def layer_gene(role, cond, val, ind="RSI", params=None):
            return make_signal_gene(
                indicator=ind, params=params, role=role,
                condition_type=cond, condition_value=val)

        dna = StrategyDNA(
            signal_genes=[],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
            execution_genes=make_mixed_dna().execution_genes,
            risk_genes=make_mixed_dna().risk_genes,
            layers=[
                TimeframeLayer(
                    timeframe="1d", role="structure",
                    signal_genes=[
                        layer_gene(SignalRole.ENTRY_TRIGGER,
                                   "price_above", 0, ind="EMA",
                                   params={"period": 20}),
                        layer_gene(SignalRole.EXIT_TRIGGER, "gt", 70)],
                    logic_genes=LogicGenes(entry_logic="AND",
                                           exit_logic="AND")),
                TimeframeLayer(
                    timeframe="4h",
                    signal_genes=[layer_gene(SignalRole.ENTRY_TRIGGER,
                                             "lt", 30),
                                  layer_gene(SignalRole.EXIT_TRIGGER,
                                             "gt", 70),
                                  # mixed+layers 值域校验要求层内含
                                  # DIRECTION 基因（方向推断由结构层承载）
                                  layer_gene(SignalRole.DIRECTION,
                                             "gt", 25)],
                    logic_genes=LogicGenes(entry_logic="AND",
                                           exit_logic="AND")),
            ],
            cross_layer_logic="AND")
        h = _MatrixHarness(tmp_path, dna, aux_frames={
            "1d": compute_all_indicators(make_ohlcv(
                n=150, freq="1d", seed=_SEED + 1, base_price=100.0))})
        h.run_task()
        opens = [f for f in h.fills() if f["kind"] == "open"]
        assert opens, "mixed×MTF 须产出开仓"
        assert {f["side"] for f in opens} <= {"long", "short"}

    def test_risk_budget_times_atr_distance_identity(self, tmp_path):
        """risk_budget×atr：定仓距离=sl_mult×当根 ATR（恒等式
        qty×(2×ATR_执行bar)=权益×risk_fraction——entry_atr 层间接线
        修复后语义触达）。"""
        dna = make_dna()
        dna = replace(dna, risk_genes=replace(
            dna.risk_genes, position_mode="risk_budget",
            risk_fraction=0.01, sl_mode="atr", stop_loss=2.0,
            take_profit=4.0))
        h = _MatrixHarness(tmp_path, dna)
        h.run_task()
        for f in h.fills():
            if f["kind"] != "open":
                continue
            atr = float(_row(h, _bar_index(h, f["bar_time"]))["atr_14"])
            equity = _prev_balance(h, f["bar_time"])
            assert f["quantity"] * (2.0 * atr) == pytest.approx(
                equity * 0.01, rel=1e-6), f

    def test_risk_budget_times_trail_distance_identity(self, tmp_path):
        """risk_budget×trail：定仓距离=价×sl 比例域（D-6 终态——
        恒等式 qty×(价×sl)=权益×risk_fraction）。"""
        dna = make_dna()
        dna = replace(dna, risk_genes=replace(
            dna.risk_genes, position_mode="risk_budget",
            risk_fraction=0.01, sl_mode="trail", stop_loss=0.02,
            take_profit=None))
        h = _MatrixHarness(tmp_path, dna)
        h.run_task()
        for f in h.fills():
            if f["kind"] != "open":
                continue
            equity = _prev_balance(h, f["bar_time"])
            base = float(_row(h, _bar_index(h, f["bar_time"]))["open"])
            assert f["quantity"] * (base * 0.02) == pytest.approx(
                equity * 0.01, rel=1e-6), f

    def test_risk_budget_times_max_exposure_cap_and_add_gate(self, tmp_path):
        """risk_budget×max_exposure：cap 截断与 ADD 敞口检查两层并存
        交互——截断侧（名义=cap 恒满→ADD 全拒）+未截断侧（恒等式成立
        +敞口余量内首个 ADD 放行、次个拒）。"""
        # 侧一：rf=0.3 → 名义预算=权益×15 >cap=权益×1.0 → 截断+ADD 全拒
        dna_cap = make_scaled_dna(
            position_mode="risk_budget", risk_fraction=0.3,
            stop_loss=0.02, take_profit=None, leverage=10,
            add_fraction=0.2, max_exposure=1.0)
        h1 = _MatrixHarness(tmp_path / "cap", dna_cap, leverage=10)
        h1.run_task()
        first1 = next(f for f in h1.fills() if f["kind"] == "open")
        i = _bar_index(h1, first1["bar_time"])
        # cap 截断：qty×定仓基准价=权益×min(lev,me)=10_000
        assert first1["quantity"] * float(_row(h1, i)["open"]) == \
            pytest.approx(10_000.0 * 1.0, rel=1e-6)
        assert not [f for f in h1.fills() if f["kind"] == "add"], \
            "cap 截断后敞口恒满——ADD 全拒"

        # 侧二：rf=0.03 → 名义 1.5×权益<cap 2.0×权益（未截断）+
        # add_fraction=0.04（首个 ADD 名义 0.4×权益放行、次个超限拒）
        dna_gate = make_scaled_dna(
            position_mode="risk_budget", risk_fraction=0.03,
            stop_loss=0.02, take_profit=None, leverage=10,
            add_fraction=0.04, max_exposure=2.0)
        h2 = _MatrixHarness(tmp_path / "gate", dna_gate, leverage=10)
        h2.run_task()
        fills2 = h2.fills()
        first2 = next(f for f in fills2 if f["kind"] == "open")
        j = _bar_index(h2, first2["bar_time"])
        # 未截断恒等式：qty×(基准价×sl)=权益×rf（15000=10000×0.03/0.02）
        base = float(_row(h2, j)["open"])
        assert first2["quantity"] * (base * 0.02) == pytest.approx(
            10_000.0 * 0.03, rel=1e-6)
        # 敞口门：每个持仓段至多一次 add，且存在被拒的第二个 add 信号 bar
        adds = [f for f in fills2 if f["kind"] == "add"]
        assert adds, "敞口余量内首个 ADD 须放行"
        in_pos = False
        seg_adds = 0
        max_seg_adds = 0
        for f in fills2:
            if f["kind"] == "open":
                in_pos, seg_adds = True, 0
            elif f["kind"] == "add":
                seg_adds += 1
                max_seg_adds = max(max_seg_adds, seg_adds)
            elif f["kind"] in _FULL_EXIT:
                in_pos = False
        assert max_seg_adds == 1, "每段恰一次 add（次个超限拒）"
        # 被拒证据：同段内存在第二个 add 信号 bar（信号面直算）
        sig = dna_to_signal_set(dna_gate, h2.enhanced)
        first_add = adds[0]
        k = _bar_index(h2, first_add["bar_time"])
        later = sig.adds.iloc[k + 1:k + 4]
        assert later.any(), "次根邻段须存在第二个 add 信号（被敞口门拒）"
