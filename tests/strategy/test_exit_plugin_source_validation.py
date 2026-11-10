"""出场判据插件源面锚（fr_296f43410afb 修复③——缺陷③复验面）。

锚点面（断言从 strategy-expression「信号源实质判定」MODIFIED 条款
scenario 推导，不从现行实现反推；sandbox-and-report-supplement 块 C）：
- 正向主锚：无分层策略 signal_genes 仅 entry 基因、出场全部来自
  role=="exit" 的公式信号源（缺陷③形制）→ validate_dna 放行
  （spec scenario「纯出场插件源校验放行」）——修复前该形态报
  "No exit signal defined"（出场判据只查 signal_genes 的绝对表述
  反转为角色对称形）。
- 负向锚：无 EXIT 角色基因且 signal_sources 无出场角色条目（含
  role 缺省/entry/exit_partial 源在场）→ 仍拒（spec scenario
  「出场两侧皆空仍拒」——判据放开不等于出场可缺）。
- MTF 面：复制形态层 exit 判据随顶层同判（顶层 role=="exit" 源
  顶替）；真分层不豁免层缺出场基因（与进场判据同形）。
- evidence 复验锚：库存 st_755c04c53a96（缺陷单 fr_296f43410afb
  关联策略）只读实测——signal_genes 无 EXIT 角色基因+signal_sources
  含 role=="exit" 公式源 → 修复前 "No exit signal defined" 误拒，
  修复后 is_valid=True（任务 C3 完成标准「修复前负向锚转正向——
  evidence 对象复验」）。
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

# 缺陷单关联策略（0.2 evidence 核验在库；禁改库——只读实测）
EVIDENCE_EXIT_LEG_SID = "st_755c04c53a96"


def _entry_gene() -> SignalGene:
    return SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                      "rsi", {"type": "lt", "threshold": 35})


def _exit_gene() -> SignalGene:
    return SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                      "rsi", {"type": "cross_above", "threshold": 50.0})


def _exit_source(source_type: str = "formula", **extra) -> dict:
    """出场角色插件源条目（缺陷③形制：fr_296f43410afb 的
    fx_s5_mom24 出场腿——阈值比较形，与翻译面插件源产物同键空间；
    缺省显式 role="exit"，extra 可覆盖）。"""
    src = {"type": source_type, "ref": "fx_s5_mom24",
           "threshold": 0.02, "operator": ">", "role": "exit"}
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
# 正向锚（修复③验收面——修复前红）
# ===========================================================================

class TestExitPluginSourceAdmitted:
    """出场判据双源：role=="exit" 插件源顶替出场完备。"""

    @pytest.mark.parametrize("source_type", ["formula", "model"])
    def test_formula_exit_leg_only_should_pass(self, source_type):
        """纯出场插件源放行（spec scenario 主锚——缺陷③形制）。

        signal_genes 仅 ENTRY 基因（进场面由基因满足）+出场全来自
        role=="exit" 源（formula/model 两型同判——插件子集对称）。
        修复前：出场判据只查 signal_genes → "No exit signal
        defined"（红——本锚即修复的验收面）。
        """
        dna = StrategyDNA(
            signal_genes=[_entry_gene()],
            signal_sources=[_exit_source(source_type)],
            risk_genes=RiskGenes(),
        )
        result = validate_dna(dna)
        assert result.is_valid, (
            f"公式出场腿（{source_type} 源 role=exit）应放行："
            f"{result.errors}")

    def test_exit_gene_plus_exit_source_still_passes(self):
        """EXIT 基因+role=="exit" 源混合形态放行（双源任一即过——
        防新判据误伤合法叠加形态）。"""
        dna = StrategyDNA(
            signal_genes=[_entry_gene(), _exit_gene()],
            signal_sources=[_exit_source()],
            risk_genes=RiskGenes(),
        )
        result = validate_dna(dna)
        assert result.is_valid, result.errors

    def test_evidence_stored_exit_leg_row_should_pass(self):
        """evidence 复验：库存 st_755c04c53a96（缺陷单关联策略）
        修复前报 "No exit signal defined"，修复后放行。

        形态（2026-11-09 只读实测）：signal_genes=FundingZScore/
        OI_ChangeRate 两 entry_trigger（无 EXIT 角色）+
        signal_sources=[rule, fx_s5_mom24(entry), fx_s5_mom24(exit)]。
        """
        dna = _load_stored_dna(EVIDENCE_EXIT_LEG_SID)
        result = validate_dna(dna)
        assert result.is_valid, (
            f"缺陷③ evidence 行 {EVIDENCE_EXIT_LEG_SID} 应过校验"
            f"（出场腿由 role=exit 插件源满足）：{result.errors}")


# ===========================================================================
# 负向锚（判据放开不等于出场可缺）
# ===========================================================================

class TestExitStillRequired:
    """出场两侧皆空仍拒；非 exit 角色源不顶替出场。"""

    def test_both_sides_empty_still_rejected(self):
        """spec scenario「出场两侧皆空仍拒」：无 EXIT 基因+无插件源
        条目 → 拒 no exit。"""
        dna = StrategyDNA(
            signal_genes=[_entry_gene()],
            risk_genes=RiskGenes(),
        )
        result = validate_dna(dna)
        assert not result.is_valid
        assert any("No exit signal defined" in e for e in result.errors), \
            result.errors

    @pytest.mark.parametrize(
        "source",
        [
            # role 缺省（=entry 语义，与 G7 同格但独立锚——本文件锚
            # spec scenario 侧不依赖既有文件在位）
            {"type": "formula", "ref": "fx_s5_mom24",
             "threshold": 0.0, "operator": "<"},
            # 显式 entry
            {"type": "formula", "ref": "fx_s5_mom24",
             "threshold": 0.0, "operator": "<", "role": "entry"},
            # exit_partial=部分离场通道，不构成全部出场完备
            {"type": "formula", "ref": "fx_s5_mom24",
             "threshold": 0.0, "operator": "<", "role": "exit_partial"},
        ],
        ids=["role缺省", "role_entry", "role_exit_partial"])
    def test_non_exit_roles_do_not_substitute_exit(self, source):
        """role 缺省/entry/exit_partial 源不顶替出场判据（角色对称形
        的否定侧——历史绝对表述修正为「非出场角色不顶替」）。"""
        dna = StrategyDNA(
            signal_genes=[_entry_gene()],
            signal_sources=[source],
            risk_genes=RiskGenes(),
        )
        result = validate_dna(dna)
        assert not result.is_valid
        assert any("No exit signal defined" in e for e in result.errors), \
            result.errors


# ===========================================================================
# MTF 面（复制形态放开/真分层维持——与进场判据同形）
# ===========================================================================

class TestMtfExitLeg:
    """MTF 层 exit 判据：复制形态随顶层同判，真分层不豁免。"""

    def test_mirror_layer_exit_leg_should_pass(self):
        """复制形态层 exit 判据随顶层同判：单层镜像（层基因集与顶层
        一致）+顶层无 EXIT 基因+顶层 role=="exit" 源 → 层 exit 放行
        （进场面同构：顶层 ENTRY 基因顶替层 entry）。"""
        gene = _entry_gene()
        dna = StrategyDNA(
            signal_genes=[gene],
            layers=[TimeframeLayer(
                timeframe="4h",
                signal_genes=[gene],
                logic_genes=LogicGenes(),
                role=None,
            )],
            execution_genes=ExecutionGenes(timeframe="4h"),
            signal_sources=[_exit_source()],
            risk_genes=RiskGenes(),
        )
        result = validate_dna(dna)
        assert result.is_valid, (
            f"复制形态 MTF 出场腿（顶层 role=exit 源）应放行："
            f"{result.errors}")

    def test_true_layering_missing_exit_still_rejected(self):
        """真分层不豁免层缺出场基因：两层（非镜像）、4h 层无 EXIT
        基因+顶层 role=="exit" 源在场 → "Layer 4h: no exit signal
        defined" 维持（与进场判据同形——插件源不豁免真分层强制）。"""
        dna = StrategyDNA(
            signal_genes=[_entry_gene(), _exit_gene()],
            layers=[
                TimeframeLayer("1h", [_entry_gene(), _exit_gene()],
                               LogicGenes(), "execution"),
                TimeframeLayer("4h", [_entry_gene()],
                               LogicGenes(), "zone"),
            ],
            execution_genes=ExecutionGenes(timeframe="1h"),
            cross_layer_logic="AND",
            signal_sources=[_exit_source()],
            risk_genes=RiskGenes(),
        )
        result = validate_dna(dna)
        assert not result.is_valid
        assert any(
            "Layer 4h: no exit signal defined" in e
            for e in result.errors), result.errors

    def test_single_layer_non_mirror_missing_exit_still_rejected(self):
        """单层但基因集与顶层不一致（非复制形态）缺出场 → 维持拒绝
        （防复制形态判据过宽：仅 len==1 即放行——与 G4 entry 侧变体
        同款校准）。"""
        dna = StrategyDNA(
            signal_genes=[_entry_gene(), _exit_gene()],
            layers=[TimeframeLayer("4h", [_entry_gene()],
                                   LogicGenes(), None)],
            execution_genes=ExecutionGenes(timeframe="4h"),
            signal_sources=[_exit_source()],
            risk_genes=RiskGenes(),
        )
        result = validate_dna(dna)
        assert not result.is_valid
        assert any(
            "Layer 4h: no exit signal defined" in e
            for e in result.errors), result.errors
