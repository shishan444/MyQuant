"""批量评估健康性测试（fix-executor-batch-health 任务 1/2/3）。

覆盖：非法基因形态（条件 target 为 None / 条件含 dict 值）下批量评估
的局部化行为（单基因降级、整批不降级、其余基因正常）；源头校验（进化
循环内非法产物换随机个体、种群不含非法结构）。

复现锚点（修复前红）：`_gene_signature` 对两种实证形态在 Step 1 构造
签名时抛 TypeError 上炸 batch_run → evaluate_population 宽 except 整批
降级逐个体。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

import pandas as pd

from core.strategy.executor import batch_signal_sets, clear_indicator_cache
from tests.helpers.data_factory import (
    make_dna, make_enhanced_df, make_signal_gene,
)
from tests.helpers.data_factory import (
    make_dna, make_enhanced_df, make_signal_gene,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_indicator_cache()
    yield
    clear_indicator_cache()


def _illegal_target_none():
    """实证形态①：条件 threshold 为 None（float vs None 比较炸签名构造）。"""
    gene = make_signal_gene(condition_value=30)
    gene.condition = {"type": "lt", "threshold": None}
    return gene


def _illegal_dict_value():
    """实证形态②：条件含 dict 值（tuple 不可哈希，membership 查询炸）。"""
    gene = make_signal_gene(condition_value=30)
    gene.condition = {"type": "lt", "threshold": {"nested": 30}}
    return gene


class TestBatchLocalizedDegradation:
    """任务 1.1 复现（修复前红）→ 任务 3.2 转绿：非法基因局部降级。"""

    @pytest.mark.parametrize("make_illegal", [
        _illegal_target_none, _illegal_dict_value,
    ], ids=["target_none", "dict_value"])
    def test_illegal_gene_batch_completes(self, make_illegal):
        """非法基因存在时批量评估正常完成——合法个体的结果与单独跑
        逐位一致（不受同批非法基因影响）、整批不降级。"""
        df = make_enhanced_df(n=200)
        bad_gene = make_illegal()

        dna_good = make_dna(entry_value=30, exit_value=70)
        dna_bad = make_dna(entry_value=30, exit_value=70)
        dna_bad.signal_genes = [bad_gene, *dna_bad.signal_genes[1:]]

        results = batch_signal_sets([dna_good, dna_bad], df)
        solo = batch_signal_sets([dna_good], df)

        assert len(results) == 2, "整批完成（修复前：TypeError 上炸）"
        # 合法个体混批与单独跑逐位一致——非法基因不污染同批结果
        pd.testing.assert_series_equal(
            results[0].entries, solo[0].entries, check_names=False)

    def test_illegal_gene_degrades_to_never_trigger(self):
        """非法基因降级为全 False（永不触发）：携带它的个体仍完成组装，
        该基因对信号无贡献（得低分淘汰由评分层负责，非批量层炸断）。"""
        df = make_enhanced_df(n=200)
        dna = make_dna(entry_value=30, exit_value=70)
        dna.signal_genes = [_illegal_target_none(), *dna.signal_genes[1:]]

        results = batch_signal_sets([dna], df)

        assert len(results) == 1
        assert not bool(results[0].entries.any()), "非法入口基因降级全 False"


class TestSourceValidation:
    """任务 2.2：变异产物源头拦截——非法结构不进种群。"""

    def test_validate_offspring_rejects_illegal_gene(self):
        """校验最小集：两种实证形态被拒（异常带原因），合法 DNA 通过。"""
        from core.evolution.engine import validate_offspring

        with pytest.raises(ValueError, match="target"):
            dna = make_dna()
            dna.signal_genes = [_illegal_target_none(), *dna.signal_genes[1:]]
            validate_offspring(dna)

        with pytest.raises(ValueError, match="hash|可哈希"):
            dna = make_dna()
            dna.signal_genes = [_illegal_dict_value(), *dna.signal_genes[1:]]
            validate_offspring(dna)

        validate_offspring(make_dna())  # 合法 DNA 静默通过


class TestFragmentationFix:
    """任务 4.1：按需列写回一次整块赋值——宽表不再触发碎片化警告。"""

    def test_wide_df_on_demand_no_performance_warning(self):
        """宽 enhanced_df（>100 列，过 pandas 碎片化阈值）上按需现算
        指标列：零 PerformanceWarning（修复前逐列 insert 触发），且
        按需列可正常求值（行为等价）。"""
        import warnings

        from core.strategy.executor import _get_indicator_column

        df = make_enhanced_df(n=120)
        for i in range(45):  # 垫列过 100 阈值（72 基础 + 45 垫 = 117 列）
            df[f"pad_{i}"] = float(i)
        gene = make_signal_gene(indicator="RSI", params={"period": 27})  # 非默认→按需

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            df = df.copy()  # 去碎片化基线（垫列的 insert 成本不带入被测路径）
            col = _get_indicator_column(df, gene)
            perf = [x for x in w
                    if x.category.__name__ == "PerformanceWarning"]
        assert not perf, f"碎片化警告应归零，实际 {len(perf)} 条"
        assert col is not None and len(col) == 120, "按需列照常可用"
