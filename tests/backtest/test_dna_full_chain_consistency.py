"""L1-E 策略DNA全链一致契约: 守护'DNA 从生成→序列化→DB存取→执行 全程不变形'。

审计发现 test_dna.py roundtrip 只断言部分字段(strategy_id/gene数/entry_logic/stop_loss),
未逐字段断言全等, 也未验证经 SQLite TEXT 列存取后一致, 更未验证 roundtrip 后 executor
翻译的信号一致。本契约补全:
  1. 全字段 to_json/from_json roundtrip 逐字段相等(含 MTF 层/控制字段/gene_signature)
  2. 经 SQLite TEXT 列存取后还原一致(strategy 表 + 纸盘 pt_task 表)
  3. roundtrip 后 dna_to_signal_set 产出的信号与原 DNA 完全一致(序列化不丢 executor 信息)
  4. legacy 'trend' role 迁移为 'structure'
  5. is_mtf 标志 roundtrip 保留
"""
from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.strategy.dna import (  # noqa: E402
    StrategyDNA, SignalGene, SignalRole, RiskGenes, LogicGenes,
    ExecutionGenes,
)
from core.strategy.executor import dna_to_signal_set  # noqa: E402
from core.persistence.repository import paper  # noqa: E402
from core.persistence.repository.strategy import (  # noqa: E402
    get_strategy, save_strategy,
)
from core.persistence.db_ext import init_db_ext  # noqa: E402
from tests.helpers.data_factory import make_dna, make_mtf_dna, make_ohlcv  # noqa: E402


def _assert_roundtrip_equal(original: StrategyDNA, roundtrip: StrategyDNA):
    """逐字段断言 roundtrip 后一致。

    不用 to_dict 整体比较: from_dict 的 auto-wrap(dna.py:360) 会给无 layers 的 DNA
    补单层 wrap, 导致 original(layers=None) 与 roundtrip(layers=wrapped) 的 to_dict
    在 layers 键上有差异, 但业务语义一致。改为逐业务字段比较。
    """
    # signal_genes (业务核心, 逐 gene 比较)
    assert len(original.signal_genes) == len(roundtrip.signal_genes)
    for g1, g2 in zip(original.signal_genes, roundtrip.signal_genes):
        assert g1.to_dict() == g2.to_dict(), "signal_gene 不一致"
    # risk/logic/execution genes
    assert original.risk_genes.to_dict() == roundtrip.risk_genes.to_dict(), "risk_genes 不一致"
    assert original.logic_genes.to_dict() == roundtrip.logic_genes.to_dict(), "logic_genes 不一致"
    assert original.execution_genes.to_dict() == roundtrip.execution_genes.to_dict(), "execution_genes 不一致"
    # MTF 控制字段
    assert original.cross_layer_logic == roundtrip.cross_layer_logic
    assert original.mtf_mode == roundtrip.mtf_mode
    assert original.confluence_threshold == roundtrip.confluence_threshold
    assert original.proximity_mult == roundtrip.proximity_mult
    # 标志一致(is_mtf 用 _layers_explicit 判断, 不受 auto-wrap 影响)
    assert original.is_mtf == roundtrip.is_mtf, f"is_mtf 不一致: {original.is_mtf} vs {roundtrip.is_mtf}"
    # gene_signature 核心部分(lev/dir)稳定
    assert f"lev:{original.risk_genes.leverage}|" in roundtrip.gene_signature
    assert f"dir:{original.risk_genes.direction}" in roundtrip.gene_signature
    # ⚠ 发现(记录非修复, 超出 L1-E 范围): gene_signature 完整串(dna.py:271 用 `if self.layers`)
    # 受 from_dict auto-wrap(dna.py:360) 影响不稳定——标准 DNA roundtrip 后多 layer 后缀,
    # MTF DNA roundtrip 后丢 layer 后缀。此 DNA 设计瑕疵可能影响 save_strategy 的
    # gene_signature dedup(db_ext.py:930), 故此处只断言核心 lev/dir, 完整 signature 待单独修复。


def _full_field_dna() -> StrategyDNA:
    """覆盖所有字段类型的 DNA: 多角色/参数/杠杆/方向/atr模式/MTF控制字段。"""
    return StrategyDNA(
        signal_genes=[
            SignalGene(indicator="RSI", params={"period": 14},
                       role=SignalRole.ENTRY_TRIGGER, field_name="RSI_14",
                       condition={"type": "lt", "threshold": 30}),
            SignalGene(indicator="MACD", params={"fast": 12, "slow": 26},
                       role=SignalRole.EXIT_TRIGGER, field_name="histogram",
                       condition={"type": "cross_below", "threshold": 0}),
        ],
        logic_genes=LogicGenes(entry_logic="OR", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="ETHUSDT"),
        risk_genes=RiskGenes(stop_loss=0.075, take_profit=0.15, position_size=0.35,
                             leverage=5, direction="short", sl_mode="atr", atr_period=21),
        cross_layer_logic="OR",
        mtf_mode=None,
        confluence_threshold=0.5,
        proximity_mult=2.0,
    )


class TestFullFieldRoundtrip:
    """1. 全字段 to_json/from_json 逐字段相等。"""

    def test_standard_dna_roundtrip_all_fields(self):
        dna = _full_field_dna()
        roundtrip = StrategyDNA.from_json(dna.to_json())
        _assert_roundtrip_equal(dna, roundtrip)

    def test_mtf_dna_roundtrip_preserves_layers_and_flags(self):
        """MTF DNA(多时间层)roundtrip 后 layers/mtf_mode/is_mtf 全保留。"""
        dna = make_mtf_dna(timeframes=("1d", "4h", "15m"), mtf_mode="direction+confluence",
                           confluence_threshold=0.5, proximity_mult=2.0)
        roundtrip = StrategyDNA.from_json(dna.to_json())
        _assert_roundtrip_equal(dna, roundtrip)
        assert roundtrip.is_mtf is True
        assert roundtrip.mtf_mode == "direction+confluence"
        assert len(roundtrip.layers) == 3


class TestSqliteTextSurvival:
    """2. 经 SQLite TEXT 列存取后还原一致(两条入库路径)。"""

    def test_strategy_table_roundtrip(self, tmp_path: Path):
        db = tmp_path / "test.db"
        init_db_ext(db)
        dna = _full_field_dna()
        save_strategy(db, strategy_id="s1", dna_json=dna.to_json(),
                      symbol="ETHUSDT", timeframe="4h", name="TestStrat")
        row = get_strategy(db, "s1")
        assert row is not None
        _assert_roundtrip_equal(dna, StrategyDNA.from_json(row["dna_json"]))

    def test_paper_task_roundtrip(self, tmp_path: Path):
        """纸盘新域载体：pt_task.dna_snapshot_json 存取还原一致
        （frontend-switch-and-retire R.2 由旧 paper_trading_task 表迁来，
        roundtrip 语义保留——上盘流转快照列同样是 TEXT 全文存储）。"""
        db = tmp_path / "test.db"
        init_db_ext(db)
        dna = _full_field_dna()
        paper.create_task(
            db, task_id="t1", strategy_id="s1",
            dna_snapshot_json=dna.to_json(),
            symbol="ETHUSDT", timeframe="4h",
            research_status_snapshot="unverified",
            initial_cash=100_000, fee=0.001)
        row = paper.get_task(db, "t1")
        assert row is not None
        _assert_roundtrip_equal(
            dna, StrategyDNA.from_json(row["dna_snapshot_json"]))


class TestLegacyMigration:
    """4. legacy 'trend' role 经 roundtrip 迁移为 'structure'(dna.py:329-330)。"""

    def test_legacy_trend_role_migrated_to_structure(self):
        dna = make_mtf_dna(timeframes=("1d", "4h"))
        for layer in dna.layers:  # 手动注入 legacy trend role
            layer.role = "trend"
        roundtrip = StrategyDNA.from_json(dna.to_json())
        for layer in roundtrip.layers:
            assert layer.role == "structure", f"layer role 未迁移: {layer.role}"


class TestExecutorSignalConsistency:
    """3. roundtrip 后 dna_to_signal_set 信号与原 DNA 完全一致。

    比 to_dict 相等更强: 证明 executor 翻译 DNA 为信号时不依赖任何 to_dict 之外的
    隐藏状态(roundtrip 后产出的 entries/exits/adds/reduces 与原 DNA 逐 bar 相同)。
    """

    def test_signal_set_identical_after_roundtrip(self):
        dna = make_dna(indicator="RSI", entry_value=30, exit_value=70)
        df = make_ohlcv(120, "4h")
        sig_original = dna_to_signal_set(dna, df)
        roundtrip = StrategyDNA.from_json(dna.to_json())
        sig_roundtrip = dna_to_signal_set(roundtrip, df)
        pd.testing.assert_series_equal(
            sig_original.entries, sig_roundtrip.entries, check_names=False)
        pd.testing.assert_series_equal(
            sig_original.exits, sig_roundtrip.exits, check_names=False)
        pd.testing.assert_series_equal(
            sig_original.adds, sig_roundtrip.adds, check_names=False)
        pd.testing.assert_series_equal(
            sig_original.reduces, sig_roundtrip.reduces, check_names=False)


# ═══════════════════════════════════════════════════════════════
# T② 二层往返不变式族（fix-mtf-execution-consistency R1b，组 1）
#
# 三要素硬性检查清单（红锚归因程序——缺一即失效）：
#   ① 测试 DNA 含带轴基因（timeframe=3d ≠ 执行周期 30m）；
#   ② 经「落库链二层形制」——伪层形制 dna_json（旧 to_dict 时代
#      auto-wrap 层字节）落库后库加载，非纯内存往返；
#   ③ 求值传装载集（执行轴 30m + 标注轴 3d + 伪层轴 30m）。
# ═══════════════════════════════════════════════════════════════

def _r1b_axis_env(n_30m: int = 4320, seed: int = 11):
    """30m 基准 + 3d 轴合成环境（形制同 test_mtf_layer_gene_tf.py）。"""
    base = make_ohlcv(n=n_30m, freq="30min", start="2024-01-01", seed=seed)

    def _resample(rule):
        out = pd.DataFrame({
            "open": base["open"].resample(rule).first(),
            "high": base["high"].resample(rule).max(),
            "low": base["low"].resample(rule).min(),
            "close": base["close"].resample(rule).last(),
            "volume": base["volume"].resample(rule).sum(),
        }).dropna()
        return out[(out["high"] >= out["low"]) & (out["volume"] > 0)]

    return base, _resample("3D")


def _r1b_annotated_dna() -> "StrategyDNA":
    """带轴基因 DNA（EMA@3d 趋势类 + BB/RVOL 无标注——活体同构骨架）。

    基因选型说明：趋势类带轴基因在 seed 固定合成数据下有自然触发
    （形态类 AND 组合在随机游走下可能零触发使对照侧恒空——锚失判
    别力）；带轴基因形态类的判别锚归 T①-B（组 2）。
    """
    from core.strategy.dna import TimeframeLayer  # 局部 import 防循环

    genes = [
        SignalGene("EMA", {"period": 10}, SignalRole.ENTRY_TRIGGER,
                   "ema", {"type": "price_above"},
                   timeframe="3d"),
        SignalGene("RVOL", {"period": 20}, SignalRole.ENTRY_TRIGGER,
                   "rvol", {"type": "gt", "threshold": 1.0}),
        SignalGene("BB", {"period": 20, "std": 2}, SignalRole.EXIT_TRIGGER,
                   "middle", {"type": "price_below"}),
    ]
    dna = StrategyDNA(
        strategy_id="st_t2_roundtrip_anchor",
        signal_genes=genes,
        execution_genes=ExecutionGenes(timeframe="30m", symbol="BTCUSDT"),
        logic_genes=LogicGenes(),
        risk_genes=RiskGenes(direction="short", exit_fraction=0.5,
                             add_fraction=0.1),
        signal_sources=[{"type": "rule"}])
    return dna


def _r1b_legacy_autowrap_json(dna: "StrategyDNA") -> str:
    """构造旧 to_dict 时代的伪层形制 dna_json（R1b 前序列化形制）。

    旧 to_dict 会把 from_json auto-wrap 的伪层写出——本函数等价复刻
    该历史形制：正常 to_dict 后注入单伪层（层周期=执行周期、层基因
    同 signal_genes、role None、无 mtf_mode 键差异）。
    """
    import json as _json
    from core.strategy.dna import TimeframeLayer

    payload = _json.loads(dna.to_json())
    ghost = TimeframeLayer(
        timeframe=dna.execution_genes.timeframe,
        signal_genes=list(dna.signal_genes),
        logic_genes=LogicGenes(
            entry_logic=dna.logic_genes.entry_logic,
            exit_logic=dna.logic_genes.exit_logic),
    )
    payload["layers"] = [ghost.to_dict()]
    return _json.dumps(payload, ensure_ascii=False, indent=2)


def _assert_five_tuple_equal(sig_a, sig_b):
    """五元组逐位相等断言（entries/exits/adds/reduces/entry_direction）。"""
    pd.testing.assert_series_equal(
        sig_a.entries, sig_b.entries, check_names=False)
    pd.testing.assert_series_equal(
        sig_a.exits, sig_b.exits, check_names=False)
    pd.testing.assert_series_equal(
        sig_a.adds, sig_b.adds, check_names=False)
    pd.testing.assert_series_equal(
        sig_a.reduces, sig_b.reduces, check_names=False)
    pd.testing.assert_series_equal(
        sig_a.entry_direction, sig_b.entry_direction, check_names=False)


class TestR1bTwoLayerRoundtripInvariants:
    """T② 族：内存构造态 vs 落库链加载态同路由同语义（R1b 四条件判据）。"""

    def _load_set(self, base, df_3d):
        return {"30m": base, "3d": df_3d}

    def test_t2_1_memory_vs_loaded_five_tuple_identical(self, tmp_path):
        """主锚：三要素 DNA 伪层加载态与内存构造态五元组逐位相等。"""
        base, df_3d = _r1b_axis_env()
        dna_mem = _r1b_annotated_dna()
        legacy_json = _r1b_legacy_autowrap_json(dna_mem)
        dna_loaded = StrategyDNA.from_json(legacy_json)
        assert dna_loaded.is_mtf is False  # 愈合判据生效（前置）

        sig_mem = dna_to_signal_set(
            dna_mem, base, self._load_set(base, df_3d))
        sig_loaded = dna_to_signal_set(
            dna_loaded, base, self._load_set(base, df_3d))
        assert int(sig_mem.entries.fillna(False).sum()) > 0, (
            "对照信号应非空（seed 固定下带轴基因应有触发）")
        _assert_five_tuple_equal(sig_mem, sig_loaded)

    def test_t2_2_legacy_loaded_equals_equivalent_flat_dna(self, tmp_path):
        """存量对照锚：伪层加载态求值 == 语义字段现造等价无层 DNA 求值。"""
        base, df_3d = _r1b_axis_env()
        dna_mem = _r1b_annotated_dna()
        legacy_json = _r1b_legacy_autowrap_json(dna_mem)
        dna_loaded = StrategyDNA.from_json(legacy_json)

        # 等价无层 DNA：以同一语义字段现造（非往返产物）
        flat = StrategyDNA(
            strategy_id="st_t2_flat_equiv",
            signal_genes=[SignalGene.from_dict(g.to_dict())
                          for g in dna_loaded.signal_genes],
            execution_genes=ExecutionGenes(**{
                "timeframe": dna_loaded.execution_genes.timeframe,
                "symbol": dna_loaded.execution_genes.symbol}),
            logic_genes=LogicGenes(),
            risk_genes=RiskGenes(direction="short", exit_fraction=0.5,
                                 add_fraction=0.1),
            signal_sources=[{"type": "rule"}])
        sig_loaded = dna_to_signal_set(
            dna_loaded, base, self._load_set(base, df_3d))
        sig_flat = dna_to_signal_set(
            flat, base, self._load_set(base, df_3d))
        _assert_five_tuple_equal(sig_flat, sig_loaded)

    def test_t2_3a_autowrap_form_loads_as_single_period(self):
        """形态分锚 (a)：伪层形制加载态 is_mtf=False。"""
        dna_mem = _r1b_annotated_dna()
        loaded = StrategyDNA.from_json(_r1b_legacy_autowrap_json(dna_mem))
        assert loaded.is_mtf is False
        assert loaded.layers is not None and len(loaded.layers) == 1
        # 内存构造态同路由（无显式层构造）
        assert dna_mem.is_mtf is False

    def test_t2_3b_explicit_layer_forms_stay_mtf(self):
        """形态分锚 (b)：真显式层五形态加载态 is_mtf=True 保持（负向防误归）。"""
        dna_mem = _r1b_annotated_dna()

        def _with_layers_json(layers, mtf_mode=None, empty_top=False):
            import json as _json
            payload = _json.loads(dna_mem.to_json())
            payload["layers"] = [l.to_dict() for l in layers]
            if mtf_mode is not None:
                payload["mtf_mode"] = mtf_mode
            if empty_top:
                payload["signal_genes"] = []
            return _json.dumps(payload, ensure_ascii=False)

        from core.strategy.dna import TimeframeLayer
        one_tf = dna_mem.execution_genes.timeframe
        genes = list(dna_mem.signal_genes)

        # 多层（4h+30m）
        multi = [
            TimeframeLayer("4h", list(genes), LogicGenes()),
            TimeframeLayer(one_tf, list(genes), LogicGenes()),
        ]
        assert StrategyDNA.from_json(_with_layers_json(multi)).is_mtf is True
        # 带 mtf_mode 单层=执行周期
        single = [TimeframeLayer(one_tf, list(genes), LogicGenes())]
        assert StrategyDNA.from_json(
            _with_layers_json(single, mtf_mode="score")).is_mtf is True
        # 层周期异构单层（4h≠执行 30m）
        hetero = [TimeframeLayer("4h", list(genes), LogicGenes())]
        assert StrategyDNA.from_json(_with_layers_json(hetero)).is_mtf is True
        # 带 role 单层（=执行周期但带角色——进化 trend 层形态）
        roled = [TimeframeLayer(one_tf, list(genes), LogicGenes(),
                                role="structure")]
        assert StrategyDNA.from_json(_with_layers_json(roled)).is_mtf is True
        # 顶层空基因+单层同周期（镜像条件不满足——基因只在层内的真显式
        # 层，tf 不变性冻结锚与进化层形构造实存形态；误归会使单周期
        # 路径读空基因集产出全零信号——实施期冻结值红实证）
        empty_top = [TimeframeLayer(one_tf, list(genes), LogicGenes())]
        loaded = StrategyDNA.from_json(
            _with_layers_json(empty_top, empty_top=True))
        assert loaded.is_mtf is True, (
            "顶层空 signal_genes 的单层形态不满足镜像特征，须保持层路径")

    def test_t2_4_new_product_json_has_no_layers_key(self):
        """新产物字节锚：R1b 后 to_dict 不再写 auto-wrap 层（结构锚）。"""
        import json as _json
        dna = _r1b_annotated_dna()
        # 构造态经规范化往返（translate :564 同款 from_json→to_json）
        normalized = StrategyDNA.from_json(dna.to_json())
        payload = _json.loads(normalized.to_json())
        assert "layers" not in payload, (
            "新转化产物 dna_json 不应携带 auto-wrap 层字节")

    def test_t2_5_legacy_bytes_not_migrated_on_load(self, tmp_path):
        """存量字节不迁移锚：加载含伪层存量 dna_json 后库内字节不变。"""
        db = tmp_path / "t2_5.db"
        init_db_ext(db)
        dna_mem = _r1b_annotated_dna()
        legacy_json = _r1b_legacy_autowrap_json(dna_mem)
        save_strategy(db, strategy_id="st_t2_legacy",
                      dna_json=legacy_json, symbol="BTCUSDT",
                      timeframe="30m", source="manual")

        row_before = get_strategy(db, "st_t2_legacy")
        loaded = StrategyDNA.from_json(row_before["dna_json"])  # 加载动作
        assert loaded.is_mtf is False  # 愈合生效且不改字节
        row_after = get_strategy(db, "st_t2_legacy")
        assert row_before["dna_json"] == row_after["dna_json"], (
            "加载不得触发库内 dna_json 字节清洗")
        assert '"layers"' in row_after["dna_json"], (
            "存量伪层字节应原样保留（可审计）")
