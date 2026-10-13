"""信号源实质判定测试（fix-research-c4-signal-source-substance · C4.3）。

缺陷五（fr_1aee6a6e7e38）：纯插件进场策略被静默作废——组合层的
「有无规则源」判定按 signal_sources 声明（type=rule 字样）而非基因
角色实质，signal_genes 仅 exit_trigger+公式进场源的形制
（st_f86b4295c58b）被误判「有规则源」，空 rule 基因的全 False 基线
把纯插件进场作废成永不入场（0 笔交易静默）。

锚组：
- A7-①：纯插件进场复活——entries 触发数与插件侧统计锚精确一致；
- A7-②：混合形制回归不变（规则进场+公式源过滤）；
- guard-only：仅 ENTRY_GUARD 进场基因时判「有规则进场侧」，guard
  掩码语义不被单位元基线静默丢弃；
- caplog 三件：固定前缀/插件列名/警告不入返回体；
- 两边界：真实零触发（同告警=正确契约）/正常混合态负向无警告；
- 退化形态：无 plugin_sources 且无 composition 走原路径零涉及。

tester 特征网补充（team3 审查轮）——已有 8 条 A7-① 构造的
signal_sources 不含 type=rule 条目，修复前判定同样走单位元（main
实测 4 failed/4 passed，该用例属 passed 侧）——未锚住「声明面判定
误判」的切换点。缺陷五完整形制是翻译器产物（hypothesis_to_strategy
genes 非空即写 rule 条目，哪怕基因只有出场角色），补充锚组：
- 缺陷形制完备：sources 含 type=rule 条目+signal_genes 无进场角色
  （九种非进场角色参数化+零基因）→ 仍走单位元复活；
- 回归不变 β 形态：rule 条目+ENTRY_TRIGGER 基因——修复前后同值
  （main 实测亦绿，锚「判定改动不动混合行为」）；
- 链路级（run_backtest_on_range）：α 形态回测 0 笔→非零笔（上界=
  区间插件触发数）；混合形制过滤双向收敛（≤纯规则、≤纯插件对照）；
  guard 掩码经引擎端到端传递（False 拦截 0 笔/True 对照非零）；
  哨兵经装载-组装-回测全流程 caplog 可见；
- 哨兵补强：消息含基因摘要（indicator+role 值）；纯插件正常复活
  形态负向无警告（哨兵条件枚举的第四格补全）。

期望值独立手算（插件侧掩码向量化重算），不用被测实现生成。
"""

from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.plugin_service import register_formula_plugin
from core.analysis.signal_assembly import assemble_signal_set
from core.analysis.backtest_bridge import run_backtest_on_range
from core.persistence.db_ext import init_db_ext
from core.analysis.executor import ensure_research_tables
from core.strategy.dna import (
    ExecutionGenes,
    SignalGene,
    SignalRole,
    StrategyDNA,
)
from tests.helpers.data_factory import make_ohlcv

PLUGIN_EXPR = "(close - shift(close, 3)) / shift(close, 3)"


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "q.db"
    init_db_ext(p)
    ensure_research_tables(p)
    register_formula_plugin(p, name="dip", expression=PLUGIN_EXPR)
    return p


@pytest.fixture()
def df() -> pd.DataFrame:
    return make_ohlcv(n=300, freq="1h", start="2024-01-01", seed=7)


def _plugin_mask(df) -> pd.Series:
    """插件侧掩码独立重算（期望值来源——不用被测实现）。"""
    return ((df["close"] - df["close"].shift(3)) / df["close"].shift(3)) < -0.01


def _dna(genes, sources=None, comp=None):
    return StrategyDNA(
        signal_genes=genes,
        signal_sources=sources if sources is not None else [
            {"type": "formula", "ref": "fx_dip",
             "threshold": -0.01, "operator": "<"}],
        composition=comp if comp is not None else {"mode": "logic", "op": "AND"},
    )


def _exit_gene() -> SignalGene:
    """THEN 出场基因（恒不触发——不干扰进场断言；注意 dna_to_signal_set
    会剔除「进场位∩出场位」（exit 优先），exit 恒 True 会吞掉全部进场）。"""
    return SignalGene(indicator="EMA", params={"period": 10},
                      role=SignalRole.EXIT_TRIGGER, field_name="ema",
                      condition={"type": "gt", "threshold": 1e12})


class TestA7PurePluginRevival:
    """A7-①：纯插件进场复活（st_f86b4295c58b 缺陷形制）。"""

    def test_pure_plugin_entries_match_stat_anchor(self, db, df):
        dna = _dna([_exit_gene()])  # 仅出场基因+公式进场源
        ss = assemble_signal_set(db, dna, df)
        got = int(ss.entries.fillna(False).astype(bool).sum())
        expect = int(_plugin_mask(df).fillna(False).sum())  # 逐 bar 直译（组合层无事件化）
        assert expect > 0
        assert got == expect  # 修复前 got==0（静默作废）


class TestA7MixedRegression:
    """A7-②：混合形制回归不变（规则进场基因+公式源过滤）。"""

    def test_mixed_rule_plus_plugin_filter(self, db, df):
        # 规则进场：close > 300 bar 中位数（约半数 bar）
        med = float(df["close"].median())
        entry = SignalGene(indicator="SMA", params={"period": 5},
                           role=SignalRole.ENTRY_TRIGGER, field_name="sma",
                           condition={"type": "gt", "threshold": med})
        ss = assemble_signal_set(db, _dna([entry, _exit_gene()]), df)
        entries = ss.entries.fillna(False).astype(bool)
        sma5 = df["close"].rolling(5).mean()  # 基因= SMA(5) gt med
        expect = (sma5 > med) & _plugin_mask(df)
        pd.testing.assert_series_equal(
            entries, expect.fillna(False).astype(bool), check_names=False)
        assert int(entries.sum()) > 0  # 非退化：确有混合交集


class TestGuardOnlySubstance:
    """guard-only：仅 ENTRY_GUARD 进场基因——判「有规则进场侧」，
    guard 掩码语义不被单位元基线静默丢弃。"""

    def test_guard_false_blocks_plugin_entries(self, db, df):
        # guard 条件恒 False（close > 天文数字）——修复前（声明面判定
        # 失误时）基线被重置为恒 True，guard 语义丢失；修复后 guard
        # 掩码参与 AND：插件触发点也不进场。
        guard = SignalGene(indicator="SMA", params={"period": 5},
                           role=SignalRole.ENTRY_GUARD, field_name="sma",
                           condition={"type": "gt", "threshold": 1e12})
        ss = assemble_signal_set(db, _dna([guard, _exit_gene()]), df)
        entries = ss.entries.fillna(False).astype(bool)
        assert int(_plugin_mask(df).sum()) > 0  # 插件侧确有触发
        assert int(entries.sum()) == 0  # guard 全 False 拦住全部进场

    def test_guard_true_passes_plugin_entries(self, db, df):
        # 对照：guard 恒 True（close > 0）——插件触发自由进场。
        guard = SignalGene(indicator="SMA", params={"period": 5},
                           role=SignalRole.ENTRY_GUARD, field_name="sma",
                           condition={"type": "gt", "threshold": 0})
        ss = assemble_signal_set(db, _dna([guard, _exit_gene()]), df)
        got = int(ss.entries.fillna(False).astype(bool).sum())
        # guard 掩码恒 True（除 SMA warmup 首 4 bar），插件触发全部放行
        assert got == int(_plugin_mask(df).fillna(False).sum())


class TestCombineSentinelCaplog:
    """C4.2 caplog 三件：固定前缀/插件列名/警告不入返回体。"""

    def test_sentinel_warning_emitted_not_in_payload(
            self, db, df, caplog):
        import logging
        # 触发条件：规则进场基因条件全 False + 插件源在场
        entry = SignalGene(indicator="SMA", params={"period": 5},
                           role=SignalRole.ENTRY_TRIGGER, field_name="sma",
                           condition={"type": "gt", "threshold": 1e12})
        with caplog.at_level(logging.WARNING,
                             logger="core.analysis.signal_assembly"):
            ss = assemble_signal_set(db, _dna([entry, _exit_gene()]), df)
        joined = "\n".join(r.getMessage() for r in caplog.records)
        assert "[signal-assembly]" in joined  # 固定前缀
        assert "fx_dip" in joined  # 插件列名
        assert not hasattr(ss, "warnings") or not getattr(ss, "warnings", None)

    def test_real_zero_trigger_same_warning_is_contract(self, db, df, caplog):
        """边界一：真实零触发（规则基线与插件侧都为零）同样告警——
        spec 字面声明为正确行为契约（可观测优先，不区分误报）。"""
        import logging
        entry = SignalGene(indicator="SMA", params={"period": 5},
                           role=SignalRole.ENTRY_TRIGGER, field_name="sma",
                           condition={"type": "gt", "threshold": 1e12})
        zero_dna = StrategyDNA(
            signal_genes=[entry, _exit_gene()],
            signal_sources=[{"type": "formula", "ref": "fx_dip",
                             "threshold": -99.0, "operator": "<"}],
            composition={"mode": "logic", "op": "AND"})
        with caplog.at_level(logging.WARNING,
                             logger="core.analysis.signal_assembly"):
            assemble_signal_set(db, zero_dna, df)
        assert any("[signal-assembly]" in r.getMessage()
                   for r in caplog.records)

    def test_normal_mixed_no_warning(self, db, df, caplog):
        """边界二：正常混合态（规则基线非全 False）负向无警告。"""
        import logging
        med = float(df["close"].median())
        entry = SignalGene(indicator="SMA", params={"period": 5},
                           role=SignalRole.ENTRY_TRIGGER, field_name="sma",
                           condition={"type": "gt", "threshold": med})
        with caplog.at_level(logging.WARNING,
                             logger="core.analysis.signal_assembly"):
            assemble_signal_set(db, _dna([entry, _exit_gene()]), df)
        assert not any("[signal-assembly]" in r.getMessage()
                       for r in caplog.records)


class TestDegeneratePathUnchanged:
    """退化形态：无 plugin_sources 且无 composition 走原路径零涉及。"""

    def test_no_sources_no_composition_original_path(self, db, df):
        med = float(df["close"].median())
        entry = SignalGene(indicator="SMA", params={"period": 5},
                           role=SignalRole.ENTRY_TRIGGER, field_name="sma",
                           condition={"type": "gt", "threshold": med})
        dna = StrategyDNA(signal_genes=[entry, _exit_gene()])  # 无 sources/composition
        ss = assemble_signal_set(db, dna, df)
        entries = ss.entries.fillna(False).astype(bool)
        # 原路径无 policy 即原样（逐 bar 直译，无事件化）——掩码按
        # 基因 SMA(5) 手算
        sma5 = df["close"].rolling(5).mean()
        assert int(entries.sum()) == int((sma5 > med).fillna(False).sum())


# ===========================================================================
# tester 特征网补充（team3 审查轮）：缺陷形制完备 + 链路级锚 + 哨兵补强。
# 缺陷五真实形态=翻译器完整产物：sources 含 type=rule 条目
# （hypothesis_to_strategy.py「genes 非空即写 rule 条目」——基因只有
# 出场角色也写）。已实证（main worktree）：上方 A7-① 构造缺 rule 条目，
# 修复前后同走单位元——对判定切换点免疫。
# ===========================================================================


def _translator_sources() -> list:
    """翻译器完整产物形制的 signal_sources（rule 条目+公式源，新列表）。"""
    return [{"type": "rule"},
            {"type": "formula", "ref": "fx_dip",
             "threshold": -0.01, "operator": "<"}]


def _gene(role: SignalRole, threshold: float) -> SignalGene:
    return SignalGene(indicator="SMA", params={"period": 5},
                      role=role, field_name="sma",
                      condition={"type": "gt", "threshold": threshold})


class TestSubstanceDomainCompleteness:
    """判定域=进场两角色的完整枚举：sources 含 type=rule 条目（缺陷五
    真实形态）时，signal_genes 只要有任一非进场角色（或零基因）——声明
    面判定（type=rule 存在即判「有规则源」）会把全 False 基线 AND 上去
    作废纯插件进场；基因实质判定须仍走单位元复活。"""

    @pytest.mark.parametrize("role", [
        pytest.param(None, id="零基因-纯公式源带rule声明条目"),
        pytest.param(SignalRole.EXIT_TRIGGER, id="出场触发基因不算规则进场侧"),
        pytest.param(SignalRole.EXIT_GUARD, id="出场守卫基因不算规则进场侧"),
        pytest.param(SignalRole.ADD_TRIGGER, id="加仓触发基因不算规则进场侧"),
        pytest.param(SignalRole.ADD_GUARD, id="加仓守卫基因不算规则进场侧"),
        pytest.param(SignalRole.ADD_INHIBIT, id="加仓抑制基因不算规则进场侧"),
        pytest.param(SignalRole.REDUCE_TRIGGER, id="减仓触发基因不算规则进场侧"),
        pytest.param(SignalRole.REDUCE_GUARD, id="减仓守卫基因不算规则进场侧"),
        pytest.param(SignalRole.DIRECTION, id="方向基因不算规则进场侧"),
    ])
    def test_non_entry_role_with_rule_declaration_still_revives(
            self, db, df, role):
        genes = [] if role is None else [_gene(role, 1e12), _exit_gene()]
        dna = StrategyDNA(
            signal_genes=genes,
            signal_sources=_translator_sources(),
            composition={"mode": "logic", "op": "AND"})
        ss = assemble_signal_set(db, dna, df)
        got = ss.entries.fillna(False).astype(bool)
        expect = _plugin_mask(df).fillna(False).astype(bool)
        assert int(expect.sum()) > 0  # 插件侧确有触发（防数据退化假阳性）
        pd.testing.assert_series_equal(got, expect, check_names=False)
        # 修复前（声明面判定）：EXIT_TRIGGER 例即 st_f86b4295c58b 缺陷
        # 本体——has_rule_source=True 吃掉单位元，entries 恒全 False

    def test_translator_form_mixed_regression_unchanged(self, db, df):
        """β 形态回归锚：rule 条目+ENTRY_TRIGGER 基因——修复前后判定
        同 True、同走规则基线，entries 逐位不变（main 实测亦绿；锚
        「判定改动不动混合行为」——tasks A7-② 回归不变）。"""
        med = float(df["close"].median())
        dna = StrategyDNA(
            signal_genes=[_gene(SignalRole.ENTRY_TRIGGER, med), _exit_gene()],
            signal_sources=_translator_sources(),
            composition={"mode": "logic", "op": "AND"})
        ss = assemble_signal_set(db, dna, df)
        entries = ss.entries.fillna(False).astype(bool)
        sma5 = df["close"].rolling(5).mean()
        expect = ((sma5 > med) & _plugin_mask(df)).fillna(False).astype(bool)
        pd.testing.assert_series_equal(entries, expect, check_names=False)
        assert int(entries.sum()) > 0


class TestSentinelCompleteness:
    """哨兵契约补强：基因摘要入消息 + 纯插件正常复活形态负向无警告
    （哨兵条件四格：有进场基因+基线全 False=告警；有进场基因+基线有
    真=无告警[上方 test_normal_mixed_no_warning]；无进场基因+基线全
    False=正常复活不告警；无进场基因+基线有真=结构上仅非进场基因
    可产非 False 基线外的量，entries 仍由基因侧决定，同不告警）。"""

    def test_sentinel_message_contains_gene_summary(self, db, df, caplog):
        """design「消息含插件列名与基因摘要」：摘要=进场基因
        indicator+role 值两元素都要在消息里。"""
        import logging
        dna = StrategyDNA(
            signal_genes=[_gene(SignalRole.ENTRY_TRIGGER, 1e12), _exit_gene()],
            signal_sources=_translator_sources(),
            composition={"mode": "logic", "op": "AND"})
        with caplog.at_level(logging.WARNING,
                             logger="core.analysis.signal_assembly"):
            assemble_signal_set(db, dna, df)
        joined = "\n".join(r.getMessage() for r in caplog.records)
        assert "[signal-assembly]" in joined
        assert "SMA" in joined            # 基因摘要：indicator
        assert "entry_trigger" in joined  # 基因摘要：role 值

    def test_pure_plugin_revival_no_sentinel_warning(self, db, df, caplog):
        """纯插件正常复活（无进场基因，基线全 False 被单位元顶替是
        正确路径而非可疑形态）不触发哨兵。"""
        import logging
        dna = StrategyDNA(
            signal_genes=[_exit_gene()],
            signal_sources=[{"type": "formula", "ref": "fx_dip",
                             "threshold": -0.01, "operator": "<"}],
            composition={"mode": "logic", "op": "AND"})
        with caplog.at_level(logging.WARNING,
                             logger="core.analysis.signal_assembly"):
            assemble_signal_set(db, dna, df)
        assert not any("[signal-assembly]" in r.getMessage()
                       for r in caplog.records)


@pytest.fixture()
def bt_env(tmp_path: Path):
    """链路级环境：420 bar 1h parquet + 插件注册库；回测区间 [90,)，
    warmup 60（90>60 前缀可得）。"""
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    df = make_ohlcv(n=420, freq="1h", start="2024-01-01", seed=7)
    df.to_parquet(data_dir / "BTCUSDT_1h.parquet")
    db = tmp_path / "q.db"
    init_db_ext(db)
    ensure_research_tables(db)
    register_formula_plugin(db, name="dip", expression=PLUGIN_EXPR)
    return {"data_dir": data_dir, "db": db, "df": df,
            "start": df.index[90].isoformat(), "end": df.index[-1].isoformat()}


def _run_bt(env, dna) -> dict:
    """经装载→组装→引擎的受控回测桥取 metrics。"""
    metrics, _, _, _ = run_backtest_on_range(
        env["db"], env["data_dir"], dna, "BTCUSDT", "1h",
        env["start"], env["end"], warmup_bars=60)
    return metrics


def _range_plugin_triggers(env) -> int:
    """回测区间内插件掩码触发数（期望上界独立重算——区间切回与
    _slice_signal_set 同口径：[start, end)）。"""
    mask = _plugin_mask(env["df"]).fillna(False)
    start = pd.Timestamp(env["start"])
    return int(mask[mask.index >= start].sum())


class TestBacktestChainBehaviour:
    """链路级锚（spec 三场景的 THEN 都落在回测端）：run_backtest_on_range
    全流程（装载→组装→_slice 切回→引擎 shift(1)→vectorbt）验证。

    交易笔数关系从契约+引擎通用语义独立推导：entries 信号 bar 的下一
    bar 执行、持仓中不重复进场、出场后下一信号再进——故交易笔数 ≥1
    （有信号时）且 ≤ 信号 bar 数；交集过滤的信号集是两对照的子集，
    交易笔数不放大。"""

    def test_translator_form_pure_plugin_backtest_nonzero(
            self, bt_env):
        """α 形态（st_f86b4295c58b 完整形制：rule 条目+仅出场基因）回测：
        修复前 0 笔（静默作废），修复后不得为零，且不超过区间插件
        触发数（触发密度上界）。"""
        dna = StrategyDNA(
            signal_genes=[_exit_gene()],
            signal_sources=_translator_sources(),
            composition={"mode": "logic", "op": "AND"},
            execution_genes=ExecutionGenes(timeframe="1h"))
        metrics = _run_bt(bt_env, dna)
        upper = _range_plugin_triggers(bt_env)
        assert upper > 0  # 区间内插件确有触发（防数据退化假阳性）
        assert metrics["total_trades"] > 0  # 修复前恒 0（缺陷判别点）
        assert metrics["total_trades"] <= upper

    def test_translator_form_mixed_backtest_filter_converges(
            self, bt_env):
        """β 形态回测过滤生效：混合（规则∩插件）交易笔数不超过纯规则
        对照、也不超过纯插件对照（交集语义的双向收敛）。"""
        med = float(bt_env["df"]["close"].median())
        exit_ema = SignalGene(
            indicator="EMA", params={"period": 10},
            role=SignalRole.EXIT_TRIGGER, field_name="ema",
            condition={"type": "gt", "threshold": med})
        entry = _gene(SignalRole.ENTRY_TRIGGER, med)
        common = dict(execution_genes=ExecutionGenes(timeframe="1h"),
                      signal_genes=[entry, exit_ema])
        mixed = _run_bt(bt_env, StrategyDNA(
            signal_sources=_translator_sources(),
            composition={"mode": "logic", "op": "AND"}, **common))
        rule_only = _run_bt(bt_env, StrategyDNA(
            signal_sources=[{"type": "rule"}], **common))  # 退化形态
        plugin_only = _run_bt(bt_env, StrategyDNA(
            signal_genes=[exit_ema],
            signal_sources=_translator_sources()[1:],  # 仅公式源
            composition={"mode": "logic", "op": "AND"},
            execution_genes=ExecutionGenes(timeframe="1h")))
        assert rule_only["total_trades"] > 0  # 对照非退化
        assert plugin_only["total_trades"] > 0
        assert mixed["total_trades"] > 0  # 混合形制照常交易（回归不变）
        assert mixed["total_trades"] <= rule_only["total_trades"]
        assert mixed["total_trades"] <= plugin_only["total_trades"]

    def test_guard_false_blocks_backtest_zero_trades(self, bt_env):
        """guard 掩码语义经引擎端到端传递：仅 ENTRY_GUARD 进场基因且
        guard 恒 False——修复前基线被单位元顶替（guard 丢失，插件自由
        进场非零笔）；修复后 guard 掩码拦住全部进场=0 笔。"""
        guard = _gene(SignalRole.ENTRY_GUARD, 1e12)
        dna = StrategyDNA(
            signal_genes=[guard, _exit_gene()],
            signal_sources=_translator_sources(),
            composition={"mode": "logic", "op": "AND"},
            execution_genes=ExecutionGenes(timeframe="1h"))
        metrics = _run_bt(bt_env, dna)
        assert _range_plugin_triggers(bt_env) > 0  # 插件侧本可触发
        assert metrics["total_trades"] == 0  # guard 全 False 拦截

    def test_guard_true_backtest_nonzero_as_control(self, bt_env):
        """上例对照：仅 guard 条件翻转为恒 True——插件触发自由进场非
        零笔（排除「0 笔源于数据/引擎不触发」的假阳性归因）。"""
        guard = _gene(SignalRole.ENTRY_GUARD, 0)
        dna = StrategyDNA(
            signal_genes=[guard, _exit_gene()],
            signal_sources=_translator_sources(),
            composition={"mode": "logic", "op": "AND"},
            execution_genes=ExecutionGenes(timeframe="1h"))
        metrics = _run_bt(bt_env, dna)
        assert metrics["total_trades"] > 0

    def test_sentinel_visible_through_backtest_chain(self, bt_env, caplog):
        """哨兵在回测链路的可见性：规则基线全 False+插件源在场的形态
        经 run_backtest_on_range 全流程（装载/组装/切回/引擎），warning
        照常落日志（caplog 可捕获）且不改变回测结果照常返回。"""
        import logging
        dna = StrategyDNA(
            signal_genes=[_gene(SignalRole.ENTRY_TRIGGER, 1e12),
                          _exit_gene()],
            signal_sources=_translator_sources(),
            composition={"mode": "logic", "op": "AND"},
            execution_genes=ExecutionGenes(timeframe="1h"))
        with caplog.at_level(logging.WARNING,
                             logger="core.analysis.signal_assembly"):
            metrics = _run_bt(bt_env, dna)
        joined = "\n".join(r.getMessage() for r in caplog.records)
        assert "[signal-assembly]" in joined
        assert "fx_dip" in joined
        assert metrics["total_trades"] == 0  # 该形态本身零交易（照常返回）
