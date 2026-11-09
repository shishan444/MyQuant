"""CARD-02（S-1 executor 面堵口）回归：cross_params 联合约束在策略取列路径的统一拦截。

契约依据：champion-indicators spec R5「alpha+beta≥1 MUST 经 cross_params
结构化拒绝（平稳性）」＋S-1 组卡定性扩展——三条取列路径全覆盖（研究链
column_ref._resolve_indicator_ref / indicator_query.validate_params 两通道
既有，executor._get_indicator_column resolve 段为手工 DNA/变异产物直达的
第三条，本修复堵口）；同钩同判＝三钩子指标（GARCHFilter/SessionRange/
BoxChannel）在本路径统一拦截，非单指标特判。

期望不经被测实现生成：violation 子串为字面量锚（注册表钩子单源文案的
独立转录），端到端只断言拦截通道与收编形制（ResearchError 码/
detail.cause）。进化链 sanitize 面（validate_offspring）在
tests/evolution/test_sanitize_offspring.py。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

import pandas as pd

from core.analysis.errors import ResearchError
from core.strategy.executor import (
    _get_indicator_column, clear_indicator_cache, dna_to_signal_set,
)
from tests.helpers.data_factory import (
    make_dna, make_enhanced_df, make_signal_gene,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_indicator_cache()
    yield
    clear_indicator_cache()


def _dna_with_entry_gene(indicator: str, params: dict):
    """合法 DNA 首位 entry 基因替换为指定指标/参数的目标 DNA。"""
    dna = make_dna()
    gene = make_signal_gene(indicator=indicator, params=params,
                            condition_type="gt", condition_value=0.0)
    dna.signal_genes = [gene, *dna.signal_genes[1:]]
    return dna


# 三钩子指标违约组合与 violation 字面量锚（各参数自身在分域内、
# 联合违约——逐参数域校验拦不住的形态）
VIOLATIONS = [
    ("GARCHFilter",
     {"alpha": 0.5, "beta": 0.6, "init_window": 500},
     "alpha+beta 须小于 1"),
    ("SessionRange",
     {"start_minute": 1439, "end_minute": 0},
     "start_minute 须小于 end_minute"),
    ("BoxChannel",
     {"edge_window": 240, "q_low": 0.9, "q_high": 0.5, "buffer": 0.25},
     "q_low 须小于 q_high"),
]


class TestCrossParamsIntercepted:
    """违约组合经 dna_to_signal_set 主入口 → RESEARCH_CONDITION_INVALID
    收编（detail.cause 携 violation 全文——修复前该路径静默现算无意义
    结果）。"""

    @pytest.mark.parametrize(
        ("indicator", "params", "violation_sub"),
        VIOLATIONS,
        ids=["garch", "session", "boxch"],
    )
    def test_rejected_with_violation_cause(
            self, indicator, params, violation_sub):
        df = make_enhanced_df(n=700)
        with pytest.raises(ResearchError) as ei:
            dna_to_signal_set(_dna_with_entry_gene(indicator, params), df)
        assert ei.value.code == "RESEARCH_CONDITION_INVALID"
        assert ei.value.detail["indicator"] == indicator
        cause = ei.value.detail["cause"]
        assert cause.startswith(
            f"Cannot resolve/compute indicator {indicator}")
        assert violation_sub in cause, \
            "cause 须携带注册表钩子 violation 全文"


class TestLegalPathNotDisturbed:
    """修复不误伤：合法组合现算正常；str 形参数（手工 DNA 字符串
    数字）float 归一后与数值形产出逐位一致；畸形参数归 bad_param_key
    文案族（A5 四格锚不受扰）。"""

    def test_legal_garch_computes_normally(self):
        """合法 GARCHFilter（默认冻结参数）经取列路径现算——cond_vol
        列产出且非全 NaN（拦截只对违约组合生效）。"""
        df = make_enhanced_df(n=700)
        gene = make_signal_gene(
            indicator="GARCHFilter",
            params={"alpha": 0.08, "beta": 0.90, "init_window": 500},
            condition_type="gt", condition_value=0.0)
        col = _get_indicator_column(df, gene)
        assert col.name == "garch_0.08_0.9_500_cond_vol"
        assert col.notna().sum() > 200, "现算产出非空（检出力）"
        assert (col.dropna() > 0).all(), "cond_vol 连续正值列"

    def test_string_digit_params_equivalent(self):
        """str 形参数（手工 DNA 形态）float 归一后与数值形产出逐位
        一致——归一只改类型不改数值语义（误拒与数值漂移双向防）。"""
        df = make_enhanced_df(n=700)
        gene_num = make_signal_gene(
            indicator="GARCHFilter",
            params={"alpha": 0.08, "beta": 0.90, "init_window": 500},
            condition_type="gt", condition_value=0.0)
        gene_str = make_signal_gene(
            indicator="GARCHFilter",
            params={"alpha": "0.08", "beta": "0.90",
                    "init_window": "500"},
            condition_type="gt", condition_value=0.0)
        clear_indicator_cache()
        col_num = _get_indicator_column(df, gene_num)
        clear_indicator_cache()
        col_str = _get_indicator_column(df, gene_str)
        pd.testing.assert_series_equal(col_num, col_str)

    def test_malformed_param_bad_key_message(self):
        """非数字参数（float 归一失败）归 _bad_param_key_message 文案
        族（「bad or missing parameter key」尾段——A5 四格完备性锚
        断言面，resolve/现算两段同源）。"""
        df = make_enhanced_df(n=700)
        with pytest.raises(ResearchError) as ei:
            dna_to_signal_set(_dna_with_entry_gene(
                "GARCHFilter",
                {"alpha": "abc", "beta": 0.90, "init_window": 500}), df)
        assert ei.value.code == "RESEARCH_CONDITION_INVALID"
        assert ei.value.detail["cause"].endswith(
            "bad or missing parameter key")


class TestHookParityAcrossPaths:
    """S-1 核心承诺「三条取列路径同钩同判」的性质面（阶段四属性补强）：
    研究链 validate_params / column_ref._resolve_indicator_ref 与策略链
    _get_indicator_column 对同一参数组的拒/过判定恒一致——样本含
    合法/违约/恰等 1.0 边界混合（固定样例可复现）。"""

    # (alpha, beta, 违约)——恰等 1.0 三组（上/中/下界分布）＋越界两组
    # ＋合法四组（含 0.99 紧贴界内）。前提：样本组均在各自分域内
    # （alpha∈[0.01,0.5]、beta∈[0.5,0.99]）——研究链入口有逐参数域
    # 校验而策略链取列路径不做（既有语义，域外值属研究链拦截面），
    # 三面同判性质在「分域内、联合越界」的样本空间上成立
    CASES = [
        (0.08, 0.90, False),
        (0.20, 0.79, False),
        (0.02, 0.97, False),
        (0.25, 0.74, False),
        (0.50, 0.50, True),
        (0.01, 0.99, True),
        (0.30, 0.70, True),
        (0.50, 0.60, True),
        (0.40, 0.80, True),
    ]

    @pytest.fixture(scope="class")
    def shared_df(self):
        return make_enhanced_df(n=700)

    @pytest.mark.parametrize(
        ("a", "b", "violating"), CASES,
        ids=[f"a{a}_b{b}_{'reject' if v else 'pass'}"
             for a, b, v in CASES])
    def test_three_paths_agree(self, shared_df, a, b, violating):
        params = {"alpha": a, "beta": b, "init_window": 500}

        # 面一 研究链 validate_params
        from core.analysis.indicator_query import validate_params
        research = False
        try:
            validate_params("GARCHFilter", params)
        except ResearchError as exc:
            research = exc.code == "RESEARCH_PARAM_INVALID"

        # 面二 研究链 column_ref 解析
        from core.analysis.column_ref import (
            ColumnRefError, _resolve_indicator_ref)
        resolve = False
        try:
            _resolve_indicator_ref({
                "indicator": "GARCHFilter", "params": params,
                "field": "cond_vol"})
        except ColumnRefError as exc:
            resolve = exc.code == "PARAM_INVALID"

        # 面三 策略链取列（CARD-02 堵口路径）
        gene = make_signal_gene(
            indicator="GARCHFilter", params=params,
            condition_type="gt", condition_value=0.0)
        clear_indicator_cache()
        executor_rejected = False
        try:
            col = _get_indicator_column(shared_df, gene)
            assert col.notna().sum() > 0, \
                "合法组现算须产出非空列（过≠空转）"
        except ValueError as exc:
            executor_rejected = "Cannot resolve/compute" in str(exc)

        assert research == resolve == executor_rejected == violating, \
            f"三面判定不一致: research={research} resolve={resolve} " \
            f"executor={executor_rejected} 预期违约={violating}"
