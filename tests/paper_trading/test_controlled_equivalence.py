"""受控等价测试（rebuild-paper-trading 任务 8.1，design §8.4 分层边界）。

B0 基线对照：pct+无分批+无时间出场+long+单周期+杠杆 1+有 TP+参数显式
+fixed 档——纸盘执行器（PaperTaskExecutor.process_frames 历史回放注入
通道，任务 3.2 结构性前提）×回测引擎（BacktestEngine——run_backtest_
on_range 桥内部同一构造与调用形制；桥入口另以 metrics 级断言覆盖）。
同段数据（对照段=第 _WARMUP 根起，指标预热收敛后）同参数逐笔/逐 bar
对照。risk_budget 档同入等价层（design §8.4：定仓公式单源共用
risk_budget_qty，杠杆=1 场景两侧可等价）。

受控手段（等价层前提，逐项有断言或前提守护）：
- 平价合成数据（make_flat_ohlcv，open≡close≡high≡low）：纸盘信号类
  意图按执行 bar 开盘价成交、回测 vbt 按当根 close 成交——平价使两侧
  成交基准价重合；
- fee=slippage=0 主锚：vbt 百分比仓换算含资金预留除数（数量=
  percent×cash/(price×(1+fees)×(1+slippage))，vectorbt portfolio/nb.py
  buy_nb 语义），非零费滑下定仓数量带该除数的系统差（实测 0.15%=
  fee+slip）——等价层主锚归零费滑聚焦路径语义，费用滑移形制另以带
  费滑场景断言成交价与费用同侧同幅；
- 空 funding 索引：资金费两侧均不发生（纸盘结算点全挂起不扣款、回测
  lev=1 免扣）——资金费维度（纸盘真实数据任意杠杆均扣 vs 回测固定
  估算+lev≤1 免扣）属不等价层，不整曲线比对；
- SL/TP 距离（0.19/0.20）远大于行情波动（step=0.003）：即时类出场
  （纸盘按触发价、回测按当根 close 成交）不在对照段触发——即时类
  出场价形制差属不等价面，其数值锚由组 1 test_kernel_faithfulness
  的 SL/TP 触发价锚承载；
- 对照段前一根无 entry 信号（前提断言）：shift(1) 首行边界——回测
  warmup 末根信号会 shift 进对照段首根，纸盘不处理 warmup 段无此
  pending，此前提守住两侧首根对齐。

【缺陷在案·修复前本组恒红】逐 bar 权益对照按正确语义断言：纸盘权益
应=balance+占用保证金+未实现盈亏（逐仓合约总权益）。现状
Ledger.equity/AccountSnapshot.equity=balance+upnl 漏 margin 项——开仓
即权益跌去保证金（杠杆=1、position_size=0.5 场景开仓瞬间权益掉 50%）、
持仓段逐 bar 恒差 margin、报告层 total_return/max_drawdown 因此失真。
实测证据（fee=slip=0 主锚）：margin 重组曲线（balance+margin+upnl）与
回测 equity_curve 全曲线 120/120 bar 逐位一致（最大绝对差 0.0），
终值两侧同为 10149.066384。TestEquityCurveEquivalence 的逐 bar 断言
是缺陷在案证据，禁删断言或放宽容差迁就现状实现（修复方向：snapshot
持仓分支 equity=balance+position.margin+upnl）。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.backtest.engine import BacktestEngine
from core.data.storage import save_parquet
from core.features.indicators import compute_all_indicators
from core.paper_trading.runtime.executor import PaperTaskExecutor
from core.paper_trading.runtime.feed import FeedRegistry, FeedSubscription
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import paper
from core.strategy.executor import dna_to_signal_set
from tests.helpers.data_factory import make_dna, make_flat_ohlcv

# 合成数据基线（seed 固定——信号分布确定：对照段内 4 笔开平、末段空仓）
_N_BARS = 140
_WARMUP = 20
_SEED = 11
_INIT_CASH = 10_000.0
# B0 风控参数（SL/TP 距离远大于行情波动 step=0.003——即时类出场不触发）
_SL = 0.19
_TP = 0.20
_PS = 0.5
# risk_budget 档参数（杠杆=1 等价层同入；stop_loss 同取远距离）
_RF = 0.01
_FEE = 0.001
_SLIP = 0.0005


def _funding_frame(times: tuple[str, ...],
                   rate: float = 0.0001) -> pd.DataFrame:
    """funding 归档帧（沿 test_executor 形制；times 空=空索引）。"""
    idx = pd.DatetimeIndex(pd.to_datetime(list(times), utc=True),
                           name="timestamp")
    return pd.DataFrame({"funding_rate": [rate] * len(idx)}, index=idx)


def _b0_dna() -> "StrategyDNA":  # noqa: F821
    """B0 基线 DNA（参数显式；RSI 45/55 保证对照段有多笔开平）。"""
    return make_dna(entry_value=45, exit_value=55, stop_loss=_SL,
                    take_profit=_TP, position_size=_PS, leverage=1)


def _risk_budget_dna() -> "StrategyDNA":  # noqa: F821
    """risk_budget 档 DNA（pct 距离域；杠杆=1 场景两侧同 risk_budget_qty）。"""
    dna = _b0_dna()
    dna.risk_genes.position_mode = "risk_budget"
    dna.risk_genes.risk_fraction = _RF
    return dna


class _Harness:
    """受控等价装配：同段数据同参数跑两侧引擎并暴露对照面。

    纸盘侧=process_frames 全帧回放+start_after 对照段起点（warmup 段
    bar 不感知不计数——与回测桥切回 [start,end) 的 bar 集对齐）；
    回测侧=BacktestEngine.run(dna, bt_df)（桥内部同一构造+调用形制）。
    """

    def __init__(self, tmp_path: Path, *, dna=None, fee: float = 0.0,
                 slippage: float = 0.0, funding_times: tuple = (),
                 leverage: int = 1):
        self.dna = dna or _b0_dna()
        if leverage != 1:
            self.dna.risk_genes.leverage = leverage

        self.data_dir = tmp_path / "market"
        self.data_dir.mkdir()
        self.df = make_flat_ohlcv(n=_N_BARS, seed=_SEED)
        save_parquet(self.df, self.data_dir / "BTCUSDT_4h.parquet")
        self.enhanced = compute_all_indicators(self.df)

        self.db = tmp_path / "paper.db"
        init_db_ext(self.db)
        self.task_id = paper.create_task(
            self.db, strategy_id="strat-1",
            dna_snapshot_json=self.dna.to_json(),
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified",
            initial_cash=_INIT_CASH, leverage=leverage,
            fee=fee, slippage=slippage)

        self.registry = FeedRegistry(
            self.data_dir, fetcher=lambda **k: self.df,
            funding_refresher=lambda **k: _funding_frame(funding_times))
        self._dummy = FeedSubscription(on_frames=lambda df, idx: None)
        self.registry.acquire("BTCUSDT", "4h", self._dummy).refresh()

        row = paper.get_task(self.db, self.task_id)
        executor = PaperTaskExecutor(
            self.db, row, registry=self.registry,
            push=lambda tid, p: None, on_concluded=lambda tid, st: None,
            start_after=self.df.index[_WARMUP].isoformat())
        assert executor.prepare() is True, "装载须就绪"
        # 模拟拾取写 running（回放通道跳过 run()——迁移链不断：
        # pending→running→completed；CARD-06 后 append_bar 的 task_state
        # UPDATE 附 WHERE status='running'，直驱不置 running 会冻结
        # task_state 组——形制沿 test_executor 模拟拾取）
        paper.update_task(
            self.db, self.task_id, status="running",
            started_at="2026-10-16T00:00:00+00:00")
        executor.process_frames(self.enhanced, 0)

        # 回测侧：对照段=enhanced 切回（指标列带 warmup 记忆——桥形制）
        self.bt_df = self.enhanced.iloc[_WARMUP:]
        self.bt_result = BacktestEngine(
            init_cash=_INIT_CASH, fee=fee, slippage=slippage).run(
                self.dna, self.bt_df)

        # 信号全帧组装（两侧信号同源——纸盘 process_frames 与桥均按
        # dna_to_signal_set 全帧计算）
        self.sig = dna_to_signal_set(self.dna, self.enhanced)
        # shift(1) 首行边界前提：对照段前一根无 entry 信号（否则回测
        # warmup 末根信号 shift 进对照段首根而纸盘无此 pending）
        assert not bool(self.sig.entries.iloc[_WARMUP - 1])

    # ── 纸盘对照面 ──────────────────────────────────────────

    @property
    def fills(self) -> list[dict]:
        return paper.list_fills(self.db, self.task_id, limit=100_000)

    def opens(self) -> list[dict]:
        return [f for f in self.fills if f["kind"] == "open"]

    def closes(self) -> list[dict]:
        return [f for f in self.fills if f["kind"] == "close"]

    def equity_series(self) -> pd.Series:
        eqs = paper.list_equity_snapshots(self.db, self.task_id, limit=100_000)
        return pd.Series(
            [float(e["equity"]) for e in eqs],
            index=pd.DatetimeIndex(
                [pd.Timestamp(e["bar_time"]) for e in eqs]))

    def funding_records(self) -> list[dict]:
        return paper.list_funding_records(self.db, self.task_id, limit=100_000)

    # ── 共用定位 ────────────────────────────────────────────

    def index_loc(self, bar_time: str) -> int:
        """bar_time → 全帧行号（对照段定位锚）。"""
        ts = pd.Timestamp(bar_time)
        return int(self.df.index.get_loc(ts))

    def close_at(self, bar_time: str) -> float:
        return float(self.df["close"].iloc[self.index_loc(bar_time)])

    @property
    def start_iso(self) -> str:
        return self.df.index[_WARMUP].isoformat()

    @property
    def end_iso(self) -> str:
        return self.df.index[-1].isoformat()


class TestSignalTimingEquivalence:
    """等价层断言①：信号执行时序（shift(1) 对齐锚）。"""

    def test_fill_bars_match_backtest_trades(self, tmp_path):
        """逐笔成交 bar_time 两侧全等（开仓=Entry、平仓=Exit Timestamp）。"""
        h = _Harness(tmp_path)
        opens, closes = h.opens(), h.closes()
        trades = h.bt_result.trades_df
        assert trades is not None and len(trades) >= 2, "对照段须有多笔交易"
        assert len(opens) == len(trades)
        assert [pd.Timestamp(f["bar_time"]) for f in opens] == (
            list(trades["Entry Timestamp"]))
        assert len(closes) == len(trades)
        assert [pd.Timestamp(f["bar_time"]) for f in closes] == (
            list(trades["Exit Timestamp"]))

    def test_fill_bar_is_next_bar_of_signal(self, tmp_path):
        """成交 bar_time=信号 bar 次根（shift(1) 锚——成交 bar 的前一根
        有信号；两侧同锚，不在信号当根成交防前视）。"""
        h = _Harness(tmp_path)
        for f in h.opens():
            i = h.index_loc(f["bar_time"])
            assert i - 1 >= _WARMUP, "信号须在对照段内"
            assert bool(h.sig.entries.iloc[i - 1]), f
        for f in h.closes():
            i = h.index_loc(f["bar_time"])
            assert bool(h.sig.exits.iloc[i - 1]), f
        # 回测侧同锚（trades 逐笔前一根有信号）
        trades = h.bt_result.trades_df
        for ts in trades["Entry Timestamp"]:
            i = int(h.df.index.get_loc(ts))
            assert bool(h.sig.entries.iloc[i - 1])
        for ts in trades["Exit Timestamp"]:
            i = int(h.df.index.get_loc(ts))
            assert bool(h.sig.exits.iloc[i - 1])

    def test_trade_count_and_side_match(self, tmp_path):
        """笔数与方向两侧一致（出场类计数口径=平仓事件）。"""
        h = _Harness(tmp_path)
        trades = h.bt_result.trades_df
        assert int(h.bt_result.total_trades) == len(h.closes())
        task = paper.get_task(h.db, h.task_id)
        assert int(task["total_trades"]) == len(h.closes())
        assert set(f["side"] for f in h.opens()) == {"long"}
        assert set(trades["Direction"]) == {"Long"}


class TestPositionSizingEquivalence:
    """等价层断言②：仓位比例（权益口径；fee=slip=0 主锚——vbt 百分比
    换算的资金预留除数归零后两侧定仓式同构）。

    权益手算链独立承载（禁被测实现生成期望）：开仓时点权益从
    initial_cash 起按逐笔出场净盈亏推进（fee=slip=0 下净盈亏=数量×
    价差，价与量本身已由两侧对照锚背书）。
    """

    @staticmethod
    def _equity_chain(h: "_Harness") -> list[float]:
        """逐笔开仓时点权益手算链（空仓时点权益=init+已实现净盈亏）。"""
        opens = {f["bar_time"]: f for f in h.opens()}
        closes = {f["bar_time"]: f for f in h.closes()}
        equity = _INIT_CASH
        chain: list[float] = []
        pending_open = None
        for f in sorted(h.fills, key=lambda x: x["bar_time"]):
            if f["kind"] == "open":
                pending_open = f
                chain.append(equity)
            elif f["kind"] == "close" and pending_open is not None:
                equity += (f["price"] - pending_open["price"]) * (
                    f["quantity"])
                pending_open = None
        assert len(chain) == len(opens)
        assert len(closes) >= 1
        return chain

    def test_fixed_qty_and_margin_equal(self, tmp_path):
        """fixed 档：两侧入场数量等值+逐笔保证金=当时权益×position_size
        （杠杆=1 下保证金=数量×成交价=权益口径动用）。"""
        h = _Harness(tmp_path)
        trades = h.bt_result.trades_df
        opens = h.opens()
        chain = self._equity_chain(h)
        assert len(opens) >= 2, "须覆盖权益浮动后的第二笔"
        for k, f in enumerate(opens):
            # 两侧数量等值（装配一致性：margin×leverage/price 与 percent
            # ×cash/price 同式同 double 除法——rel=1e-12 仅留位浮点余量）
            assert f["quantity"] == pytest.approx(
                float(trades["Size"].iloc[k]), rel=1e-12), (k, f)
            margin = f["quantity"] * f["price"]  # leverage=1
            assert margin == pytest.approx(
                chain[k] * _PS, rel=1e-12), (k, f)

    def test_risk_budget_qty_equal_and_identity(self, tmp_path):
        """risk_budget 档（杠杆=1 同入等价层）：两侧数量等值（装配一致
        性——定仓公式单源 risk_budget_qty、两侧传参一致）+恒等式手算锚
        独立承载数值真值（数量×止损距离=当时权益×risk_fraction，未触
        cap——名义远低于权益×min(1,1)）。"""
        h = _Harness(tmp_path, dna=_risk_budget_dna())
        trades = h.bt_result.trades_df
        opens = h.opens()
        chain = self._equity_chain(h)
        assert len(opens) >= 2
        for k, f in enumerate(opens):
            assert f["quantity"] == pytest.approx(
                float(trades["Size"].iloc[k]), rel=1e-12), (k, f)
            stop_dist = f["price"] * _SL  # pct 距离域=价×比例
            assert f["quantity"] * stop_dist == pytest.approx(
                chain[k] * _RF, rel=1e-12), (k, f)


class TestFeeSlippageFormEquivalence:
    """等价层断言③：费用滑移形制（vbt 不利偏移同侧同幅；带费滑场景
    fee=0.001/slip=0.0005——数量含 vbt 资金预留除数系统差，本断言面
    为成交价与费用的形制，非数量）。"""

    def test_prices_adverse_shift_same_side_same_magnitude(self, tmp_path):
        """多头买入=基准价×(1+slip)、卖出=×(1−slip)（不利偏移）——两侧
        同式同幅，成交价逐笔等值（平价数据下基准价=当根 close）。"""
        h = _Harness(tmp_path, fee=_FEE, slippage=_SLIP)
        trades = h.bt_result.trades_df
        for k, f in enumerate(h.opens()):
            base = h.close_at(f["bar_time"])
            expected = base * (1.0 + _SLIP)  # 多头买入不利上浮
            assert f["price"] == pytest.approx(expected, rel=1e-12), (k, f)
            assert f["price"] == pytest.approx(
                float(trades["Avg Entry Price"].iloc[k]), rel=1e-12), (k, f)
        for k, f in enumerate(h.closes()):
            base = h.close_at(f["bar_time"])
            expected = base * (1.0 - _SLIP)  # 多头卖出不利下压
            assert f["price"] == pytest.approx(expected, rel=1e-12), (k, f)
            assert f["price"] == pytest.approx(
                float(trades["Avg Exit Price"].iloc[k]), rel=1e-12), (k, f)

    def test_fees_are_notional_times_rate_both_engines(self, tmp_path):
        """费用=成交名义×费率——两侧各自同式（vbt fees 计费基数=含滑价
        名义 adj_price×自身 size，与纸盘 fee_rate×quantity×price 同
        形制；两侧数量本身含 vbt 资金预留除数差，费用对照各用自身数量
        ——B0 全平，vbt 出场数量=入场 Size）。"""
        h = _Harness(tmp_path, fee=_FEE, slippage=_SLIP)
        trades = h.bt_result.trades_df
        for k, f in enumerate(h.opens()):
            assert f["fee"] == pytest.approx(
                _FEE * f["quantity"] * f["price"], rel=1e-12), (k, f)
            size = float(trades["Size"].iloc[k])
            entry = float(trades["Avg Entry Price"].iloc[k])
            assert float(trades["Entry Fees"].iloc[k]) == pytest.approx(
                _FEE * size * entry, rel=1e-12), (k, f)
        for k, f in enumerate(h.closes()):
            assert f["fee"] == pytest.approx(
                _FEE * f["quantity"] * f["price"], rel=1e-12), (k, f)
            size = float(trades["Size"].iloc[k])  # 全平：出场量=入场量
            exit_price = float(trades["Avg Exit Price"].iloc[k])
            assert float(trades["Exit Fees"].iloc[k]) == pytest.approx(
                _FEE * size * exit_price, rel=1e-12), (k, f)


class TestEquityCurveEquivalence:
    """等价层断言④：逐 bar 权益数值（fee=slip=0 主锚；容差依据见
    模块 docstring——两侧为独立浮点记账链，IEE754 double 逐 bar 加减
    的相对误差量级 ~1e-13，rel=1e-9 留两个数量级余量；实测修复 margin
    项后全曲线逐位差 0.0）。

    【缺陷在案】现状 Ledger.equity=balance+upnl 漏 margin 项，
    test_equity_curve_bar_by_bar 恒红=缺陷证据（见模块 docstring），
    禁删断言或放宽容差迁就实现。
    """

    def test_equity_curve_bar_by_bar(self, tmp_path):
        """逐 bar 权益两侧数值对照（bar 集对齐+逐点 approx）。"""
        h = _Harness(tmp_path)
        paper_eq = h.equity_series()
        bt_eq = h.bt_result.equity_curve
        assert len(paper_eq) == len(bt_eq) == _N_BARS - _WARMUP
        assert list(paper_eq.index) == list(bt_eq.index)
        for ts in paper_eq.index:
            assert float(paper_eq[ts]) == pytest.approx(
                float(bt_eq[ts]), rel=1e-9, abs=1e-6), ts

    def test_final_value_matches_bridge_metrics(self, tmp_path):
        """终值与桥入口 metrics 级对照（run_backtest_on_range——组 4
        偏差对照层同一入口；末段空仓 balance=权益，不受 margin 缺陷
        影响）。"""
        from core.analysis.backtest_bridge import run_backtest_on_range

        h = _Harness(tmp_path)
        metrics, _returns, warmup, bars = run_backtest_on_range(
            h.db, h.data_dir, h.dna, "BTCUSDT", "4h",
            start=h.start_iso, end=h.end_iso, warmup_bars=_WARMUP,
            init_cash=_INIT_CASH, fee=0.0, slippage=0.0)
        assert warmup == _WARMUP
        assert bars == _N_BARS - _WARMUP
        # 桥入口与受控等价回测侧同值（桥内部同一引擎构造+调用）
        assert metrics["total_return"] == pytest.approx(
            h.bt_result.total_return, rel=1e-12)
        # 纸盘终值（末段空仓：最后成交=close、权益=balance）
        assert h.fills[-1]["kind"] == "close"
        assert float(h.equity_series().iloc[-1]) == pytest.approx(
            _INIT_CASH * (1.0 + h.bt_result.total_return), rel=1e-9)


class TestNonEquivalenceLayerDeclaration:
    """不等价层显式规避（design §8.4）——只实证分叉与声明规避面，
    不重复锚实现。

    规避面（不整曲线比对的维度与两独立锚的承载位置）：
    - 清算：纸盘=仓位口径逐 bar 分层判定（market_model mmr 档+触发价
      成交）vs 回测=无杠杆曲线等效线（maintenance_threshold 换算形制）
      ——数值锚由组 1 tests/paper_trading/test_ledger.py
      TestLiquidationAnchors 承载（主锚反向 9.6386%+边界格点 0.904 不
      触发/0.903 触发+第二锚 33.07%+空头对称式）；
    - 资金费：纸盘=真实 parquet 逐结算点结算（与杠杆无关，对质六裁决
      ——任意杠杆持仓跨 8h 结算点均扣）vs 回测=固定费率估算（RATE_
      PER_8H 按 bar 折算）+lev≤1 免扣——对账锚由组 3 tests/
      paper_trading/test_funding_backfill.py（挂起补记逐点对账）与
      tests/paper_trading/test_executor.py TestFundingSettlement（金额
      =数量×结算价×费率×方向符号锚）承载。
    """

    def test_lev1_funding_point_diverges_both_engines(self, tmp_path):
        """资金费差异面实证（对质六裁决扩面）：杠杆=1 持仓跨结算点时
        纸盘按真实费率扣（balance 降）、回测 lev≤1 免扣（funding=0）
        ——两侧权益曲线自结算 bar 起分叉。等价层以空 funding 索引规避
        （结算点全挂起不扣款）。"""
        # 持仓期 2024-01-05 16:00→01-08 04:00 内取一结算点
        h = _Harness(tmp_path, funding_times=("2024-01-06 00:00:00",))
        settled = [r for r in h.funding_records()
                   if r["status"] == "settled"]
        assert settled, "纸盘杠杆=1 持仓跨结算点须扣真实资金费"
        assert settled[0]["rate"] == 0.0001
        assert h.bt_result.total_funding_cost == 0.0, "回测 lev=1 免扣"
        paper_eq = h.equity_series()
        bt_eq = h.bt_result.equity_curve
        assert (paper_eq - bt_eq).abs().max() > 0.5, "资金费点分叉实证"

    def test_lev_gt1_curves_diverge_no_curve_comparison(self, tmp_path):
        """杠杆>1 场景两侧曲线分叉实证（清算形制/资金费/杠杆放大后处理
        三维口径差叠加）——因此不整曲线比对，等价层限于杠杆=1；清算与
        资金费两维走类 docstring 所列独立锚。"""
        h = _Harness(tmp_path, leverage=3)
        paper_eq = h.equity_series()
        bt_eq = h.bt_result.equity_curve
        assert len(paper_eq) == len(bt_eq)
        assert (paper_eq - bt_eq).abs().max() > 1.0, "杠杆>1 分叉实证"
