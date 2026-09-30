"""多周期条件独立复核用例（tester·multi-timeframe-conditions）。

与实施锚（test_mtf_conditions.py）数据错开的独立对照——期望由契约
手工推导（design R2/R3/R6/B2/B3/B4/B7 + 错误契约七条 + spec 场景：
「混合周期假设登记成功」「asof_completed 前视锚」「低周期条件拒绝」
「THEN 携带周期拒绝」「区间周期不一致拒绝」「指纹周期归一连续」
「掩码投影语义与穿越状态窗」「高周期数据不可达登记拒绝」「高周期
warmup 不足执行拒绝」「多周期假设转化显式拒绝」「三链对齐语义一致」
「既有链形成期窗口收口」）。掩码期望按「高周期轴手算 + 收盘时刻
T+tf 挂载投影」逐点推导，不调被测实现生成期望。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.condition_identity import (
    canonical_condition_hash, legacy_compatible_hash,
)
from core.analysis.condition_kernel import MtfEvalContext, _mask_for
from core.analysis.hypothesis_service import ResearchError, register_hypothesis
from core.data.mtf_alignment import project_completed, project_completed_bool
from core.data.timeframes import TF_SECONDS
from tests.helpers.data_factory import make_ohlcv

SYMBOL = "BTCUSDT"
_T0 = pd.Timestamp("2024-03-05 00:00:00", tz="UTC")
_SEP = "\x1f"


def _grid(start: pd.Timestamp, n: int, tf: str) -> pd.DatetimeIndex:
    step = pd.Timedelta(seconds=TF_SECONDS[tf])
    return pd.DatetimeIndex([start + i * step for i in range(n)])


def _ohlcv(close: list[float], volume: list[float] | None = None,
           start: pd.Timestamp = _T0, tf: str = "4h") -> pd.DataFrame:
    """确定性高周期 OHLCV（close/volume 手工设定，期望可手算）。"""
    vol = volume if volume is not None else [100.0] * len(close)
    idx = _grid(start, len(close), tf)
    c = pd.Series(close, index=idx)
    return pd.DataFrame(
        {"open": c.shift(1).fillna(c.iloc[0]), "high": c, "low": c,
         "close": c, "volume": pd.Series(vol, index=idx)})


def _write_parquet(data_dir: Path, tf: str, df: pd.DataFrame) -> None:
    df.to_parquet(data_dir / f"{SYMBOL}_{tf}.parquet")


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    from core.persistence.db_ext import init_db_ext

    path = tmp_path / "quant.db"
    init_db_ext(path)
    return path


@pytest.fixture()
def rand_env(tmp_path: Path):
    """随机数据环境（仅用于「同掩码」类对照断言，不用于手算期望）。"""
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    base = make_ohlcv(n=600, freq="1h", start="2024-01-01", seed=23)
    high = make_ohlcv(n=150, freq="4h", start="2024-01-01", seed=23)
    _write_parquet(data_dir, "1h", base)
    _write_parquet(data_dir, "4h", high)
    return {"data_dir": data_dir, "base": base, "high": high}


def _register(db, when, then=None, ranges=None):
    return register_hypothesis(
        db,
        conditions={"when_conditions": when,
                    "then_conditions": then or [
                        {"subject": "close", "action": "gt", "target": 0}]},
        statement="mtf 独立复核",
        data_ranges=ranges or [
            {"symbol": SYMBOL, "timeframe": "1h",
             "start": "2024-03-01T00:00:00Z", "end": "2024-04-01T00:00:00Z"}])


def _when(**extra):
    cond = {"subject": "close", "action": "gt", "target": 0.0}
    cond.update(extra)
    return [cond]


# ---------------------------------------------------------------- 登记链（B3）

class TestRegistrationIndependent:
    def test_校验序_THEN带未知周期_报THEN拒而非值域拒(self, db):
        """B3 校验序 THEN 拒 → 值域：then 带非法周期键先命中
        TIMEFRAME_ON_THEN（跨周期前向窗口归属未定义是更根本的拒绝）。"""
        with pytest.raises(ResearchError) as ei:
            _register(db, _when(),
                      then=[{"subject": "close", "action": "gt",
                             "target": 0, "timeframe": "2d"}])
        assert ei.value.code == "TIMEFRAME_ON_THEN"
        assert "前向收益对照" in ei.value.message

    def test_未知周期_拒绝附合法清单(self, db):
        with pytest.raises(ResearchError) as ei:
            _register(db, _when(timeframe="2d"))
        assert ei.value.code == "TIMEFRAME_UNKNOWN"
        legal = (ei.value.detail or {}).get("legal_range", [])
        assert "4h" in legal and "1w" in legal and "2d" not in legal

    def test_低周期_拒绝附降基准指引与基准周期(self, db):
        with pytest.raises(ResearchError) as ei:
            _register(db, _when(timeframe="15m"))
        assert ei.value.code == "TIMEFRAME_NOT_HIGHER"
        assert "降基准" in ei.value.message
        assert (ei.value.detail or {}).get("base_timeframe") == "1h"

    def test_区间symbol不一致_拒绝(self, db):
        ranges = [
            {"symbol": SYMBOL, "timeframe": "1h",
             "start": "2024-03-01T00:00:00Z", "end": "2024-03-15T00:00:00Z"},
            {"symbol": "ETHUSDT", "timeframe": "1h",
             "start": "2024-03-15T00:00:00Z", "end": "2024-04-01T00:00:00Z"}]
        with pytest.raises(ResearchError) as ei:
            _register(db, _when(), ranges=ranges)
        assert ei.value.code == "RESEARCH_RANGE_INVALID"
        assert "基准周期" in ei.value.message

    def test_混合假设登记成功_键原样保留_显式基准剥离(self, db):
        """spec「混合周期假设登记成功」：高周期与缺省条件并存，
        timeframe 原样保留；显式基准在 canonical 化时剥离。"""
        out = _register(db, [
            {"subject": "close", "action": "gt", "target": 0.0,
             "timeframe": "4h"},
            {"subject": "close", "action": "gt", "target": 0.0}])
        assert out["duplicated"] is False
        from core.persistence.repository import research as rr
        hyp = rr.get_hypothesis(db, out["hypothesis_id"])
        whens = json.loads(hyp["conditions"])["when_conditions"]
        assert whens[0].get("timeframe") == "4h"
        assert "timeframe" not in whens[1]

    def test_幂等_先显式基准后缺省_反序命中同一假设(self, db):
        """spec「指纹周期归一连续」登记侧：显式基准与缺省同身份。"""
        a = _register(db, _when(timeframe="1h"))
        b = _register(db, _when())
        assert b["duplicated"] is True
        assert b["hypothesis_id"] == a["hypothesis_id"]


# ---------------------------------------------------------------- 指纹（B4）

class TestFingerprintIndependent:
    """指纹独立推导：按 docstring 归一规则手工拼 \\x1f 段并 sha256
    （subject 段经 0.2.13 既有 canonical_ref——非本变更对象，有
    test_column_ref 独立把守），比对 0.2.14 第 4 分量有效周期归一。"""

    COND = {"subject": {"indicator": "EMA", "params": {"period": 20},
                        "field": "close"},
            "action": "gt", "target": 25}

    def _manual(self, cond, task_tf):
        from core.analysis.column_ref import canonical_ref
        parts = [canonical_ref(cond["subject"]),
                 str(cond["action"]).strip(),
                 "n:25",                       # target 数值归一（整值去尾）
                 str(cond.get("timeframe") or task_tf).strip()]
        return hashlib.sha256(_SEP.join(parts).encode()).hexdigest()[:16]

    def test_无timeframe键_与手工推导逐字节一致(self):
        got = canonical_condition_hash(self.COND, "1h")
        assert got == self._manual(self.COND, "1h")
        assert len(got) == 16

    def test_显式基准与缺省同指纹(self):
        a = dict(self.COND, timeframe="1h")
        assert canonical_condition_hash(a, "1h") == \
            canonical_condition_hash(self.COND, "1h") == \
            self._manual(a, "1h")

    def test_同名列不同周期_不同指纹(self):
        """spec：同名列跨周期数据不同，不算精确复用。"""
        high = dict(self.COND, timeframe="4h")
        assert canonical_condition_hash(high, "1h") != \
            canonical_condition_hash(self.COND, "1h")

    def test_条件自带周期优先于任务周期(self):
        """B4 eff=condition.get("timeframe") or timeframe——任务周期
        被条件键覆盖（五调用方传任务周期即正确的机制基础）。"""
        c = dict(self.COND, timeframe="4h")
        assert canonical_condition_hash(c, "1d") == \
            canonical_condition_hash(c, "1h") == \
            self._manual(c, "1h")

    def test_legacy影子_字符串subject两算法一致_周期归一同构(self):
        plain = {"subject": "close", "action": "lt", "target": "30.5"}
        assert legacy_compatible_hash(plain, "1h") == \
            canonical_condition_hash(plain, "1h")
        # 同构扩展：条件自带周期时两算法同覆盖
        tagged = dict(plain, timeframe="1d")
        assert legacy_compatible_hash(tagged, "1h") == \
            canonical_condition_hash(tagged, "1h")


# ---------------------------------------------------------------- 三分派（B2）

class TestMaskForDispatchIndependent:
    def _ctx(self, rand_env):
        return MtfEvalContext(
            rand_env["data_dir"], SYMBOL, "1h",
            eval_start=rand_env["base"].index[50])

    def test_无ctx带高周期键_显式ResearchError指明接线缺陷(self, rand_env):
        work = rand_env["base"].copy()
        with pytest.raises(ResearchError) as ei:
            _mask_for(work, {"subject": "close", "action": "gt",
                             "target": 0.0, "timeframe": "4h"})
        assert ei.value.code == "RESEARCH_MTF_CONTEXT_MISSING"
        assert "调用方" in ei.value.message   # 非用户输入错误的定位

    def test_显式基准等于缺省_同掩码(self, rand_env):
        """spec：显式写基准周期与缺省语义完全相同（同求值）。"""
        work = rand_env["base"].copy()
        threshold = float(rand_env["base"]["close"].median())
        explicit = _mask_for(
            work, {"subject": "close", "action": "gt",
                   "target": threshold, "timeframe": "1h"},
            self._ctx(rand_env))
        default = _mask_for(
            work, {"subject": "close", "action": "gt", "target": threshold},
            self._ctx(rand_env))
        pd.testing.assert_series_equal(explicit, default)

    def test_缺省零变化_有ctx与无ctx同掩码(self, rand_env):
        """mtf=None 缺省走现行两段式——ctx 存在不改变无键条件行为。"""
        work = rand_env["base"].copy()
        threshold = float(rand_env["base"]["close"].median())
        cond = {"subject": "close", "action": "gt", "target": threshold}
        with_ctx = _mask_for(work.copy(), cond, self._ctx(rand_env))
        without = _mask_for(work.copy(), cond)
        pd.testing.assert_series_equal(with_ctx, without)


# ---------------------------------------------------------------- 掩码投影（R2）

class TestMaskProjectionIndependent:
    """确定性 4h 数据 + 手算高周期掩码 + T+tf 挂载投影的逐点期望。"""

    def _env(self, tmp_path, high_df, base_bars=24):
        data_dir = tmp_path / "market"
        data_dir.mkdir(parents=True, exist_ok=True)
        _write_parquet(data_dir, "4h", high_df)
        base = make_ohlcv(n=base_bars, freq="1h", start="2024-03-05", seed=99)
        _write_parquet(data_dir, "1h", base)
        ctx = MtfEvalContext(data_dir, SYMBOL, "1h", eval_start=base.index[0])
        return base, ctx

    def test_cross挂高周期_双状态窗手工推导(self, tmp_path):
        """close=[5,12,8,30,3,...] cross_above 10：高周期轴穿越 bar1
        （5→12）与 bar3（8→30）；投影 1h 轴 24 根：状态窗 [08:00,12:00)
        与 [16:00,20:00)，其余 False（含首段与 bar2/bar4 挂载后的回落）。
        """
        high = _ohlcv([5, 12, 8, 30, 3, 3, 3, 3])
        base, ctx = self._env(tmp_path, high)
        out = _mask_for(base.copy(), {"subject": "close",
                                      "action": "cross_above",
                                      "target": 10.0, "timeframe": "4h"}, ctx)
        expected = ([False] * 8 + [True] * 4 + [False] * 4
                    + [True] * 4 + [False] * 4)
        assert out.tolist() == expected

    def test_spike挂高周期_高周期轴自洽非恒False(self, tmp_path):
        """volume 平稳 50 十九根后一根 400：bar19 的 rolling(20,
        min_periods=1) 均值 =(19×50+400)/20=67.5，400≥67.5×2 → 高周期
        轴 True；投影后 T0+80h 起可见（值投影阶梯常数下环比恒 False）。
        """
        high = _ohlcv([100.0] * 20, volume=[50.0] * 19 + [400.0])
        base, ctx = self._env(tmp_path, high, base_bars=85)
        out = _mask_for(base.copy(), {"subject": "volume",
                                      "action": "spike",
                                      "target": 2.0, "timeframe": "4h"}, ctx)
        assert out.tolist() == [False] * 80 + [True] * 5
        # 值投影失效对照：投影后阶梯列平坦段环比不触发（R2 论据实证）
        stepped = project_completed(
            high["volume"], base.index, "4h")
        rolled = stepped.rolling(window=20, min_periods=1).mean()
        assert not (stepped >= rolled * 2.0).iloc[4:80].any()

    def test_asof差分_形成中4h_bar改值_掩码逐点不变(self, tmp_path):
        """spec「asof_completed 前视锚」：末根 4h（收盘 T0+24h 晚于
        基准末端 T0+21h）close 99（再穿越）与 7（不穿越）掩码输出相同。"""
        outs = []
        for last in (99.0, 7.0):
            high = _ohlcv([5, 12, 8, 30, 3, last])
            base, ctx = self._env(tmp_path / f"v{int(last)}", high,
                                  base_bars=22)
            outs.append(_mask_for(
                base.copy(), {"subject": "close", "action": "cross_above",
                              "target": 10.0, "timeframe": "4h"}, ctx))
        pd.testing.assert_series_equal(outs[0], outs[1])
        assert outs[0].tolist() == [False] * 8 + [True] * 4 + \
            [False] * 4 + [True] * 4 + [False] * 2

    def test_gt状态族_掩码投影与阶梯常数比较逐bar等价(self, tmp_path):
        """R2 论据表：gt/lt 状态族两语义逐 bar 等价（双路独立对照）。"""
        high = _ohlcv([10, 25, 5, 30, 30, 15])
        base, ctx = self._env(tmp_path, high)
        via_mask = _mask_for(base.copy(), {"subject": "close",
                                           "action": "gt",
                                           "target": 15.0,
                                           "timeframe": "4h"}, ctx)
        stepped = project_completed(high["close"], base.index, "4h")
        via_value = (stepped > 15.0).fillna(False).astype(bool)
        pd.testing.assert_series_equal(via_mask, via_value)

    def test_额外键window在高周期路径透传生效(self, tmp_path):
        """D-r2：window=6 的 lookback_any——close=[40,55,40×6]，
        bar6 窗（bar1..bar6 含 55）为 True、缺省 window=5 时 bar6 窗
        （bar2..bar6）为 False——投影后 [28h,32h) 段差异。"""
        high = _ohlcv([40, 55, 40, 40, 40, 40, 40, 40])
        base, ctx = self._env(tmp_path, high, base_bars=32)
        cond = {"subject": "close", "action": "lookback_any",
                "target": 50.0, "inner_action": "gt", "timeframe": "4h"}
        w6 = _mask_for(base.copy(), dict(cond, window=6), ctx)
        w_default = _mask_for(base.copy(), cond, ctx)
        assert w6.iloc[28:].tolist() == [True] * 4      # bar6 状态窗
        assert w_default.iloc[28:].tolist() == [False] * 4
        assert w6.iloc[8:12].tolist() == w_default.iloc[8:12].tolist()


# ---------------------------------------------------------------- warmup（R3）

class TestWarmupRejectIndependent:
    def test_warmup不足_拒绝附需求与可得bar数(self, tmp_path):
        """spec「高周期 warmup 不足执行拒绝」：EMA(20) 需 20×3=60 bar
        前扩；数据起点早于 eval_start 仅 5d（=30 bar）→ 不足拒绝。"""
        data_dir = tmp_path / "market"
        data_dir.mkdir()
        high = _ohlcv([100.0] * 30, start=_T0 - pd.Timedelta(days=5))
        _write_parquet(data_dir, "4h", high)
        base = make_ohlcv(n=100, freq="1h", start="2024-03-05", seed=5)
        _write_parquet(data_dir, "1h", base)
        ctx = MtfEvalContext(data_dir, SYMBOL, "1h",
                             eval_start=base.index[0])
        with pytest.raises(ResearchError) as ei:
            _mask_for(base.copy(), {
                "subject": {"indicator": "EMA", "params": {"period": 20}},
                "action": "gt", "target": 0.0, "timeframe": "4h"}, ctx)
        assert ei.value.code == "RESEARCH_WARMUP_INSUFFICIENT"
        detail = ei.value.detail or {}
        assert detail["warmup_bars"] == 60      # 收敛 20 × 3
        assert detail["available_bars"] == 30   # 5d / 4h
        assert "data_start" in detail


# ---------------------------------------------------------------- Lab 投影器（B5）

class TestMergeToBaseIndependent:
    def test_值投影器_收盘挂载无前视_位移退役(self):
        """spec「既有链形成期窗口收口」：4h close [7,77]——T0 开盘 bar
        的 7 自 T0+4h 起可见（旧 +1 位移在开盘窗内即暴露=前视，退役）。"""
        from core.validation.mtf import merge_to_base

        base = make_ohlcv(n=12, freq="1h", start="2024-03-05", seed=3)
        info = _ohlcv([7.0, 77.0])
        merged = merge_to_base(base, {"4h": info}, base_tf_minutes=60)
        col = merged["close_4h"]
        assert col.iloc[:4].isna().all()        # 首根收盘前不可见
        assert col.iloc[4:8].tolist() == [7.0] * 4
        assert col.iloc[8:12].tolist() == [77.0] * 4

    def test_OHLCV列同样投影_排除清单移除(self):
        from core.validation.mtf import merge_to_base

        base = make_ohlcv(n=8, freq="1h", start="2024-03-05", seed=3)
        info = _ohlcv([7.0, 77.0])
        merged = merge_to_base(base, {"4h": info}, base_tf_minutes=60)
        for col in ("open_4h", "high_4h", "low_4h", "close_4h", "volume_4h"):
            assert col in merged.columns

    def test_后缀列名与列序保持(self):
        from core.validation.mtf import merge_to_base

        base = make_ohlcv(n=8, freq="1h", start="2024-03-05", seed=3)
        info = pd.DataFrame(
            {"ema_20": [1.0, 2.0], "rsi_14": [55.0, 45.0]},
            index=_grid(_T0, 2, "4h"))
        merged = merge_to_base(base, {"4h": info}, base_tf_minutes=60)
        added = [c for c in merged.columns if c.endswith("_4h")]
        assert added == ["ema_20_4h", "rsi_14_4h"]   # 列序不变


# ---------------------------------------------------------------- 四链一致（B6）

class TestCrossChainIndependent:
    def test_策略链信号投影_与研究链布尔投影逐点一致(self):
        """spec「三链对齐语义一致」最小独立证明：同一 4h 布尔序列，
        resample_signals（策略链，索引推断 delta）与 project_completed_
        bool（研究/Lab 掩码通道）在基准轴逐点同值。"""
        from core.strategy.executor import resample_signals

        mask = pd.Series([False, True, False, True, False],
                         index=_grid(_T0, 5, "4h"))
        base_idx = _grid(_T0, 20, "1h")
        via_strategy = resample_signals(mask, base_idx)
        via_research = project_completed_bool(mask, base_idx, "4h")
        pd.testing.assert_series_equal(via_strategy, via_research)
        # 期望手算：T 窗 [04:00,08:00)→挂载 08:00..11h；[12:00,16:00)
        # →挂载 16:00..19h
        expected = ([False] * 8 + [True] * 4 + [False] * 4
                    + [True] * 4 + [False] * 0)[:20]
        assert via_strategy.tolist() == expected

    def test_Lab链值投影通道_与权威值投影同构(self):
        from core.validation.mtf import merge_to_base

        base = make_ohlcv(n=12, freq="1h", start="2024-03-05", seed=3)
        info = _ohlcv([7.0, 77.0])
        merged = merge_to_base(base, {"4h": info[["close"]]},
                               base_tf_minutes=60)
        direct = project_completed(info["close"], base.index, "4h")
        pd.testing.assert_series_equal(
            merged["close_4h"], direct, check_names=False)


# ---------------------------------------------------------------- 转化拒绝（B7）

class TestTranslationRejectIndependent:
    def test_多周期假设转化显式拒绝_不产出草稿(self, db, rand_env):
        """spec「多周期假设转化显式拒绝」：带 timeframe 键假设转化
        MUST 拒并说明暂不支持；MUST NOT 静默降级产出单周期草稿。"""
        from core.analysis.hypothesis_to_strategy import translate_hypothesis
        from core.persistence.repository import strategy as sr

        db.parent.parent.mkdir(exist_ok=True)   # noqa: 保留 tmp 结构
        out = _register(db, _when(timeframe="4h"))
        before = sr.list_strategies(db)
        with pytest.raises(ResearchError) as ei:
            translate_hypothesis(db, hypothesis_id=out["hypothesis_id"])
        assert ei.value.code == "RESEARCH_NOT_EXPRESSIBLE"
        assert "多周期假设翻译暂不支持" in ei.value.message
        assert sr.list_strategies(db) == before   # 禁止变化：无草稿落库

    def test_同假设登记与求值不受转化边界影响(self, db, rand_env):
        """spec：拒绝文案明示登记/条件求值类分析/案例检查不受影响。"""
        out = _register(db, _when(timeframe="4h"))
        assert out["duplicated"] is False       # 登记可用
        ctx = MtfEvalContext(
            rand_env["data_dir"], SYMBOL, "1h",
            eval_start=rand_env["base"].index[0])
        mask = _mask_for(rand_env["base"].copy(),
                         {"subject": "close", "action": "gt",
                          "target": 0.0, "timeframe": "4h"}, ctx)
        assert mask.dtype == bool and len(mask) == len(rand_env["base"])


# ---------------------------------------------------------------- 模板透传（B2/场景⑬）

class TestTemplatePassthroughIndependent:
    """spec ai-research-tools「高周期中心条件经投影求值」+ 场景矩阵⑬
    （tester 补充锚——实施锚在模板级缺失，见交付说明）。

    掩码投影在模板级生效的决定性对照：阈值 > 4h close 全域时，带键
    （4h 轴求值，全 False）samples=0；同阈值去键（1h 轴求值，随机
    数据下存在越界 bar）samples>0——两轴结果分离证明投影路径接通。
    """

    def _env(self, tmp_path):
        data_dir = tmp_path / "market"
        data_dir.mkdir(parents=True, exist_ok=True)
        base = make_ohlcv(n=600, freq="1h", start="2024-01-01", seed=23)
        high = make_ohlcv(n=150, freq="4h", start="2024-01-01", seed=23)
        _write_parquet(data_dir, "1h", base)
        _write_parquet(data_dir, "4h", high)
        ctx = MtfEvalContext(data_dir, SYMBOL, "1h")
        return base, high, ctx

    def _sweep(self, base, cond, ctx):
        from core.analysis.templates.param_sweep import param_sweep

        t = float(cond["target"])
        payload = param_sweep(
            base.copy(), indicator="RSI", condition=cond,
            sweep={"axis": "target", "values": [t - 10.0, t, t + 10.0]},
            forward_bars=12,
            data_ranges=[{"symbol": SYMBOL, "timeframe": "1h",
                          "start": "2024-01-02", "end": "2024-01-25"}],
            timeframe="1h", mtf=ctx).to_dict()["payload"]
        return next(p for p in payload["points"] if p["value"] == t)

    def test_param_sweep_高周期中心条件_掩码投影求值生效(self, tmp_path):
        base, high, ctx = self._env(tmp_path)
        beyond = float(high["close"].max()) + 1.0
        mid = float(high["close"].median())
        # 带键：4h 轴全 False → 资格门拦截（samples=0）
        p_high = self._sweep(base, {"subject": "close", "action": "gt",
                                    "target": beyond, "timeframe": "4h"}, ctx)
        assert p_high["samples"] == 0
        assert p_high["state"] == "insufficient_sample"
        # 同阈值去键：1h 轴随机数据存在越界 bar → samples>0（轴分离铁证）
        p_base = self._sweep(base, {"subject": "close", "action": "gt",
                                    "target": beyond}, ctx)
        assert p_base["samples"] > 0
        # 中位阈值两轴样本数不同（掩码投影改变命中集）
        a = self._sweep(base, {"subject": "close", "action": "gt",
                               "target": mid, "timeframe": "4h"}, ctx)
        b = self._sweep(base, {"subject": "close", "action": "gt",
                               "target": mid}, ctx)
        assert a["samples"] != b["samples"]

    def test_param_sweep_低周期中心条件_结构化拒绝(self, tmp_path):
        base, _, ctx = self._env(tmp_path)
        with pytest.raises(ResearchError) as ei:
            self._sweep(base, {"subject": "close", "action": "gt",
                               "target": 100.0, "timeframe": "15m"}, ctx)
        assert ei.value.code == "TIMEFRAME_NOT_HIGHER"

    def test_param_sweep_不可达周期_结构化拒绝(self, tmp_path):
        base, _, ctx = self._env(tmp_path)
        with pytest.raises(ResearchError) as ei:
            self._sweep(base, {"subject": "close", "action": "gt",
                               "target": 100.0, "timeframe": "1w"}, ctx)
        assert ei.value.code == "RESEARCH_SYMBOL_NOT_FOUND"

    def test_condition_stats_高周期条件_端到端三要素在场(self, tmp_path):
        """spec「混合周期假设条件求值端到端成功」条件统计族模板面：
        掩码投影求值成功，结果携带样本量/置信信息/品种周期数据区间。"""
        from core.analysis.templates.condition_stats import condition_stats

        base, high, ctx = self._env(tmp_path)
        low = float(high["close"].min()) - 1.0   # 4h 轴全 True
        out = condition_stats(
            base.copy(), indicator="RSI", params={"period": 14},
            condition={"subject": "close", "action": "gt",
                       "target": low, "timeframe": "4h"},
            data_ranges=[{"symbol": SYMBOL, "timeframe": "1h",
                          "start": "2024-01-02", "end": "2024-01-25"}],
            forward_bars=12, mtf=ctx).to_dict()
        assert out["sample_size"] > 0
        assert out["confidence"] is not None
        assert out["data_ranges"][0]["timeframe"] == "1h"
        assert out["payload"]["hit_count"] > 0


# ---------------------------------------------------------------- 路由可达（N2）

class TestReachabilityIndependent:
    def test_高周期周期无数据_拒绝附探测可得清单(self, tmp_path, db):
        """N2：「周期非法」与「周期无数据」语义区分——数据目录仅 1h
        文件时，4h 键拒绝附实际可得清单（仅 1h）。"""
        from fastapi.testclient import TestClient

        from MyQuant.api.app import create_app

        data_dir = tmp_path / "only_1h"
        data_dir.mkdir()
        _write_parquet(data_dir, "1h",
                       make_ohlcv(n=100, freq="1h", start="2024-03-01",
                                  seed=11))
        app = create_app(db_path=db, data_dir=data_dir)
        with TestClient(app) as client:
            resp = client.post("/api/research/hypotheses", json={
                "statement": "可达独立复核",
                "conditions": {
                    "when_conditions": [
                        {"subject": "close", "action": "gt",
                         "target": 0.0, "timeframe": "4h"}],
                    "then_conditions": [
                        {"subject": "close", "action": "gt", "target": 0}]},
                "data_ranges": [
                    {"symbol": SYMBOL, "timeframe": "1h",
                     "start": "2024-03-10T00:00:00Z",
                     "end": "2024-03-20T00:00:00Z"}]})
        assert resp.status_code == 422, resp.text
        body = resp.json()["detail"]
        assert body["code"] == "RESEARCH_SYMBOL_NOT_FOUND"
        assert body["detail"]["available_timeframes"] == ["1h"]


# ---------------------------------------------------------------- P-5 字符串 subject decode（3b33c4f 回归）

class TestP5StringSubjectDecode:
    """P-5（design R2 subject/target 对称）：模板层字符串化 subject 与
    用户直传编码列名形在高周期轴 decode 现算；不可解析保持列缺失全
    False 契约；decode 可解析即并入 warmup 收敛需求（静默全 False 的
    组合转显式拒绝——行为变化披露项）。"""

    def _env(self, tmp_path, base_bars=600, base_start="2024-01-01"):
        data_dir = tmp_path / "market"
        data_dir.mkdir(parents=True, exist_ok=True)
        base = make_ohlcv(n=base_bars, freq="1h", start=base_start, seed=23)
        high = make_ohlcv(n=150, freq="4h", start="2024-01-01", seed=23)
        _write_parquet(data_dir, "1h", base)
        _write_parquet(data_dir, "4h", high)
        return data_dir, base, high

    def test_字符串编码subject_与结构化subject_同掩码(self, tmp_path):
        """「rsi_14」与 {"indicator":"RSI","params":{"period":14}} 两形
        同条件同掩码（decode 现算与结构化路径等价）。"""
        data_dir, base, _ = self._env(tmp_path)
        ctx = MtfEvalContext(data_dir, SYMBOL, "1h",
                             eval_start=pd.Timestamp("2024-01-15", tz="UTC"))
        shared = {"action": "lt", "target": 30.0, "timeframe": "4h"}
        via_str = _mask_for(base.copy(), dict(shared, subject="rsi_14"), ctx)
        via_dict = _mask_for(base.copy(), dict(
            shared, subject={"indicator": "RSI", "params": {"period": 14}}), ctx)
        pd.testing.assert_series_equal(via_str, via_dict)

    def test_结构化EMA掩码_与手算递推EMA投影逐点一致(self, tmp_path):
        """硬锚：EMA=递推（seed=前 20 根 SMA、前 19 NaN、alpha=2/(p+1)）
        ——手算递推 EMA 于 4h close、gt 掩码、+4h 挂载投影，与 _mask_for
        结构化路径逐点一致。"""
        data_dir, base, high = self._env(tmp_path)
        ctx = MtfEvalContext(data_dir, SYMBOL, "1h",
                             eval_start=pd.Timestamp("2024-01-15", tz="UTC"))
        mid = float(high["close"].median())
        cond = {"subject": {"indicator": "EMA", "params": {"period": 20}},
                "action": "gt", "target": mid, "timeframe": "4h"}
        got = _mask_for(base.copy(), cond, ctx)

        # 手算递推 EMA（TA 惯例：seed=SMA(20)，前 19 NaN）
        c = high["close"].to_numpy()
        alpha = 2.0 / (20 + 1)
        ema = [float("nan")] * 19 + [float(c[:20].mean())]
        for x in c[20:]:
            ema.append(alpha * x + (1 - alpha) * ema[-1])
        high_mask = pd.Series(ema, index=high.index) > mid
        # +4h 挂载投影（手工 reindex ffill——不调被测投影函数）
        closed_idx = high.index + pd.Timedelta(hours=4)
        visible = pd.Series(high_mask.astype(float).to_numpy(),
                            index=closed_idx).reindex(base.index, method="ffill")
        expected = visible.fillna(0.0).astype(bool)
        pd.testing.assert_series_equal(got, expected, check_names=False)

    def test_decode可解析subject_并入warmup_不足显式拒_基础列不拒(self, tmp_path):
        """行为变化披露项：rsi_14@4h 在高周期起点不足 RSI(14) 收敛×3
        （42 bar）时显式拒绝（detail 附需求 vs 可得）；同数据纯基础列
        close@4h 正常求值（零前扩契约保持——不以 floor 拒可求值请求）。"""
        import numpy as np

        data_dir = tmp_path / "market"
        data_dir.mkdir(parents=True, exist_ok=True)
        # 4h 37 根起点 T0；1h 基准起点 T0+39×4h（=2024-01-07 12:00）
        high = make_ohlcv(n=37, freq="4h", start="2024-01-01", seed=7)
        base = make_ohlcv(n=100, freq="1h", start="2024-01-07 12:00", seed=7)
        _write_parquet(data_dir, "4h", high)
        _write_parquet(data_dir, "1h", base)
        ctx = MtfEvalContext(data_dir, SYMBOL, "1h", eval_start=base.index[0])

        with pytest.raises(ResearchError) as ei:
            _mask_for(base.copy(), {"subject": "rsi_14", "action": "lt",
                                    "target": 30.0, "timeframe": "4h"}, ctx)
        assert ei.value.code == "RESEARCH_WARMUP_INSUFFICIENT"
        detail = ei.value.detail or {}
        assert detail["warmup_bars"] == 42       # RSI(14) 收敛 14 × 3
        assert detail["available_bars"] == 39    # 距 4h 起点 39 bar
        # 同数据 close（decode 不可解析、无指标收敛需求）正常求值
        mask = _mask_for(base.copy(), {"subject": "close", "action": "gt",
                                       "target": 0.0, "timeframe": "4h"}, ctx)
        assert len(mask) == len(base) and bool(mask.any())

    def test_后缀列名subject_decode失败_全False降级_基础列raw直用(self, tmp_path):
        """结构性不碰撞：ema_20_4h（Lab 后缀列名）decode 失败 → 高周期
        raw 无此列 → 全 False 安全降级（契约保持）；close 同样不可 decode
        但 raw 直用命中（「decode 失败」≠「不可用」双证）。"""
        from core.analysis.column_ref import decode_column_ref

        data_dir, base, high = self._env(tmp_path)
        ctx = MtfEvalContext(data_dir, SYMBOL, "1h")
        # decode 不可解析契约=返回 None（rsi_14/ema_20 可解析、
        # close/ema_20_4h 不可——实测边界）
        assert decode_column_ref("ema_20_4h") is None
        assert decode_column_ref("close") is None
        assert decode_column_ref("rsi_14") is not None
        mask = _mask_for(base.copy(), {"subject": "ema_20_4h",
                                       "action": "gt", "target": 0.0,
                                       "timeframe": "4h"}, ctx)
        assert not mask.any()
        # close 直用 4h raw 列：掩码与手算挂载投影命中一致（296）
        mid = float(high["close"].median())
        via_close = _mask_for(base.copy(), {"subject": "close", "action": "gt",
                                            "target": mid, "timeframe": "4h"}, ctx)
        closed_idx = high.index + pd.Timedelta(hours=4)
        visible = pd.Series(high["close"].to_numpy(),
                            index=closed_idx).reindex(base.index, method="ffill")
        expected = (visible > mid).fillna(False)
        pd.testing.assert_series_equal(via_close, expected, check_names=False)
