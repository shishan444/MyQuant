"""出场组内 OR 通道——回测行为锚与混组边界（add-exit-group-or 任务 4.1/4.2）。

引擎/装配层零改动（D7）——本文件全部为既有算子的行为级验收：
- 4.1 三态：exit_logic="or" 翻译产物在合成行情下「条件 A 触发 bar
  离场/条件 B 触发 bar 离场/双触发 bar 离场」；"and" 产物与无键产物
  信号输出逐位一致；零改动双证（git diff 文件清单断言）
- 4.2 混组四例：then 同含内置与插件列条件且 exit_logic="or"——
  语义=(内置1 OR 内置2) AND (插件A AND 插件B)（signal_assembly
  _combine 既有分桶结构的自然结果，零实现改动）

断言面说明：assemble_signal_set 是回测引擎的信号求值层（validate-
backtest L3 链路内部消费同一 SignalSet.exits）——exits 布尔即回测
离场行为的决定输入，行为锚在此层与回测层等价。
"""

import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.executor import ensure_research_tables
from core.analysis.hypothesis_service import register_hypothesis
from core.analysis.hypothesis_to_strategy import translate_hypothesis
from core.analysis.plugin_service import register_formula_plugin
from core.analysis.signal_assembly import assemble_signal_set
from core.persistence.db_ext import init_db_ext
from core.persistence.repository.strategy import get_strategy
from core.strategy.dna import StrategyDNA

N = 40


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    ensure_research_tables(d)
    return d


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    """translate 公式组件试算装载目录（占位行情——试算只求可执行）。"""
    d = tmp_path / "market"
    d.mkdir()
    _df_level(75000.0).to_parquet(d / "BTCUSDT_4h.parquet")
    return d


def _df_level(close_level: float, *, shock_at: int | None = None,
              shock_hi: float = 130000.0, shock_lo: float = 20000.0,
              n: int = N, start: str = "2024-01-01") -> pd.DataFrame:
    """合成行情：close 恒定平台（低波动），可选单根巨震（high/low
    拉开、close 不动——ATR 触发而价格类指标不动）。"""
    idx = pd.date_range(start, periods=n, freq="4h", tz="UTC",
                        name="timestamp")
    close = np.full(n, float(close_level))
    high = close + 100.0
    low = close - 100.0
    if shock_at is not None:
        high[shock_at] = shock_hi
        low[shock_at] = shock_lo
    return pd.DataFrame({
        "open": close, "high": high, "low": low, "close": close,
        "volume": np.full(n, 500.0), "trades": np.full(n, 40),
    }, index=idx)


def _ranges() -> list:
    return [{"symbol": "BTCUSDT", "timeframe": "4h",
             "start": "2024-01-01T00:00:00Z", "end": "2024-01-05T00:00:00Z"}]


def _register_and_translate(db, data_dir, *, then, exit_logic=None):
    """登记（when 占位可表达条件）→ 翻译 → 产物 StrategyDNA。"""
    conditions = {
        "when_conditions": [
            {"subject": {"indicator": "SMA", "params": {"period": 5},
                         "field": "sma"}, "action": "lt", "target": 61000}],
        "then_conditions": then,
    }
    h = register_hypothesis(
        db, conditions=conditions, statement="回测行为锚",
        data_ranges=_ranges(), strategy_params={"stop_loss": 0.05},
        exit_logic=exit_logic)
    t = translate_hypothesis(db, hypothesis_id=h["hypothesis_id"],
                             data_dir=data_dir)
    dna_json = get_strategy(db, t["strategy_id"])["dna_json"]
    return StrategyDNA.from_json(dna_json)


# 出场两条件（正交可控对）：A=价格面 SMA(5)、B=波动面 ATR(14)
_COND_A = {"subject": {"indicator": "SMA", "params": {"period": 5},
                       "field": "sma"}, "action": "gt", "target": 95000}
_COND_B = {"subject": {"indicator": "ATR", "params": {"period": 14},
                       "field": "atr"}, "action": "gt", "target": 4000}


class TestBacktestThreeStates:
    """4.1①：or 产物三态离场（只 A/只 B/双触发任一触发即离场）。"""

    def test_only_a_triggers_exits(self, db, data_dir):
        """态一（只 A）：close=100000 平台低波动——SMA5>95000 ✓、
        ATR ✗ → or 产物该 bar 离场；and 产物不离场（交集语义对照）。"""
        dna_or = _register_and_translate(
            db, data_dir, then=[_COND_A, _COND_B], exit_logic="or")
        dna_and = _register_and_translate(
            db, data_dir, then=[_COND_A, _COND_B])
        df = _df_level(100000.0)
        exits_or = assemble_signal_set(db, dna_or, df).exits
        exits_and = assemble_signal_set(db, dna_and, df).exits
        # SMA5 需 5 根平台——bar 5 起为真
        assert bool(exits_or.iloc[10]) is True
        assert bool(exits_and.iloc[10]) is False

    def test_only_b_triggers_exits(self, db, data_dir):
        """态二（只 B）：close=75000 平台 bar 25 单根巨震——ATR ✓、
        SMA5 ✗ → or 产物该 bar 离场；and 产物不离场。"""
        dna_or = _register_and_translate(
            db, data_dir, then=[_COND_A, _COND_B], exit_logic="or")
        dna_and = _register_and_translate(
            db, data_dir, then=[_COND_A, _COND_B])
        df = _df_level(75000.0, shock_at=25)
        exits_or = assemble_signal_set(db, dna_or, df).exits
        exits_and = assemble_signal_set(db, dna_and, df).exits
        assert bool(exits_or.iloc[25]) is True
        assert bool(exits_and.iloc[25]) is False
        # 平台段无触发（ATR 平台低位、SMA5 低位）
        assert not exits_or.iloc[:24].any()

    def test_both_trigger_exits(self, db, data_dir):
        """态三（双触发）：close=130000 平台 bar 30 巨震——SMA5 ✓ 且
        ATR ✓ → or 与 and 产物同 bar 离场。"""
        dna_or = _register_and_translate(
            db, data_dir, then=[_COND_A, _COND_B], exit_logic="or")
        dna_and = _register_and_translate(
            db, data_dir, then=[_COND_A, _COND_B])
        df = _df_level(130000.0, shock_at=30, shock_hi=200000.0,
                       shock_lo=60000.0)
        exits_or = assemble_signal_set(db, dna_or, df).exits
        exits_and = assemble_signal_set(db, dna_and, df).exits
        assert bool(exits_or.iloc[30]) is True
        assert bool(exits_and.iloc[30]) is True

    def test_neither_triggers_no_exit(self, db, data_dir):
        """边界对照（两者缺）：close=75000 平台无巨震——全 bar 不离场。"""
        dna_or = _register_and_translate(
            db, data_dir, then=[_COND_A, _COND_B], exit_logic="or")
        df = _df_level(75000.0)
        exits = assemble_signal_set(db, dna_or, df).exits
        assert not exits.any()


class TestAndFormBitwiseIdentical:
    """4.1②：「and」产物与无键产物信号输出逐位一致（全通道）。"""

    def test_and_vs_missing_all_channels(self, db, data_dir):
        dna_and = _register_and_translate(
            db, data_dir, then=[_COND_A, _COND_B], exit_logic="and")
        dna_plain = _register_and_translate(
            db, data_dir, then=[_COND_A, _COND_B])
        for df in (_df_level(100000.0),
                   _df_level(75000.0, shock_at=25),
                   _df_level(130000.0, shock_at=30, shock_hi=200000.0,
                             shock_lo=60000.0)):
            ss_and = assemble_signal_set(db, dna_and, df)
            ss_plain = assemble_signal_set(db, dna_plain, df)
            for ch in ("entries", "exits", "adds", "reduces"):
                assert list(getattr(ss_and, ch)) == \
                    list(getattr(ss_plain, ch)), f"{ch} 通道逐位一致"


class TestZeroChangeProof:
    """4.1③零改动双证之一：变更集文件清单断言（既有 executor/装配
    测试全绿由 7.1 全量回归与局部跑承载）。"""

    def test_git_diff_excludes_engine_and_assembly(self):
        """本 change 变更集不含 core/strategy/executor.py 与
        core/analysis/signal_assembly.py（D7 引擎/装配零改动）。"""
        repo = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            ["git", "diff", "--name-only", "main...HEAD"],
            cwd=repo, capture_output=True, text=True, check=True)
        changed = {line for line in result.stdout.splitlines() if line}
        assert "core/strategy/executor.py" not in changed
        assert "core/analysis/signal_assembly.py" not in changed


class TestMixedGroupBoundary:
    """4.2 混组四例：(内置1 OR 内置2) AND (插件A AND 插件B)。

    内置1=SMA(5) gt 120000（价格面）、内置2=ATR(14) gt 4000（波动面）；
    插件A=fx_pa（close/60000）gt 1.2、插件B=fx_pb（close/80000）
    gt 1.1——close 单变量控制四态。
    """

    @pytest.fixture()
    def mixed_dnas(self, db, data_dir):
        col_a = register_formula_plugin(
            db, name="pa", expression="close / 60000")["column"]
        col_b = register_formula_plugin(
            db, name="pb", expression="close / 80000")["column"]
        then = [
            {"subject": {"indicator": "SMA", "params": {"period": 5},
                         "field": "sma"}, "action": "gt", "target": 120000},
            {"subject": {"indicator": "ATR", "params": {"period": 14},
                         "field": "atr"}, "action": "gt", "target": 4000},
            {"subject": col_a, "action": "gt", "target": 1.2},
            {"subject": col_b, "action": "gt", "target": 1.1},
        ]
        dna_or = _register_and_translate(
            db, data_dir, then=then, exit_logic="or")
        return dna_or, col_a, col_b

    def test_builtin_triggers_plugin_incomplete_no_exit(
            self, db, data_dir, mixed_dnas):
        """例①内置触发（ATR 路径）插件不全→不离场：close=75000
        平台 bar 25 巨震——内置2 ✓ 内置1 ✗（内置 OR ✓）；插件A
        1.25>1.2 ✓ 插件B 0.94<1.1 ✗（插件 AND ✗）→ 全序列不离场。"""
        dna_or, _, _ = mixed_dnas
        df = _df_level(75000.0, shock_at=25)
        assert not assemble_signal_set(db, dna_or, df).exits.any()

    def test_builtin_not_trigger_plugin_complete_no_exit(
            self, db, data_dir, mixed_dnas):
        """例②内置不触发插件全真→不离场：close=100000 平台低波动
        ——内置1 SMA5=100000<120000 ✗ 内置2 ATR ✗（内置 OR ✗）；
        插件A 1.67 ✓ 插件B 1.25 ✓（插件 AND ✓）→ OR 不越界跨源。"""
        dna_or, _, _ = mixed_dnas
        df = _df_level(100000.0)
        assert not assemble_signal_set(db, dna_or, df).exits.any()

    def test_both_sides_trigger_exits(self, db, data_dir, mixed_dnas):
        """例③两者齐→离场（两条内置达成路径均验证）：前 25 根
        close=75000、后 15 根 130000——bar 25 段界跳空触发内置2
        （ATR 单根拉高）且插件齐→离场；bar 26-28 ATR 回落且 SMA5
        未成→不离场；bar 29 起 SMA5（第 5 根高位）✓ 插件齐→离场。"""
        dna_or, _, _ = mixed_dnas
        df = pd.concat([_df_level(75000.0, n=25),
                        _df_level(130000.0, n=15,
                                  start="2024-01-05T08:00:00")])
        exits = assemble_signal_set(db, dna_or, df).exits
        # 路径一（bar 25）：段界跳空 → ATR ✓ + 插件齐 → 离场
        assert bool(exits.iloc[25]) is True
        # 中间段（bar 26-28）：ATR 回落、SMA5 含低位样本 → 不离场
        assert not exits.iloc[26:29].any()
        # 路径二（bar 29 起）：SMA5 ✓ + 插件齐 → 离场
        assert bool(exits.iloc[29]) is True

    def test_neither_side_triggers_no_exit(self, db, data_dir, mixed_dnas):
        """例④两者缺→不离场：close=50000 平台低波动——内置 ✗、
        插件A 0.83 ✗ 插件B 0.63 ✗。"""
        dna_or, _, _ = mixed_dnas
        df = _df_level(50000.0)
        assert not assemble_signal_set(db, dna_or, df).exits.any()

    def test_mixed_group_plugin_role_encoded(self, db, data_dir, mixed_dnas):
        """结构锚：混组产物插件源 role=exit（非 entry 桶——R2 多角色
        分桶通道），插件源进 signal_sources 参与 src: 段签名。"""
        dna_or, col_a, col_b = mixed_dnas
        exit_roles = [s.get("role") for s in dna_or.signal_sources
                      if s.get("type") == "formula"]
        assert exit_roles == ["exit", "exit"]
        sig = dna_or.gene_signature
        assert col_a in sig and col_b in sig
