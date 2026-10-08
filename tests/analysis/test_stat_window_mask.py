"""condition_kernel 共享样板锚：stat_window_mask 与 p_value_for。

warmup 前缀「只用不算」的统计段切回样板自四分析模板提取至
condition_kernel（与 p_value_for 同居）。行为面由四模板既有特征网
覆盖（机械替换逐字等价）；本文件锚定：① 样板函数正牌行为；
② regime_split None 分支引用变副本的输出冻结（等价性论据的行为
证据——段边界/类型/bars 与显式段判读值全经 stat 读出）；
③ 四模板 import 防漂移（对齐 to_utc/safe_symbol 收敛锚形制）。
"""

import ast
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.condition_kernel import stat_window_mask

_IDX = pd.date_range("2024-01-01", periods=100, freq="1h", tz="UTC",
                     name="timestamp")
_DF = pd.DataFrame({"close": range(100)}, index=_IDX)


class TestStatWindowMaskBehavior:
    def test_None_全True(self) -> None:
        m = stat_window_mask(_DF, None)
        assert m.dtype == bool
        assert len(m) == 100 and m.all()

    def test_UTC字符串_起点含入(self) -> None:
        m = stat_window_mask(_DF, "2024-01-03T02:00:00Z")
        assert int(m.sum()) == 50  # [50, 100) 含起点（>= 语义）

    def test_aware非UTC_与UTC形等价(self) -> None:
        utc = "2024-01-03T02:00:00Z"
        aware = "2024-01-03T08:00:00+06:00"  # 同一绝对时刻
        assert (stat_window_mask(_DF, aware)
                == stat_window_mask(_DF, utc)).all()

    def test_Timestamp直传_与字符串形等价(self) -> None:
        ts = pd.Timestamp("2024-01-03T02:00:00Z")
        assert (stat_window_mask(_DF, ts)
                == stat_window_mask(_DF, "2024-01-03T02:00:00Z")).all()

    def test_掩码直切_与原直切形等价(self) -> None:
        """regime_split 变体形制：df[mask] == df[df.index >= req]。"""
        req = pd.Timestamp("2024-01-03T02:00:00Z")
        cut = _DF[stat_window_mask(_DF, "2024-01-03T02:00:00Z")]
        assert cut.equals(_DF[_DF.index >= req])


class TestRegimeSplitNoneFrozen:
    """CARD-07 等价性证据：None 分支 stat 引用变副本（掩码直切）后
    输出逐字段不变——段边界/类型/bars 与显式段判读值全经 stat 读出。
    冻结值取自 make_two_regime_df 确定性数据（探针 2026-10-01）。"""

    _RANGES = [{"symbol": "TESTUSDT", "timeframe": "1d",
                "start": "2024-01-01", "end": "2025-09-01"}]

    def test_auto段输出冻结(self) -> None:
        from tests.helpers.regime_factory import make_two_regime_df
        from core.analysis.templates.regime_split import regime_split
        p = regime_split(make_two_regime_df(), data_ranges=self._RANGES,
                         timeframe="1d").to_dict()["payload"]
        got = [(s["start"][:10], s["end"][:10], s["type"], s["bars"])
               for s in p["segments"]]
        assert got == [
            ("2024-01-01", "2024-07-11", "趋势", 193),
            ("2024-07-12", "2024-08-08", "高波动", 28),
            ("2024-08-09", "2024-11-25", "趋势", 109),
            ("2024-11-26", "2024-12-19", "高波动", 24),
            ("2024-12-20", "2025-03-08", "趋势", 79),
            ("2025-03-09", "2025-08-12", "震荡", 157),
        ]

    def test_explicit段判读值冻结(self) -> None:
        from tests.helpers.regime_factory import make_two_regime_df
        from core.analysis.templates.regime_split import regime_split
        df = make_two_regime_df()
        p = regime_split(
            df, data_ranges=self._RANGES, timeframe="1d",
            segmentation=[
                {"start": str(df.index[50].date()),
                 "end": str(df.index[250].date())},
                {"start": str(df.index[300].date()),
                 "end": str(df.index[500].date()), "type": "趋势"},
            ]).to_dict()["payload"]
        got = [(s["adx_mean"], s["atr_pct_mean"], s["bars"])
               for s in p["segments"]]
        assert got == [(100.0, 0.72, 201), (54.07, 1.59, 201)]


class TestSingleSourceDrift:
    """四模板必须 import stat_window_mask（内联回潮即红）。"""

    def test_四模板导入防漂移(self) -> None:
        import core.analysis.templates  # noqa: F401 触发包初始化
        mods = [sys.modules[f"core.analysis.templates.{name}"]
                for name in ("condition_stats", "param_sweep",
                             "regime_split", "screen_patterns")]
        for mod in mods:
            tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
            has = any(
                isinstance(n, ast.ImportFrom)
                and n.module == "core.analysis.condition_kernel"
                and any(a.name == "stat_window_mask" for a in n.names)
                for n in ast.walk(tree))
            assert has, (
                f"{mod.__name__} 未自 condition_kernel 导入 "
                "stat_window_mask——样板内联回潮")


class TestPValueForBehavior:
    """CARD-08 配套：p_value_for 直测（四消费方共享——condition_
    stats/param_sweep/screen_patterns/regime_split；此前全域无直测，
    行为面仅靠消费方间接覆盖）。

    baseline None 边界说明：kernel 返回 (None, 0)、condition_stats
    旧内联返回 (None, len(subset))——真实耦合下 stat_mask ⊆ in_stat
    且 baseline None ⟺ 统计段 fwd 全 NaN ⟹ subset 必空，两值恒等；
    且该形态下 condition_stats 的 sample_size=0 先被小样本闸门拒，
    forward_significance 输出面不可达（探针实证）。
    """

    _IDX = pd.date_range("2024-01-01", periods=100, freq="1h", tz="UTC",
                         name="timestamp")

    def _fwd(self, values):
        return pd.Series(values, index=self._IDX)

    def test_baseline为None_返回None零(self) -> None:
        from core.analysis.condition_kernel import p_value_for
        mask = np.ones(100, dtype=bool)
        p, n = p_value_for(self._fwd(np.linspace(1, 2, 100)), mask,
                            4, None)
        assert p is None and n == 0

    def test_正常形态_返回p值与去重叠样本数(self) -> None:
        from core.analysis.condition_kernel import p_value_for
        rng = np.random.default_rng(3)
        mask = np.zeros(100, dtype=bool)
        mask[::10] = True  # 10 个信号，间隔 10 >= horizon 4 全入选
        fwd = self._fwd(rng.normal(0.01, 0.02, 100))
        p, n = p_value_for(fwd, mask, 4, 0.0)
        assert 0.0 <= p <= 1.0
        assert n == 10

    def test_去重叠贪心_间隔不足horizon的信号跳过(self) -> None:
        from core.analysis.condition_kernel import p_value_for
        mask = np.zeros(100, dtype=bool)
        mask[[10, 11, 12, 40, 41]] = True  # 贪心选 10、40（间隔<4 跳过）
        fwd = self._fwd(np.linspace(1, 2, 100))
        _, n = p_value_for(fwd, mask, 4, 0.5)
        assert n == 2

    def test_样本不足两成_返回None带样本数(self) -> None:
        from core.analysis.condition_kernel import p_value_for
        mask = np.zeros(100, dtype=bool)
        mask[50] = True  # 单信号
        fwd = self._fwd(np.linspace(1, 2, 100))
        p, n = p_value_for(fwd, mask, 4, 0.5)
        assert p is None and n == 1

    def test_零方差_返回None带样本数(self) -> None:
        from core.analysis.condition_kernel import p_value_for
        mask = np.ones(100, dtype=bool)
        fwd = self._fwd(np.full(100, 1.0))  # 恒定值零方差
        p, n = p_value_for(fwd, mask, 4, 0.5)
        # n = 去重叠子集大小：全 True 掩码贪心选 0/4/8/...（horizon 4）
        assert p is None and n == 25


class TestCoreInvariants:
    """阶段四属性补强：核心计算面不变量（一类输入应保持的关系）。"""

    def test_hash_json_注入区分与规模无关(self) -> None:
        """属性：不同输入不同指纹（区分性）；嵌套深度不影响稳定性。"""
        from core.analysis.hypothesis_service import hash_json
        base = hash_json({"a": 1})
        assert hash_json({"a": 2}) != base
        assert hash_json({"b": 1}) != base
        assert hash_json({"a": {"deep": {"deeper": [1, 2]}}}) != base

    def test_stat_window_mask_起点单调收缩(self) -> None:
        """属性：更晚的起点，掩码是更早起点的子集（统计域单调收缩）。"""
        idx = pd.date_range("2024-01-01", periods=100, freq="1h", tz="UTC",
                            name="timestamp")
        df = pd.DataFrame({"close": range(100)}, index=idx)
        m_early = stat_window_mask(df, "2024-01-02T00:00:00Z")
        m_late = stat_window_mask(df, "2024-01-03T00:00:00Z")
        assert not (m_late & ~m_early).any()

    def test_p_value_值域与样本上界(self) -> None:
        """属性：p ∈ [0,1]；去重叠样本数 ≤ 掩码命中数。"""
        from core.analysis.condition_kernel import p_value_for
        rng = np.random.default_rng(5)
        mask = np.zeros(500, dtype=bool)
        mask[::7] = True  # 71 命中，间隔 7 ≥ horizon 4 全入选
        fwd = pd.Series(rng.normal(0, 0.01, 500),
                        index=pd.date_range("2024-01-01", periods=500,
                                            freq="1h", tz="UTC"))
        p, n = p_value_for(fwd, mask, 4, 0.0)
        assert 0.0 <= p <= 1.0
        assert n <= int(mask.sum())

    def test_validate_params_注册表默认参数恒放行(self) -> None:
        """属性：注册表每个指标的默认参数集均通过值域校验（默认值
        与注册表声明自洽——漂移即红）。"""
        from core.analysis.indicator_query import validate_params
        from core.analysis.registry_meta import indicator_metadata
        for meta in indicator_metadata():
            defaults = {k: spec["default"]
                        for k, spec in meta["params"].items()}
            validate_params(meta["name"], defaults)
