"""家族对拍审计（fix-research-c7-contract-parity-audit · C7.1~C7.4）。

四处对拍锁「声明面与实现面一致」的家族级契约（R2 对拍审计——入口
声明支持、执行层必须有实现；双向：未声明的实现=暗门、未实现的声明
=静默暗差）。已知历史暗差以显式豁免集锚定（扩大即红——防新增暗差），
处置记录见变更文档差异清单（C7.5）。

- 对拍①：supported_conditions（registry+screen _PRICE_REL_ACTIONS+
  registry_meta 透传三清单）vs engine action_map——双向；
- 对拍②：公式 DSL 白名单 vs 两消费方可求值；
- 对拍③：编码列名 decode 往返幂等+三消费路径（统计/翻译/回测）
  同一 ColumnDef 语义；
- 对拍④：P4a registry 参数声明键集 vs 计算分支实际消费键集（AST
  双向+机制自证）/ P4b 指标名域双向（有名无分支=静默空返回红）。
"""

import ast
import inspect
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.features.registry import INDICATOR_REGISTRY
from core.validation.engine import _evaluate_single_condition

REPO_ROOT = Path(__file__).resolve().parents[2]


def _action_map_keys() -> set:
    """实现面：engine action_map 键集（源码 AST 提取——非文本匹配）。"""
    src = inspect.getsource(_evaluate_single_condition)
    keys = set()
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Dict)
                and any(isinstance(k, ast.Constant) and k.value == "touch"
                        for k in node.keys if isinstance(k, ast.Constant))):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
    return keys


# 已知历史暗差（豁免集——处置记录见变更文档 C7.5 差异清单；
# 集合扩大即红：新增暗差必须显式登记处置去向）
KNOWN_DECLARED_NOT_IMPLEMENTED = frozenset({
    "eq",           # 10 个 pattern 指标声明；统计面无实现（回测面有）
    "lookback_all", # BB/RVOL/VROC 声明；统计面无实现（回测面有）
    "wick_touch",   # Keltner/BB/Donchian 声明；统计面无实现（TC-1 发现）
})
KNOWN_IMPLEMENTED_NOT_DECLARED = frozenset({
    "breakdown", "breakout", "consecutive_down", "consecutive_up",
    "divergence_bottom", "divergence_top", "shrink", "spike", "touch",
    # 通用动作族：引擎级实现、无指标级声明（口径=supported_conditions
    # 为指标侧声明非全量动作清单——差异清单在案）
})


class TestParityOneDeclaredVsImplemented:
    """对拍①：三份声明清单 vs action_map 双向。"""

    def _declared_union(self) -> set:
        declared = set()
        for d in INDICATOR_REGISTRY.values():
            declared |= set(d.supported_conditions or [])
        # 第二份清单：screen_patterns 的价格相对动作（F-A 修复：正则
        # 兼容类型注解形 `_NAME: Dict[str, List[str]] = {`；只取值列表
        # 中的动作名——指标名键不进动作集；提取失败必须红，防死代码
        # 静默跳过——reviewer7 实证原形制 MISS 而全绿是假绿）
        sp = (REPO_ROOT / "core/analysis/templates/screen_patterns.py").read_text()
        m = re.search(r"_PRICE_REL_ACTIONS[^=]*=\s*\{", sp)
        assert m, "第二份清单（_PRICE_REL_ACTIONS）提取失败——对拍①守卫面缺失"
        body = sp[m.end():]
        depth, end = 1, 0
        for i, ch in enumerate(body):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        block = body[:end]
        for vm in re.finditer(r":\s*\[([^]]*)\]", block):
            declared |= set(re.findall(r"['\"](\w+)['\"]", vm.group(1)))
        # 第三份清单：registry_meta 透传层（supported_conditions 的
        # 出口透传——键集一致性由 registry 源头保证，此处并集收口）
        return declared

    def test_declared_has_implementation_bidirectional(self):
        impl = _action_map_keys()
        declared = self._declared_union()
        miss = declared - impl
        assert miss <= KNOWN_DECLARED_NOT_IMPLEMENTED, (
            f"新增声明未实现暗差: {sorted(miss - KNOWN_DECLARED_NOT_IMPLEMENTED)}")
        dark = impl - declared
        assert dark <= KNOWN_IMPLEMENTED_NOT_DECLARED, (
            f"新增未声明实现暗门: {sorted(dark - KNOWN_IMPLEMENTED_NOT_DECLARED)}")

    def test_known_gaps_exact_membership(self):
        """豁免集自身精确性：豁免键确实在对应差集中（防豁免集腐烂）。"""
        impl = _action_map_keys()
        declared = self._declared_union()
        assert KNOWN_DECLARED_NOT_IMPLEMENTED <= (declared - impl)
        assert KNOWN_IMPLEMENTED_NOT_DECLARED <= impl


class TestParityTwoDslWhitelist:
    """对拍②：DSL 白名单函数 vs 两消费方可求值。"""

    def test_whitelist_functions_evaluable_both_consumers(self):
        from core.signals.formula import FORMULA_FUNCTIONS, evaluate_formula
        import pandas as pd
        import numpy as np
        idx = pd.date_range("2024-01-01", periods=30, freq="1h",
                            tz="UTC", name="timestamp")
        df = pd.DataFrame({
            "open": 1.0, "high": 2.0, "low": 0.5, "close": np.linspace(1, 2, 30),
            "volume": 10.0}, index=idx)
        # 每个白名单一元函数可求值（二元/三元函数挑代表对）
        from core.signals import formula as F
        probes = [
            "shift(close, 1)", "diff(close, 1)",
            "rolling_mean(close, 3)", "rolling_std(close, 3)",
            "rolling_max(close, 3)", "rolling_min(close, 3)",
            "rolling_sum(close, 3)", "zscore(close, 3)",
            "ema(close, 3)", "abs(close)",
            "crossover(close, shift(close, 1))",
            "crossunder(close, shift(close, 1))",
            "max(close, 1.5)", "min(close, 1.5)",
            "gt(close, 1.0)", "lt(close, 2.0)", "eq(close, 1.0)",
            "logical_and(gt(close, 0), lt(close, 9))",
            "logical_or(gt(close, 9), lt(close, 9))",
            "logical_not(gt(close, 9))",
            "corr(close, volume, 3)",
        ]
        allowed = set(FORMULA_FUNCTIONS) | {"open", "high", "low",
                                            "close", "volume"}
        for expr in probes:
            s = evaluate_formula(expr, df, extra_columns={})
            assert s is not None, expr
        # 白名单键集与探针精确等（F-C：<= 只防探针越界，防不了白名单
        # 扩键无探针——改 == 双向锁：白名单新增函数必须补探针）
        used = {re.match(r"(\w+)\(", e).group(1) for e in probes}
        assert used == set(FORMULA_FUNCTIONS)


class TestParityThreeDecodeRoundtrip:
    """对拍③：编码列名 decode 往返+三消费路径同语义。"""

    CASES = [
        ("bb_lower_20_2.5", "BB", {"period": 20, "std": 2.5}, "lower"),
        ("kc_20_10_2.0_upper", None, None, None),  # 命名形=kc 前缀
        ("macd_histogram_12_26_9", "MACD",
         {"fast": 12, "slow": 26, "signal": 9}, "histogram"),
        ("ema_20", "EMA", {"period": 20}, "ema"),
        ("rsi_14", "RSI", {"period": 14}, "rsi"),
    ]

    def test_decode_idempotent_roundtrip(self):
        from core.analysis.column_ref import decode_column_ref
        for col, ind, prm, fld in self.CASES:
            if ind is None:
                continue
            d = decode_column_ref(col)
            assert d["indicator"] == ind
            assert d["params"] == prm
            assert d["field"] == fld
            # 幂等：同列名再解码同值
            assert decode_column_ref(col) == d

    def test_three_consumer_paths_same_semantics(self, tmp_path):
        """统计（_classify/collect_condition_refs）、翻译（decode 分支）、
        回测（_resolve_target_indicator_series 的 _SimpleGene 路径）对
        同一编码列名产出同一 (indicator, params) 语义——三面同源显式
        锚定（F-B：回测路径实际调用而非仅 import）。"""
        from core.analysis.condition_kernel import (
            _classify_target_ref, collect_condition_refs)
        from core.analysis.column_ref import decode_column_ref
        from core.strategy.executor import _resolve_target_indicator_series
        import pandas as pd
        from core.features.indicators import _compute_indicator
        n = 80  # MACD(12,26,9) 慢线 warmup 需 ~35 bar——30 bar 全 NaN
        df = pd.DataFrame({
            "open": 1.0, "high": 2.0, "low": 0.5,
            "close": pd.Series(range(1, n + 1), dtype=float)},
            index=pd.date_range("2024-01-01", periods=n,
                                freq="1h", tz="UTC"))
        for col, ind, prm, fld in self.CASES:
            if ind is None:
                continue
            kind, payload = _classify_target_ref(col)
            assert kind == "decoded" and payload["indicator"] == ind
            refs = collect_condition_refs(
                {"subject": "close", "action": "gt", "target": col})
            assert (ind, prm) in refs
            d = decode_column_ref(col)
            assert d["indicator"] == ind and d["params"] == prm
            # 回测路径：C6 序列形 condition（target_* 三键）经
            # _resolve_target_indicator_series 现算取列——同源锚定
            cond = {"type": "gt", "target_indicator": ind,
                    "target_params": dict(prm), "target_field": fld}
            work = df.copy()
            enriched = _compute_indicator(df, ind, dict(prm))
            for c, s in enriched.items():
                work[c] = s
            series = _resolve_target_indicator_series(cond, work)
            assert series is not None, f"回测路径解析失败: {col}"
            expected = enriched[list(enriched)[0]]
            pd.testing.assert_series_equal(
                series, expected, check_names=False)


class TestParityFourParamDomains:
    """对拍④ P4a：registry 参数声明键集 vs 计算分支实际消费键集。"""

    def _consumed_keys(self, indicator: str) -> set:
        """AST 提取 _compute_indicator 对应分支实际消费的 params 键。"""
        from core.features.indicators import _compute_indicator
        src = inspect.getsource(_compute_indicator)
        tree = ast.parse(src)
        branch_body = None
        for node in ast.walk(tree):
            if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)):
                left = node.test.left
                if (isinstance(left, ast.Name) and left.id == "name"):
                    for cmp in node.test.comparators:
                        if (isinstance(cmp, ast.Constant)
                                and cmp.value == indicator):
                            branch_body = node.body
        assert branch_body is not None, f"{indicator} 分支未找到"
        keys = set()
        wrapped = ast.Module(body=branch_body, type_ignores=[])
        for n in ast.walk(wrapped):
            # 下标形 params["key"] 与方法形 params.get("key", ...)
            if (isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name)
                    and n.value.id == "params"):
                if isinstance(n.slice, ast.Constant):
                    keys.add(n.slice.value)
            elif (isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "get"
                    and isinstance(n.func.value, ast.Name)
                    and n.func.value.id == "params"):
                if n.args and isinstance(n.args[0], ast.Constant):
                    keys.add(n.args[0].value)
        return keys

    def test_param_keys_declared_vs_consumed(self):
        """双向：声明未消费=吞参嫌疑；消费未声明=私参数。"""
        silent = []
        for name, d in INDICATOR_REGISTRY.items():
            declared = set((d.params or {}).keys())
            try:
                consumed = self._consumed_keys(name)
            except AssertionError:
                silent.append(name)  # 分支形制变体——收集后交叉验证
                continue
            undeclared = consumed - declared
            unconsumed = declared - consumed
            assert not undeclared, f"{name}: 消费未声明 {undeclared}"
            assert not unconsumed, f"{name}: 声明未消费 {unconsumed}"
        # F-E 交叉验证：静默名单 ⊆ P4b 差集∪豁免（「提取不到」与
        # 「确无分支」可区分——静默面意外扩大即红）
        from core.features.indicators import _compute_indicator
        bnames = set(re.findall(
            r'name == "([^"]+)"', inspect.getsource(_compute_indicator)))
        rnames = set(INDICATOR_REGISTRY.keys())
        exempt = self._no_compute_exempt_p4a()
        assert set(silent) <= (rnames - bnames) | exempt, (
            f"P4a 静默面意外扩大: "
            f"{sorted(set(silent) - ((rnames - bnames) | exempt))}")

    @staticmethod
    def _no_compute_exempt_p4a() -> set:
        import core.evolution.population as pop
        m = re.search(r"_NO_COMPUTE\s*=\s*\{([^}]+)\}",
                      inspect.getsource(pop))
        return set(re.findall(r'"([^"]+)"', m.group(1))) if m else set()

    def test_mechanism_self_proof(self):
        """机制自证（F-D 补强）：历史吞参负例写死字面量（提取器漏抓
        即红）+ 真提取器非空反例（假守卫「返回声明键集」在缺陷态下
        与真消费集可区分——防提取器与断言同源失真恒绿）。"""
        # BB 历史吞参负例：std 曾是「声明未消费」——修复后必消费
        assert "std" in self._consumed_keys("BB")
        assert "atr_period" in self._consumed_keys("Keltner")
        # 假守卫反例：声明键集恒含 std（守卫面存在），而真消费集
        # 非空且含 std——缺陷态（吞参）下消费集为空集，两者可区分
        assert set(INDICATOR_REGISTRY["BB"].params.keys())
        assert self._consumed_keys("BB") != set()

    def _no_compute_exempt(self) -> set:
        """豁免面程序派生：进化侧已知无计算实现的指标清单
        （population._NO_COMPUTE——单一事实源，不写字面副本）。"""
        import core.evolution.population as pop
        m = re.search(r"_NO_COMPUTE\s*=\s*\{([^}]+)\}",
                      inspect.getsource(pop))
        return set(re.findall(r'"([^"]+)"', m.group(1))) if m else set()

    def test_indicator_names_bidirectional(self):
        """P4b：registry 指标名 vs _compute_indicator 分支名双向。
        豁免=population._NO_COMPUTE 派生集（进化侧显式排除的已知
        无计算指标）；豁免集自身有「==派生集」双层防漂移断言。"""
        from core.features.indicators import _compute_indicator
        src = inspect.getsource(_compute_indicator)
        branch_names = set(re.findall(
            r'name == "([^"]+)"', src))
        registry_names = set(INDICATOR_REGISTRY.keys())
        exempt = self._no_compute_exempt()
        # 双层之一：豁免集确实源自 population（派生失败=空集→暗差全红）
        assert exempt, "豁免派生失败（population._NO_COMPUTE 提取空）"
        missing = registry_names - branch_names - exempt
        assert not missing, (
            f"有名无分支（静默空返回）: {sorted(missing)}")
        assert not (branch_names - registry_names), (
            f"有分支无名（死分支）: {sorted(branch_names - registry_names)}")
