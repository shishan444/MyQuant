"""基因级标注形制装载等价（fix-replay-gap-alignment T1 组——需求 2.1）。

缺陷 A 形制：无 layers DNA 的信号基因带 timeframe 标注（生产登记的
真实 DNA 形制），装载层轴清单来自顶层 timeframes property（该形制
退化为 [执行周期] 单元素）——装载能力窄于求值能力。本组验证换源后
装载轴集与求值轴集一致（T1-a/b/c）、缺档 fail-loud 前移装载链
（T1-d）、低周期维持现契约拒绝（T1-e）、单层与层级两类老形制回归
不变（T1-h；全量回归=T1-f 归 tests/replay 全绿）。

红基线：E2 探针代证（修复前 T1-a/b/c/d 红——装载缺轴间接拒/
REPLAY_PREPARE_FAILED 错码）；T1-e/T1-h 为现契约回归锚（修复前后
皆绿——T1-h 双时点基准对账，先于换源动码实测存档）。
"""
from __future__ import annotations

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.validation_service import gene_tf_axis_refs
from core.data.storage import save_parquet
from core.persistence.repository import paper
from core.persistence.repository.strategy import save_strategy
from core.replay import ReplayRunner
from core.replay.feeder import ReplayFeeder
from core.replay.runner import ReplayError
from tests.helpers.data_factory import make_ohlcv
from tests.replay._env import (
    ReplayEnv,
    make_gene_tf_dna,
    make_layer_equiv_dna,
    make_single_layer_dna,
)


def _gene_tf_env(tmp_path, *, dna=None, with_aux: bool = True):
    """1h 主轴+4h 辅轴数据环境（基因级标注形制装载用例的公共装配）。

    ReplayEnv 落盘文件名恒为 4h（freq 只改合成数据间隔）——本组执行
    周期为 1h，两轴文件按轴集显式落盘：主轴 1h（n=1200）+辅轴 4h
    （n=320，seed=11 独立序列）；with_aux=False 留 4h 缺席（T1-d）。"""
    env = ReplayEnv(
        tmp_path, dna=dna or make_gene_tf_dna(), n_bars=1200, freq="1h")
    (env.data_dir / "BTCUSDT_4h.parquet").unlink()  # 1h 间隔数据不得伪装 4h 轴
    save_parquet(env.df, env.data_dir / "BTCUSDT_1h.parquet")
    if with_aux:
        save_parquet(make_ohlcv(n=320, freq="4h", seed=11, base_price=100.0),
                     env.data_dir / "BTCUSDT_4h.parquet")
    return env


def _save_exec_strategy(env, strategy_id: str, dna) -> str:
    """按 DNA 执行周期另存策略行（ReplayEnv 缺省行 timeframe 恒 4h，
    本组执行周期为 1h——行元数据以 DNA 为准另存）。"""
    save_strategy(
        env.db, strategy_id=strategy_id, dna_json=dna.to_json(),
        symbol="BTCUSDT", timeframe=dna.execution_genes.timeframe,
        status="draft")
    return strategy_id


def make_deterministic_ramp_aux(n: int = 320, flat_bars: int = 272):
    """确定性 4h 辅轴（BT-01 补强）：前 flat_bars 根严格锯齿交替
    （+0.1%/−0.1% 等幅——RSI 稳定≈50 零触发），随后 +2%/根连续拉升
    （拉升首根 RSI 上穿 60——触发点=第 flat_bars+1 根，确定无随机）。

    seed=11 随机形制实测首个触发 bar 早于回放区间起点约 37 天（投影
    状态窗预开，时点断言对前视变异无判别力——BT-01）；本工厂使触发
    bar 确定性落在回放区间内（head_bars=300 形制下=区间中后段）。
    """
    import numpy as np

    rng = np.random.default_rng(3)
    zig = np.where(np.arange(flat_bars) % 2 == 0, 1.001, 1 / 1.001)
    flat = 100.0 * np.cumprod(zig)
    ramp = flat[-1] * np.cumprod(np.full(n - flat_bars, 1.02))
    close = np.concatenate([flat, ramp])
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame(
        {"open": close * (1 + rng.standard_normal(n) * 0.0002),
         "high": close * 1.001, "low": close * 0.999, "close": close,
         "volume": np.full(n, 1000.0)}, index=idx)


class TestT1aGeneTfReplayRun:
    """T1-a：基因级标注回放装载运行+投影时点断言。"""

    def test_gene_tf_replay_runs(self, tmp_path):
        env = _gene_tf_env(tmp_path)
        sid = _save_exec_strategy(env, "st_gene_tf_a", env.dna)
        start, end = env.range(head_bars=300)
        res = ReplayRunner(env.data_dir).run(env.db, sid, start, end,
                                             task_id="gtfa")
        assert res.status == "completed"
        assert res.bars_processed == 300, (
            f"区间主 bar 全驱动（实测 {res.bars_processed}/300）")
        fills = paper.list_fills(env.db, "gtfa", limit=100)
        assert fills, "恒真构造下有成交（对账实质面）"
        task = paper.get_task(env.db, "gtfa")
        assert task["origin"] == "replay"

    def test_projection_timing_close_anchor(self, tmp_path):
        """投影时点语义：4h 条件触发的成交时点落在 4h 收盘所在 1h bar
        （掩码投影状态窗——首根可见收盘即 True，与 pulse 脉冲在对齐
        网格下首触发同 bar）。判别力（BT-01 补强后与实测一致）：辅轴
        为 make_deterministic_ramp_aux 确定性形制——锯齿横盘段零触发、
        第 273 根起拉升触发，触发 bar 落在回放区间中后段（实测区间
        第 193/300 根）；前视退化（投影只提前 4h 开窗）下首笔成交
        相应提前、first_fill_bar >= expect_from 必红。"""
        from tests.helpers.data_factory import make_signal_gene
        from core.strategy.dna import SignalRole

        entry_hi = make_signal_gene(
            indicator="RSI", role=SignalRole.ENTRY_TRIGGER,
            condition_type="gt", condition_value=60, timeframe="4h")
        entry_base = make_signal_gene(
            indicator="RSI", role=SignalRole.ENTRY_TRIGGER,
            condition_type="gt", condition_value=0)
        gene_exit = make_signal_gene(
            indicator="RSI", role=SignalRole.EXIT_TRIGGER,
            condition_type="gt", condition_value=200)
        from core.strategy.dna import (
            ExecutionGenes, LogicGenes, RiskGenes, StrategyDNA)
        dna = StrategyDNA(
            signal_genes=[entry_hi, entry_base, gene_exit],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
            execution_genes=ExecutionGenes(
                timeframe="1h", symbol="BTCUSDT"),
            risk_genes=RiskGenes(
                stop_loss=0.05, take_profit=0.2, position_size=0.1,
                leverage=2, direction="long"))
        env = _gene_tf_env(tmp_path, dna=dna)
        # BT-01：确定性辅轴覆盖（seed=11 随机形制触发点在区间外——
        # 投影窗预开致时点断言无判别力；见 make_deterministic_ramp_aux）
        save_parquet(make_deterministic_ramp_aux(),
                     env.data_dir / "BTCUSDT_4h.parquet")
        sid = _save_exec_strategy(env, "st_gene_tf_proj", dna)
        start, end = env.range(head_bars=300)
        res = ReplayRunner(env.data_dir).run(env.db, sid, start, end,
                                             task_id="gtfproj")
        assert res.status == "completed"
        fills = paper.list_fills(env.db, "gtfproj", limit=100)
        assert fills, "4h RSI>60 于确定性拉升段触发（实质面）"

        # 期望投影时点（独立于 feeder 段实现，契约手写；指标值取生产
        # 实现同源——断言对象是投影契约而非指标口径）：4h 轴 RSI(14)
        # 首个 >60 bar 的收盘时刻 → 首个开盘时刻 ≥ 该收盘的 1h bar
        aux_df = pd.read_parquet(env.data_dir / "BTCUSDT_4h.parquet")
        from core.features.indicators import compute_all_indicators

        rsi = compute_all_indicators(aux_df)["rsi_14"]
        hot = aux_df.index[rsi.fillna(0) > 60]
        assert len(hot) > 0
        # 触发点在回放区间内（判别力前提——触发在区间外则投影窗预开、
        # 时点断言恒过；BT-01 补强锚）
        assert pd.Timestamp(start) < hot[0] < pd.Timestamp(end), (
            f"首个触发 bar {hot[0]} 须落在回放区间 ({start}, {end}) 内"
            "——否则时点断言无判别力（BT-01）")
        main_idx = pd.date_range(
            start=pd.Timestamp(start), end=pd.Timestamp(end),
            freq="1h", tz="UTC")
        first_fill_bar = pd.Timestamp(fills[0]["bar_time"])
        # 状态窗起点=首个开盘 ≥ 首个触发 4h bar 收盘；且不早于该 bar
        # （前视防线——投影只认已收盘高周期 K 线）
        expect_from = hot[0] + pd.Timedelta(hours=4)
        assert first_fill_bar >= expect_from, (
            f"成交时点 {first_fill_bar} 早于 4h 收盘投影可见点 "
            f"{expect_from}（前视）")
        assert first_fill_bar in main_idx, "成交时点落在区间内 1h bar 上"


class TestT1bGeneTfPaperMount:
    """T1-b：基因级标注纸盘装载（prepare 通过+三处分支进入证据面）。"""

    def test_prepare_passes_and_aux_axis_mounted(self, tmp_path):
        from core.analysis.warmup_loader import load_with_warmup
        from core.analysis.validation_service import dna_indicator_names
        from core.features.indicators import compute_all_indicators
        from core.paper_trading.runtime.executor import PaperTaskExecutor
        from core.paper_trading.runtime.feed import FeedRegistry

        env = _gene_tf_env(tmp_path)
        paper.create_task(
            env.db, strategy_id=env.strategy_id,
            dna_snapshot_json=env.dna.to_json(),
            symbol="BTCUSDT", timeframe="1h",
            research_status_snapshot="unverified",
            fee=0.001, slippage=0.0005, task_id="gtfb")
        registry = FeedRegistry(
            env.data_dir,
            rest_fetcher=lambda *a, **k: pd.DataFrame(),
            funding_refresher=lambda **k: pd.DataFrame())
        ex = PaperTaskExecutor(env.db, paper.get_task(env.db, "gtfb"),
                               registry=registry)
        # 装载校验通过（现状=indicators_unavailable 拒——装载缺轴下
        # 信号试算抛 RESEARCH_DATA_INSUFFICIENT）
        assert ex.prepare(), "基因级标注形制 prepare 应通过"
        # 运行期三面证据：订阅面（subscribe_feeds L829 循环）含 4h 轴
        ex.subscribe_feeds()
        assert set(ex._aux_subs) == {"4h"}, (
            f"辅轴订阅面应恰为 4h（实测 {set(ex._aux_subs)}）")
        # 快照组装面（_assemble_mtf_frames L929 循环）含 4h 轴
        names = dna_indicator_names(env.dna)
        main_df, _ = load_with_warmup(
            env.data_dir, "BTCUSDT", "1h", *env.range(head_bars=300),
            names)
        aux_df, _ = load_with_warmup(
            env.data_dir, "BTCUSDT", "4h", *env.range(head_bars=300),
            ["RSI"])
        ex._aux_frames["4h"] = compute_all_indicators(aux_df)
        frames = ex._assemble_mtf_frames(compute_all_indicators(main_df))
        assert frames is not None and "4h" in frames, (
            "多周期帧组装应含 4h 轴（装载-订阅-快照三面轴集一致）")

    def test_prepare_rejected_names_missing_aux(self, tmp_path):
        """装载校验拒绝面：辅轴 parquet 缺席时 mtf_data_missing 报文
        点名缺失轴（拒绝面 aux 清单换源单点——T1-i L624 变异锚）。"""
        from core.paper_trading.runtime.executor import PaperTaskExecutor
        from core.paper_trading.runtime.feed import FeedRegistry

        env = _gene_tf_env(tmp_path, with_aux=False)  # 4h parquet 缺席
        paper.create_task(
            env.db, strategy_id=env.strategy_id,
            dna_snapshot_json=env.dna.to_json(),
            symbol="BTCUSDT", timeframe="1h",
            research_status_snapshot="unverified",
            fee=0.001, slippage=0.0005, task_id="gtfb2")
        registry = FeedRegistry(
            env.data_dir,
            rest_fetcher=lambda *a, **k: pd.DataFrame(),
            funding_refresher=lambda **k: pd.DataFrame())
        ex = PaperTaskExecutor(env.db, paper.get_task(env.db, "gtfb2"),
                               registry=registry)
        assert not ex.prepare(), "辅轴缺档装载校验应拒绝"
        row = paper.get_task(env.db, "gtfb2")
        reason = row.get("error_detail") or ""
        assert "mtf_data_missing" in reason, f"拒绝面语义（{reason}）"
        assert "mtf_data_missing: 4h" in reason, (
            f"缺失轴点名（拒绝面 aux 清单段来自轴集单点——{reason}；"
            f"锚缺失清单段而非「所需周期」并集段——后者退化不判别）")


class TestT1cFormEquivalence:
    """T1-c：两形制装载等价对账（锚 1 口径）。

    判别力边界：两路共享执行核心，本对账只证装载差异面无漂移；4h 侧
    投影语义两路不同（层路 pulse 脉冲/基因路掩码状态窗），恒真条件+
    对齐网格下首触发同 bar——逐位一致断言仅在该条件形制下成立。"""

    def test_layer_vs_gene_form_identical(self, tmp_path):
        env = _gene_tf_env(tmp_path)
        sid_gene = _save_exec_strategy(env, "st_eq_gene",
                                       make_gene_tf_dna())
        sid_layer = _save_exec_strategy(env, "st_eq_layer",
                                        make_layer_equiv_dna())
        start, end = env.range(head_bars=300)
        rr = ReplayRunner(env.data_dir)
        res_gene = rr.run(env.db, sid_gene, start, end, task_id="eqg")
        res_layer = rr.run(env.db, sid_layer, start, end, task_id="eql")
        f_gene = paper.list_fills(env.db, "eqg", limit=100)
        f_layer = paper.list_fills(env.db, "eql", limit=100)
        assert f_gene, "基因级形式有成交（对账实质面）"
        assert f_layer, "层级形式有成交（对账实质面）"
        assert [f["bar_time"] for f in f_gene] == \
            [f["bar_time"] for f in f_layer], "成交时点序列逐位一致"
        assert [f["kind"] for f in f_gene] == \
            [f["kind"] for f in f_layer]
        for a, b in zip(f_gene, f_layer):
            assert a["quantity"] == pytest.approx(b["quantity"])
            assert a["price"] == pytest.approx(b["price"])
        assert res_gene.total_trades == res_layer.total_trades
        assert res_gene.final_equity == pytest.approx(res_layer.final_equity)


class TestT1dAuxMissingFailLoud:
    """T1-d：高周期轴缺失显式拒绝（拒绝点前移装载链）。"""

    def test_missing_4h_rejected_in_load_chain(self, tmp_path):
        env = _gene_tf_env(tmp_path, with_aux=False)  # 4h parquet 缺席
        sid = _save_exec_strategy(env, "st_gene_tf_d", env.dna)
        start, end = env.range(head_bars=300)
        with pytest.raises(ReplayError) as ei:
            ReplayRunner(env.data_dir).run(env.db, sid, start, end,
                                           task_id="gtfd")
        # 拒绝码=RESEARCH_DATA_INSUFFICIENT（现状=REPLAY_PREPARE_FAILED
        # 间接拒——换锚断言）；拒绝点在装载链=任务行不建
        assert ei.value.code == "RESEARCH_DATA_INSUFFICIENT", (
            f"装载链显式拒绝（实测 {ei.value.code}）")
        rows = [r for r in paper.list_tasks(env.db, limit=50)
                if r.get("origin") == "replay"]
        assert rows == [], "拒绝点前移装载链——行不建"

    def test_feeder_load_raises_research_error(self, tmp_path):
        """装载层单元面：feeder.load 直接抛 ResearchError（拒绝点位于
        装载链而非 prepare 段的单元证据）。"""
        from core.analysis.errors import ResearchError

        env = _gene_tf_env(tmp_path, with_aux=False)
        feeder = ReplayFeeder(
            env.data_dir, env.db, env.dna, "BTCUSDT", "1h",
            *env.range(head_bars=300))
        with pytest.raises(ResearchError) as ei:
            feeder.load()
        assert ei.value.code == "RESEARCH_DATA_INSUFFICIENT"


class TestT1eLowerTfRejected:
    """T1-e：基因级低周期维持现契约拒绝（validate_dna 值域）。"""

    def test_lower_gene_tf_rejected(self, tmp_path):
        env = _gene_tf_env(
            tmp_path, dna=make_gene_tf_dna(gene_tf="15m"))
        sid = _save_exec_strategy(env, "st_gene_tf_e", env.dna)
        start, end = env.range(head_bars=300)
        with pytest.raises(ReplayError) as ei:
            ReplayRunner(env.data_dir).run(env.db, sid, start, end,
                                           task_id="gtfe")
        assert ei.value.code == "INVALID_DNA"
        assert "must be >= execution" in (ei.value.detail or ""), (
            f"validate_dna 基因级周期值域拒绝（15m < 1h）——detail: "
            f"{ei.value.detail}")
        rows = [r for r in paper.list_tasks(env.db, limit=50)
                if r.get("origin") == "replay"]
        assert rows == [], "载入即拒——行不建"


class TestT1hSingleLayerRouting:
    """T1-h：单层 layers 分支路由回归（双时点基准对账——现状基准先于
    换源动码实测，扩门后同形制对账不变）。

    单层 layers=[执行周期]：is_mtf=True 而 required_aux_timeframes=
    空集。或语义扩门纯增量（门恒真保持 MTF 分支路由）；纯判空换源
    变异（T1-g 变异 B）会使本用例红——路由漂移即判别力。"""

    def test_single_layer_keeps_mtf_branch(self, tmp_path):
        from core.analysis.warmup_loader import load_with_warmup
        from core.analysis.validation_service import dna_indicator_names
        from core.features.indicators import compute_all_indicators
        from core.paper_trading.runtime.executor import PaperTaskExecutor
        from core.paper_trading.runtime.feed import FeedRegistry
        from tests.helpers.data_factory import make_dna

        env = ReplayEnv(tmp_path, dna=make_single_layer_dna(), n_bars=600)
        paper.create_task(
            env.db, strategy_id=env.strategy_id,
            dna_snapshot_json=env.dna.to_json(),
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified",
            fee=0.001, slippage=0.0005, task_id="slh")
        registry = FeedRegistry(
            env.data_dir,
            rest_fetcher=lambda *a, **k: pd.DataFrame(),
            funding_refresher=lambda **k: pd.DataFrame())
        ex = PaperTaskExecutor(env.db, paper.get_task(env.db, "slh"),
                               registry=registry)
        assert ex.prepare(), "单层形制 prepare 通过（现状基准）"
        names = dna_indicator_names(env.dna)
        main_df, _ = load_with_warmup(
            env.data_dir, "BTCUSDT", "4h", *env.range(head_bars=300),
            names)
        enhanced = compute_all_indicators(main_df)
        frames = ex._assemble_mtf_frames(enhanced)
        # MTF 分支路由证据：返回 dict（仅主帧键）而非 None（单周期
        # 分支返回 None——下方对照组）
        assert frames is not None, (
            "单层层级形制应走 MTF 组装分支（扩门后路由不变——现状"
            "基准同款）")
        assert set(frames) == {"4h"}, (
            f"单层轴集恰为执行周期（实测 {set(frames)}）")

        # 对照组：纯单周期 DNA（无 layers 无基因标注）走单周期分支
        (tmp_path / "pure").mkdir()
        env2 = ReplayEnv(tmp_path / "pure", dna=make_dna(), n_bars=600)
        paper.create_task(
            env2.db, strategy_id=env2.strategy_id,
            dna_snapshot_json=env2.dna.to_json(),
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified",
            fee=0.001, slippage=0.0005, task_id="pure")
        ex2 = PaperTaskExecutor(env2.db, paper.get_task(env2.db, "pure"),
                                registry=FeedRegistry(
                                    env2.data_dir,
                                    rest_fetcher=lambda *a, **k: pd.DataFrame(),
                                    funding_refresher=lambda **k: pd.DataFrame()))
        assert ex2.prepare()
        assert ex2._assemble_mtf_frames(enhanced) is None, (
            "纯单周期形制走单周期分支（对照锚）")

    def test_axis_collection_single_point(self, tmp_path):
        """轴集单点语义单元面：required_aux_timeframes 三形制取值。"""
        from core.analysis.validation_service import (
            required_aux_timeframes)

        gene_dna = make_gene_tf_dna()
        assert required_aux_timeframes(gene_dna) == ["4h"], (
            "基因级标注形制轴集含基因轴")
        assert gene_dna.is_mtf is False
        layer_dna = make_layer_equiv_dna()
        # 层级形制：dna.timeframes=[1h,4h] 原序保层级遍历序
        assert list(layer_dna.timeframes) == ["1h", "4h"]
        assert required_aux_timeframes(layer_dna) == ["4h"]
        single = make_single_layer_dna()
        assert required_aux_timeframes(single) == [], (
            "单层形制轴需求空集（is_mtf=True——或语义扩门的回归陷阱）")
        assert single.is_mtf is True
        # 收集单点一致性：基因轴引用键集 ⊆ 轴集单点输出
        assert set(gene_tf_axis_refs(gene_dna)) <= set(
            required_aux_timeframes(gene_dna))
