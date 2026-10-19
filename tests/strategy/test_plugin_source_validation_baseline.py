"""插件源 DNA 校验红灯基线锚（recognize-plugin-source-dna 任务 0.1）。

锚点面（断言全部从 design 卡 T2 判据语义推导，不从现行实现反推）：
- 红灯批四组（本锚提交时红=组 2 判据改造的验收面，防撤卡——组 2
  实施后应全数转绿）：
  * R1 纯插件策略应被放行——signal_genes 仅 EXIT 角色基因 +
    signal_sources 含 formula 源（type="formula"，无 role 键）。
    crud 创建/更新通道（crud.py:171/:304）最终消费同一 validate_dna
    判据，core 层直断即通道语义同判面。
  * R2 复制形态 MTF 应被放行——len(dna.layers)==1 且层 signal_genes
    基因集与顶层一致（对齐库存 st_0c1c0b54daa7 实测形态），顶层
    signal_sources 含无 role 键 formula 源（缺省解释为 entry）。
  * ⑤a 存量红痕 st_0c1c0b54daa7 只读实测（sqlite mode=ro）——主断言
    按目标语义写 is_valid=True；现状对照读数（2026-10-19 只读实测）：
    is_valid=False，errors=['Layer 4h: no entry signal defined']
    （MTF 分支误拒本体=撤卡事故机理）。
  * ⑥ role 缺省解析独立锚——无 role 键插件源按 entry 参与进场判定
    的正向证明（T1 契约「缺省=entry」读取侧），与生产侧显式写
    role:"entry" 构成两侧证明：两变体同判放行。
- 防回退批十项（本锚提交时绿，防新判据过严/过宽；G7-G9 为 2026-10-19
  tester 复核补锚——team-lead 裁定 C-1/C-2/C-3 三处契约点补护栏）：
  * G1 标准 ENTRY+EXIT 基因策略保持放行。
  * G2 标准 MTF 真分层保持放行。
  * G3 signal_genes 与 signal_sources 双空仍拒（"No entry signal
    defined"——T2「两源皆空才拒」的空侧边界）。
  * G4 AND 真分层缺 entry 仍拒（design T2「真分层维持强制」；含
    单层但层基因集与顶层不一致的变体——防判据过宽「仅 len==1 即
    放行」误放真分层）。
  * G5 内置 ENTRY 基因+插件源混合形态保持放行（防新判据误伤含源
    合法形态）。
  * G6 仅 role:"exit" 源+无 ENTRY 基因的错位形态仍拒（design 补丁③
    负向格：role:"exit" 源不顶替 entry，防静默零交易形态复刻）。
  * ⑤b 存量两行混合形态回归锚——st_fa536d692525/st_7cd382d439fa
    只读实测 is_valid=True（现状绿+组 2 改造后维持绿——防新判据
    过严误拒混合形态的报警锚）。
  * G7 插件源不顶替出场（T2「exit 判据不变」）：ENTRY 基因在场+
    无 EXIT 基因+插件源在场 → 仍报 "No exit signal defined"。
  * G8 OR 组合维持现状（design T2 裁量留痕）：真分层 OR 缺 entry
    层仍拒——「OR 放开待真实形态出现再议」的护栏锚。
  * G9 补丁③ MTF 对向格：复制形态（单层基因集=顶层）+顶层仅
    role:"exit" 源 → 层与顶层同报（role:"exit" 不顶替 entry 在
    MTF 侧同效，与 G6 非 MTF 格构成两侧证明）。

两层归属注记（tasks 0.1 指定写入）：⑤b 两行绿对照属 validate_dna 层
（结构校验预期 True）；组 4.3 的混合形态 REST 实测断言属 REST 通道层
（同批行预期 422 PLUGIN_SOURCES_UNSUPPORTED）——同一行策略在两层的
正确预期分别是 True 与 422，并行不悖（结构合法与通道不支持是两个
正交判定）。

库存三行实测原始读数（2026-10-19，sqlite3 mode=ro 只读）：
- st_0c1c0b54daa7: is_valid=False, errors=['Layer 4h: no entry signal
  defined']；形态=显式 layers（1 层 4h）层内仅 exit_trigger 与顶层
  一致，signal_sources=[{"type":"rule"}, {"type":"formula",
  "ref":"fx_fx_trend_pullback","threshold":0.5,"operator":">"}]
- st_fa536d692525 / st_7cd382d439fa: is_valid=True, errors=[]（含内置
  ENTRY 基因本就通过）
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
    StrategyDNA, TimeframeLayer,
)
from core.strategy.validator import validate_dna

# 库存验证资产定位（判定并集纪律外的库文件只读保护——mode=ro）
_REPO_ROOT = Path(__file__).resolve().parents[2]
_QUANT_DB = _REPO_ROOT / "data" / "quant.db"

# 库存三行策略 id（组 0 验证资产，非测试装置——禁改库）
RED_TRACE_SID = "st_0c1c0b54daa7"
HYBRID_SIDS = ("st_fa536d692525", "st_7cd382d439fa")


def _exit_gene() -> SignalGene:
    """出场基因（对齐库存红痕行实测形态：RSI cross_above 50）。"""
    return SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                      "rsi", {"type": "cross_above", "threshold": 50.0})


def _entry_gene() -> SignalGene:
    return SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                      "rsi", {"type": "lt", "threshold": 35})


def _formula_source(**extra) -> dict:
    """公式插件源条目（对齐 signal_assembly 消费链键空间，
    缺省不写 role 键——存量 11 行零 role 键形态）。"""
    src = {"type": "formula", "ref": "fx_fx_trend_pullback",
           "threshold": 0.5, "operator": ">"}
    src.update(extra)
    return src


def _load_stored_dna(strategy_id: str) -> StrategyDNA:
    """只读通道取库存策略（mode=ro——禁改库纪律）。

    库文件或目标行不存在时 skip 保护（删库等环境差异下锚不误报）。
    """
    if not _QUANT_DB.exists():
        pytest.skip(f"库文件不存在，跳过库存实测锚：{_QUANT_DB}")
    conn = sqlite3.connect(f"file:{_QUANT_DB}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT dna_json FROM strategy WHERE strategy_id=?",
            (strategy_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        pytest.skip(f"库存策略 {strategy_id} 不存在（库状态变化），"
                    "跳过库存实测锚")
    return StrategyDNA.from_json(row[0])


# ===========================================================================
# 红灯批（提交时红；组 2 判据改造后转绿——验收面）
# ===========================================================================

class TestRedBaseline:
    """红灯批四组：目标语义断言，提交时红防撤卡。"""

    def test_r1_pure_plugin_non_mtf_should_pass(self):
        """R1 纯插件策略应被放行（非 MTF 分支目标判据）。

        signal_genes 仅 EXIT 角色基因 + signal_sources 含无 role 键的
        formula 源 → T2 目标判据下应放行（插件源解释为 entry 顶替
        进场判定；exit 仍由基因满足）。现状：非 MTF 分支只查
        signal_genes ENTRY 角色 → "No entry signal defined"（红）。
        crud.py:171/:304 创建/更新通道消费同一 validate_dna 判据。
        """
        dna = StrategyDNA(
            signal_genes=[_exit_gene()],
            signal_sources=[_formula_source()],
            risk_genes=RiskGenes(),
        )
        result = validate_dna(dna)
        assert result.is_valid, (
            f"纯插件策略（EXIT 基因+formula 源）应放行：{result.errors}")

    def test_r2_replica_mtf_single_layer_should_pass(self):
        """R2 复制形态 MTF 应被放行（MTF 分支目标判据）。

        构造对齐库存 st_0c1c0b54daa7 实测形态：len(layers)==1、层
        signal_genes 基因集与顶层一致（同一 RSI exit_trigger 基因）、
        顶层 signal_sources 含无 role 键 formula 源（缺省解释为
        entry）。T2 目标判据：复制形态层 entry 判据随顶层同判，顶层
        插件源可顶替 → 放行。现状：MTF 分支逐层强制查 ENTRY 基因 →
        "Layer 4h: no entry signal defined"（红）。
        """
        gene = _exit_gene()
        dna = StrategyDNA(
            signal_genes=[gene],
            layers=[TimeframeLayer(
                timeframe="4h",
                signal_genes=[gene],
                logic_genes=LogicGenes(),
                role=None,
            )],
            execution_genes=ExecutionGenes(timeframe="4h"),
            signal_sources=[_formula_source()],
            risk_genes=RiskGenes(),
        )
        result = validate_dna(dna)
        assert result.is_valid, (
            f"复制形态 MTF（单层与顶层同基因集+formula 源）应放行："
            f"{result.errors}")

    def test_5a_stored_red_trace_row_should_pass(self):
        """⑤a 存量红痕 st_0c1c0b54daa7 只读实测应过校验（目标语义）。

        主断言按目标语义写 is_valid=True（组 2 实施后转绿）。
        现状对照（2026-10-19 只读实测）：is_valid=False，
        errors=['Layer 4h: no entry signal defined']——MTF 分支对
        复制形态层的误拒本体（撤卡事故机理：T3 二道校验上线时若
        T2 未先行，该行被读库防线误拒）。
        """
        dna = _load_stored_dna(RED_TRACE_SID)
        result = validate_dna(dna)
        assert result.is_valid, (
            f"存量红痕行 {RED_TRACE_SID} 应过校验：{result.errors}")

    def test_6_role_default_entry_resolution_should_pass(self):
        """⑥ role 缺省解析独立锚：无 role 键源与显式 role="entry" 源
        同判放行（T1 契约「缺省=entry」读取侧直接证明）。

        两变体构造：signal_genes 仅 EXIT 基因（exit 判据由基因满足，
        缺省解析在 entry 面观察）+ formula 源分别取无 role 键 / 显式
        role:"entry"。两变体均应放行且同判——存量 11 行零 role 键
        与 T1 生产面显式写键在读取侧等价。

        注：exit 判据不变是 T2 契约（插件源不顶替出场），故本锚
        exit 面由 EXIT 基因满足——「缺省=entry」只能在 entry 判定
        面上观察到放行差异。
        """
        dna_default = StrategyDNA(
            signal_genes=[_exit_gene()],
            signal_sources=[_formula_source()],  # 无 role 键（缺省形态）
            risk_genes=RiskGenes(),
        )
        dna_explicit = StrategyDNA(
            signal_genes=[_exit_gene()],
            signal_sources=[_formula_source(role="entry")],
            risk_genes=RiskGenes(),
        )
        result_default = validate_dna(dna_default)
        result_explicit = validate_dna(dna_explicit)
        assert result_default.is_valid, (
            f"无 role 键源应按缺省 entry 参与进场判定（放行）："
            f"{result_default.errors}")
        assert result_explicit.is_valid, (
            f"显式 role='entry' 源应放行：{result_explicit.errors}")
        assert result_default.is_valid == result_explicit.is_valid, \
            "缺省与显式 entry 两变体必须同判（读取侧等价性）"


# ===========================================================================
# 防回退批（提交时绿；防新判据过严误拒/过宽误放）
# ===========================================================================

class TestGuardGreenBaseline:
    """防回退批七项：既有合法形态保持放行、非法形态保持拒绝。"""

    def test_g1_standard_gene_strategy_still_passes(self):
        """G1 标准 ENTRY+EXIT 基因策略（无插件源）保持放行。"""
        dna = StrategyDNA(
            signal_genes=[_entry_gene(), _exit_gene()],
            risk_genes=RiskGenes(),
        )
        result = validate_dna(dna)
        assert result.is_valid, result.errors

    def test_g2_standard_mtf_layers_still_passes(self):
        """G2 标准 MTF 真分层（每层 ENTRY+EXIT 齐全）保持放行。"""
        dna = StrategyDNA(
            signal_genes=[_entry_gene(), _exit_gene()],
            layers=[
                TimeframeLayer("1h", [_entry_gene(), _exit_gene()],
                               LogicGenes(), "execution"),
                TimeframeLayer("4h", [_entry_gene(), _exit_gene()],
                               LogicGenes(), "zone"),
            ],
            execution_genes=ExecutionGenes(timeframe="1h"),
            cross_layer_logic="AND",
            risk_genes=RiskGenes(),
        )
        result = validate_dna(dna)
        assert result.is_valid, result.errors

    @pytest.mark.parametrize("empty_sources", [None, []],
                             ids=["sources为None", "sources为空列表"])
    def test_g3_both_sources_empty_still_rejected(self, empty_sources):
        """G3 signal_genes 与 signal_sources 双空仍拒（T2「两源皆空
        才拒」的空侧边界——防新判据把双空放行）。"""
        dna = StrategyDNA(
            signal_genes=[],
            signal_sources=empty_sources,
            risk_genes=RiskGenes(),
        )
        result = validate_dna(dna)
        assert not result.is_valid
        assert any("No entry signal defined" in e for e in result.errors), \
            result.errors

    @pytest.mark.parametrize("mode", ["多层真分层缺entry", "单层基因集不一致缺entry"])
    def test_g4_true_layering_missing_entry_still_rejected(self, mode):
        """G4 AND 真分层缺 entry 仍拒（design T2「真分层维持强制」）。

        变体一：两层真分层，4h 层缺 ENTRY 基因 → "Layer 4h: no entry
        signal defined"。变体二：单层但层基因集与顶层不一致（顶层
        完整、层仅 EXIT）且层缺 entry → 同拒——防组 2 复制形态判据
        过宽（仅 len==1 即放行）误放真分层（任务 2.2「从严起步」
        校准点的负向锚）。两变体顶层均带 formula 源（若判据误按
        len==1/顶层源放行层，此锚转红报警）。
        """
        if mode == "多层真分层缺entry":
            dna = StrategyDNA(
                signal_genes=[_entry_gene(), _exit_gene()],
                layers=[
                    TimeframeLayer("1h", [_entry_gene(), _exit_gene()],
                                   LogicGenes(), "execution"),
                    TimeframeLayer("4h", [_exit_gene()],
                                   LogicGenes(), "zone"),
                ],
                execution_genes=ExecutionGenes(timeframe="1h"),
                cross_layer_logic="AND",
                signal_sources=[_formula_source()],
                risk_genes=RiskGenes(),
            )
            missing_tf = "4h"
        else:
            dna = StrategyDNA(
                signal_genes=[_entry_gene(), _exit_gene()],
                layers=[TimeframeLayer("4h", [_exit_gene()],
                                       LogicGenes(), None)],
                execution_genes=ExecutionGenes(timeframe="4h"),
                signal_sources=[_formula_source()],
                risk_genes=RiskGenes(),
            )
            missing_tf = "4h"
        result = validate_dna(dna)
        assert not result.is_valid
        assert any(
            f"Layer {missing_tf}: no entry signal defined" in e
            for e in result.errors), result.errors

    def test_g5_hybrid_builtin_entry_plus_plugin_source_still_passes(self):
        """G5 内置 ENTRY 基因+插件源混合形态保持放行（防新判据误伤
        含源合法形态——与 ⑤b 库存两行同构的构造单元）。"""
        dna = StrategyDNA(
            signal_genes=[_entry_gene(), _exit_gene()],
            signal_sources=[
                {"type": "rule"},
                _formula_source(ref="fx_ma20_deviation_v2",
                                threshold=-0.01, operator="<"),
            ],
            risk_genes=RiskGenes(),
        )
        result = validate_dna(dna)
        assert result.is_valid, result.errors

    def test_g6_role_exit_source_does_not_substitute_entry(self):
        """G6 仅 role:"exit" 源+无 ENTRY 基因的错位形态仍拒（design
        补丁③负向格：role:"exit" 源不顶替 entry——该形态若被放行，
        引擎只组插件源进场侧得进场条件空集=静默零交易形态复刻）。"""
        dna = StrategyDNA(
            signal_genes=[_exit_gene()],
            signal_sources=[_formula_source(role="exit")],
            risk_genes=RiskGenes(),
        )
        result = validate_dna(dna)
        assert not result.is_valid
        assert any("No entry signal defined" in e for e in result.errors), \
            result.errors

    @pytest.mark.parametrize("sid", HYBRID_SIDS)
    def test_5b_stored_hybrid_rows_remain_valid(self, sid):
        """⑤b 存量两行混合形态回归锚（只读实测，现状绿+改造后维持
        绿——防新判据过严误拒混合形态的报警锚）。

        归属：本断言属 validate_dna 层（结构校验预期 True）；组 4.3
        的同批行 REST 实测属通道层（预期 422
        PLUGIN_SOURCES_UNSUPPORTED）——两层预期并行不悖。
        """
        dna = _load_stored_dna(sid)
        result = validate_dna(dna)
        assert result.is_valid, (
            f"存量混合形态行 {sid} 应维持通过：{result.errors}")

    def test_g7_plugin_source_does_not_substitute_exit(self):
        """G7 插件源不顶替出场（T2 契约「exit 判据不变——signal_genes
        无 EXIT 角色基因仍报错」）：ENTRY 基因在场（entry 面由基因
        满足）+无 EXIT 基因+插件源在场 → 仍报 "No exit signal
        defined"。现有红灯批用例的 exit 面均由 EXIT 基因满足，若
        实现误让插件源（或 role:"exit" 源）顶替出场判据，全套不红
        ——本锚补该护栏（2026-10-19 tester 复核补锚 C-1）。"""
        dna = StrategyDNA(
            signal_genes=[_entry_gene()],
            signal_sources=[_formula_source()],
            risk_genes=RiskGenes(),
        )
        result = validate_dna(dna)
        assert not result.is_valid
        assert any("No exit signal defined" in e for e in result.errors), \
            result.errors

    def test_g8_or_true_layering_missing_entry_still_rejected(self):
        """G8 OR 组合维持现状（design T2 裁量留痕「OR 放开待真实形态
        出现再议」——不为未出现形态放开）：真分层 OR 缺 entry 层仍报
        "Layer X: no entry signal defined"。G4 仅覆盖 AND/缺省组合，
        本锚补 OR 侧护栏（2026-10-19 tester 复核补锚 C-2）。"""
        dna = StrategyDNA(
            signal_genes=[_entry_gene(), _exit_gene()],
            layers=[
                TimeframeLayer("1h", [_entry_gene(), _exit_gene()],
                               LogicGenes(), "execution"),
                TimeframeLayer("4h", [_exit_gene()],
                               LogicGenes(), "zone"),
            ],
            execution_genes=ExecutionGenes(timeframe="1h"),
            cross_layer_logic="OR",
            signal_sources=[_formula_source()],
            risk_genes=RiskGenes(),
        )
        result = validate_dna(dna)
        assert not result.is_valid
        assert any(
            f"Layer 4h: no entry signal defined" in e
            for e in result.errors), result.errors

    def test_g9_replica_mtf_with_only_role_exit_source_still_rejected(self):
        """G9 补丁③ MTF 对向格：复制形态（len(layers)==1 且层基因集
        与顶层一致）+顶层仅 role:"exit" 源（顶层 entry 面空）→ 层与
        顶层同报 "Layer X: no entry signal defined"——role:"exit" 源
        不顶替 entry 在 MTF 复制形态放行判据中同效（R2 测顶层缺省源
        放行、G6 测非 MTF 拒，本锚补中间格；2026-10-19 tester 复核
        补锚 C-3）。"""
        gene = _exit_gene()
        dna = StrategyDNA(
            signal_genes=[gene],
            layers=[TimeframeLayer(
                timeframe="4h",
                signal_genes=[gene],
                logic_genes=LogicGenes(),
                role=None,
            )],
            execution_genes=ExecutionGenes(timeframe="4h"),
            signal_sources=[_formula_source(role="exit")],
            risk_genes=RiskGenes(),
        )
        result = validate_dna(dna)
        assert not result.is_valid
        assert any(
            f"Layer 4h: no entry signal defined" in e
            for e in result.errors), result.errors
