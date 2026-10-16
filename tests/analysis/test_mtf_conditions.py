"""多周期条件求值测试（multi-timeframe-conditions 3.1/2.1/2.2）。

锚族（design T2）：三分派（缺省零变化 / 无 ctx 带键显式拒 / 字面后缀
列命中）、额外键全量透传（D-r2——lookback_any 自定义 window 生效）、
混合求值锚（真实 parquet 1h 基准 + 4h 高周期，独立对照=挂载点手工
推导，不以被测实现生成期望）、warmup 不足 / 不可达拒绝、幂等缓存、
登记链拒绝五类（task 2.1/2.2 在 TestRegistration 族）。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.condition_kernel import MtfEvalContext, mask_for
from core.analysis.hypothesis_service import ResearchError
from tests.helpers.data_factory import make_ohlcv

SYMBOL = "BTCUSDT"


# ---------------------------------------------------------------- fixtures

@pytest.fixture()
def env(tmp_path: Path):
    """1h 基准 800 bar + 4h 高周期 200 bar（同窗对齐）+ 独立 db。"""
    from core.persistence.db_ext import init_db_ext

    data_dir = tmp_path / "market"
    data_dir.mkdir()
    base = make_ohlcv(n=800, freq="1h", start="2023-06-01", seed=7)
    high = make_ohlcv(n=200, freq="4h", start="2023-06-01", seed=7)
    base.to_parquet(data_dir / f"{SYMBOL}_1h.parquet")
    high.to_parquet(data_dir / f"{SYMBOL}_4h.parquet")
    db = tmp_path / "quant.db"
    init_db_ext(db)
    return {"data_dir": data_dir, "db": db, "base": base, "high": high}


def _ctx(env, eval_from: int = 200) -> MtfEvalContext:
    """真实链路形制的 ctx：eval_start=求值域起点（load_with_warmup 的
    request_start 同语义——warmup 检查以统计域起点为基准）。"""
    return MtfEvalContext(
        env["data_dir"], SYMBOL, "1h",
        eval_start=env["base"].index[eval_from])


def _hand_projection(high_close: pd.Series, base_index: pd.DatetimeIndex,
                     tf: str, fn):
    """独立对照：+tf 挂载点手工推导（不调被测 project_completed）。

    fn 接收「截至 t 已收盘的高周期值序列」，返回该基准 bar 的期望值。
    """
    from core.data.timeframes import TF_SECONDS
    closed_ts = high_close.index + pd.Timedelta(seconds=TF_SECONDS[tf])
    out = []
    for t in base_index:
        visible = high_close[closed_ts <= t]
        out.append(fn(visible))
    return pd.Series(out, index=base_index)


# ---------------------------------------------------------------- 三分派

class TestMaskForDispatch:
    def test_缺省零变化_mtf存在但条件无键_同掩码(self, env):
        """无 timeframe 键条件：ctx 存在与否掩码逐字节相同（现行两段式）。"""
        work = env["base"].copy()
        cond = {"subject": "close", "action": "gt", "target": 0.0}
        ctx = MtfEvalContext(env["data_dir"], SYMBOL, "1h")
        a = mask_for(work, cond)
        b = mask_for(work.copy(), cond, mtf=ctx)
        pd.testing.assert_series_equal(a, b)

    def test_显式基准等于缺省_同掩码(self, env):
        """timeframe=基准周期 → 与无键条件同掩码（同语义同求值）。"""
        work = env["base"].copy()
        plain = {"subject": "close", "action": "gt", "target": 0.0}
        explicit = {**plain, "timeframe": "1h"}
        ctx = MtfEvalContext(env["data_dir"], SYMBOL, "1h")
        pd.testing.assert_series_equal(
            mask_for(work, plain, mtf=ctx),
            mask_for(work.copy(), explicit, mtf=ctx))

    def test_无ctx带高周期键_显式ResearchError(self, env):
        """D-final 兜底退役：工程接线错误显式拒绝（非静默回退值投影）。"""
        cond = {"subject": "close", "action": "gt", "target": 0.0,
                "timeframe": "4h"}
        with pytest.raises(ResearchError) as ei:
            mask_for(env["base"].copy(), cond)
        assert ei.value.code == "RESEARCH_MTF_CONTEXT_MISSING"

    def test_低周期键_TIMEFRAME_NOT_HIGHER(self, env):
        cond = {"subject": "close", "action": "gt", "target": 0.0,
                "timeframe": "1m"}
        ctx = MtfEvalContext(env["data_dir"], SYMBOL, "1h")
        with pytest.raises(ResearchError) as ei:
            mask_for(env["base"].copy(), cond, mtf=ctx)
        assert ei.value.code == "TIMEFRAME_NOT_HIGHER"
        assert "降基准" in ei.value.message

    def test_未知周期键_TIMEFRAME_UNKNOWN附合法清单(self, env):
        cond = {"subject": "close", "action": "gt", "target": 0.0,
                "timeframe": "2d"}
        ctx = MtfEvalContext(env["data_dir"], SYMBOL, "1h")
        with pytest.raises(ResearchError) as ei:
            mask_for(env["base"].copy(), cond, mtf=ctx)
        assert ei.value.code == "TIMEFRAME_UNKNOWN"
        assert "1h" in (ei.value.detail or {}).get("legal_range", [])

    def test_字面后缀列名subject_照常列引用命中(self, env):
        """无 timeframe 键的后缀列名（merged 列形制）在 work 上精确命中
        ——不进掩码路径（B5：merge_to_base 降格为列引用投影器）。"""
        work = env["base"].copy()
        work["ema_20_4h"] = 1.0           # 模拟 Lab merged 后缀列
        work.loc[work.index[10], "ema_20_4h"] = 100.0
        cond = {"subject": "ema_20_4h", "action": "gt", "target": 50.0}
        mask = mask_for(work, cond)
        assert bool(mask.iloc[10]) is True
        assert int(mask.sum()) == 1


# ---------------------------------------------------------------- 额外键透传（D-r2）

class TestExtraKeyPassthrough:
    def _work_with_close(self, env) -> pd.DataFrame:
        return env["base"].copy()

    def test_lookback_any自定义window生效(self, env):
        """D-r2 锚：window=3 掩码 == 独立推导（rolling any 3）；现行
        三键重建丢键缺陷下会被默认 window=5 替换——修复后自定义生效。"""
        work = self._work_with_close(env)
        threshold = float(np.median(work["close"]))
        cond3 = {"subject": "close", "action": "lookback_any",
                 "target": threshold, "inner_action": "gt", "window": 3}
        cond5 = {**cond3, "window": 5}

        m3 = mask_for(work.copy(), cond3)
        m5 = mask_for(work.copy(), cond5)
        # 独立推导：rolling(any) 窗 3
        inner = work["close"] > threshold
        expect3 = inner.rolling(window=3, min_periods=1).apply(
            any, raw=False).fillna(False).astype(bool)
        pd.testing.assert_series_equal(m3, expect3)
        # 自定义生效（非恒等于默认窗 5 的结果）
        assert not m3.equals(m5)

    def test_额外键在高周期路径同样透传(self, env):
        """掩码投影路径 window 生效：4h close lookback window=2 高于阈值。"""
        ctx = _ctx(env)
        work = env["base"].copy()
        threshold = float(np.median(env["high"]["close"]))
        cond = {"subject": "close", "action": "lookback_any",
                "target": threshold, "inner_action": "gt", "window": 2,
                "timeframe": "4h"}
        m = mask_for(work, cond, mtf=ctx)
        # 独立对照：高周期轴 rolling any 2 → 投影（+tf 挂载手工推导）
        inner = env["high"]["close"] > threshold
        any2 = inner.rolling(window=2, min_periods=1).apply(
            any, raw=False).fillna(False).astype(bool)
        expect = _hand_projection(
            pd.Series(any2.values, index=env["high"].index),
            work.index, "4h", lambda vis: bool(vis.iloc[-1]) if len(vis) else False)
        pd.testing.assert_series_equal(m, expect, check_names=False)


# ---------------------------------------------------------------- 混合求值（真实 parquet）

class TestHigherTfEvaluation:
    def test_混合求值_close挂4h_独立对照逐点一致(self, env):
        """4h close > 中位数——掩码 == 挂载点手工推导的独立对照。"""
        ctx = _ctx(env)
        work = env["base"].copy()
        threshold = float(np.median(env["high"]["close"]))
        cond = {"subject": "close", "action": "gt", "target": threshold,
                "timeframe": "4h"}
        m = mask_for(work, cond, mtf=ctx)
        expect = _hand_projection(
            env["high"]["close"], work.index, "4h",
            lambda vis: bool(vis.iloc[-1] > threshold) if len(vis) else False)
        pd.testing.assert_series_equal(m, expect.astype(bool), check_names=False)
        # 首段（首根 4h 收盘前 4 根基准 bar）不可见 → False
        assert m.iloc[:4].tolist() == [False] * 4

    def test_asof差分_形成中4h_bar改值_掩码不变(self, env, tmp_path):
        """形成中 bar 差分锚：末根 4h bar 值改掉，投影域内掩码不变。"""
        high_b = env["high"].copy()
        high_b.iloc[-1, high_b.columns.get_loc("close")] = 1e9
        data_dir2 = tmp_path / "market2"
        data_dir2.mkdir()
        env["base"].to_parquet(data_dir2 / f"{SYMBOL}_1h.parquet")
        high_b.to_parquet(data_dir2 / f"{SYMBOL}_4h.parquet")
        threshold = float(np.median(env["high"]["close"]))
        cond = {"subject": "close", "action": "gt", "target": threshold,
                "timeframe": "4h"}
        # 投影域止于末根 4h 收盘前：基准轴去掉最后 4 根
        work_full = env["base"].iloc[:-4].copy()
        m_a = mask_for(work_full, cond,
                        mtf=MtfEvalContext(env["data_dir"], SYMBOL, "1h",
                                           eval_start=env["base"].index[200]))
        m_b = mask_for(env["base"].iloc[:-4].copy(), cond,
                        mtf=MtfEvalContext(data_dir2, SYMBOL, "1h",
                                           eval_start=env["base"].index[200]))
        pd.testing.assert_series_equal(m_a, m_b)

    def test_结构化subject高周期现算_独立对照(self, env):
        """{indicator: EMA, params: {period: 5}} 挂 4h——高周期轴现算
        EMA5 后求值投影（独立对照=测试自调指标计算+手工挂载）。"""
        from core.features.indicators import _compute_indicator

        ctx = _ctx(env)
        work = env["base"].copy()
        cond = {"subject": {"indicator": "EMA", "params": {"period": 5},
                            "field": "ema"},
                "action": "cross_above", "target": 0.0, "timeframe": "4h"}
        m = mask_for(work, cond, mtf=ctx)
        # 独立对照（_compute_indicator 返回 {列名: series}）
        ema5 = _compute_indicator(env["high"].copy(), "EMA", {"period": 5})
        col = [c for c in ema5 if c.startswith("ema")][0]
        cross = (ema5[col].shift(1) < 0) & (ema5[col] >= 0)  # 恒 False（价>0）
        assert not m.any()   # 阈值 0 下穿越不可能——安全语义锚
        # 换可触发语义：close cross_above EMA5（高周期轴，列名类 target）
        cond2 = {"subject": "close",
                 "action": "cross_above",
                 "target": col, "timeframe": "4h"}
        m2 = mask_for(env["base"].copy(), cond2, mtf=ctx)
        cross2 = (env["high"]["close"].shift(1) < ema5[col]) & \
                 (env["high"]["close"] >= ema5[col])
        expect = _hand_projection(
            pd.Series(cross2.fillna(False).astype(bool).values,
                      index=env["high"].index),
            env["base"].index, "4h",
            lambda vis: bool(vis.iloc[-1]) if len(vis) else False)
        pd.testing.assert_series_equal(m2, expect, check_names=False)

    def test_幂等缓存_同条件重复求值命中缓存列(self, env):
        ctx = _ctx(env)
        work = env["base"].copy()
        cond = {"subject": "close", "action": "gt",
                "target": float(env["high"]["close"].median()),
                "timeframe": "4h"}
        m1 = mask_for(work, cond, mtf=ctx)
        cache_cols = [c for c in work.columns if c.startswith("__mtf_mask_")]
        assert len(cache_cols) == 1        # 幂等富集落列
        m2 = mask_for(work, cond, mtf=ctx)
        # 第二次命中缓存列（name=缓存列名），值与第一次逐点一致
        pd.testing.assert_series_equal(m1, m2, check_names=False)

    def test_高周期不可达_RESEARCH_SYMBOL_NOT_FOUND(self, env, tmp_path):
        """1w 无数据文件（实测数据面：值域含 1w 但数据目录到 3d）。"""
        data_dir = tmp_path / "no1w"
        data_dir.mkdir()
        env["base"].to_parquet(data_dir / f"{SYMBOL}_1h.parquet")
        ctx = MtfEvalContext(data_dir, SYMBOL, "1h")
        cond = {"subject": "close", "action": "gt", "target": 0.0,
                "timeframe": "1w"}
        with pytest.raises(ResearchError) as ei:
            mask_for(env["base"].copy(), cond, mtf=ctx)
        assert ei.value.code == "RESEARCH_SYMBOL_NOT_FOUND"

    def test_warmup不足_RESEARCH_WARMUP_INSUFFICIENT附需求可得(self, env,
                                                            tmp_path):
        """高周期数据起点晚——前扩窗口不满足显式拒绝（不静默截断）。"""
        data_dir = tmp_path / "late4h"
        data_dir.mkdir()
        env["base"].to_parquet(data_dir / f"{SYMBOL}_1h.parquet")
        # 4h 数据仅尾部 3 根（EMA5 需收敛 5×3=15 bar 前扩）
        env["high"].tail(3).to_parquet(data_dir / f"{SYMBOL}_4h.parquet")
        ctx = MtfEvalContext(data_dir, SYMBOL, "1h",
                             eval_start=env["base"].index[600])
        cond = {"subject": {"indicator": "EMA", "params": {"period": 5},
                            "field": "ema"},
                "action": "gt", "target": 0.0, "timeframe": "4h"}
        with pytest.raises(ResearchError) as ei:
            mask_for(env["base"].copy(), cond, mtf=ctx)
        assert ei.value.code == "RESEARCH_WARMUP_INSUFFICIENT"
        assert "warmup_bars" in (ei.value.detail or {})


# ---------------------------------------------------------------- inspect_cases 直接喂入（task 3.3）

class TestInspectCasesMtf:
    """spec case-inspection「混合周期假设条件直接喂入」端到端锚。"""

    def _call(self, env, selector, **kw):
        from core.analysis.case_inspection import inspect_cases

        return inspect_cases(
            env["db"], env["data_dir"], symbol=SYMBOL, timeframe="1h",
            selector=selector, **kw)

    def test_非默认参数加timeframe条件_直接喂入成功(self, env):
        """非默认参数 EMA(30) 挂 4h——结构化 subject 自带指标名
        （indicator 参数可省），任意参数现算 + 掩码投影。"""
        out = self._call(env, {"condition": {
            "subject": {"indicator": "EMA", "params": {"period": 30},
                        "field": "ema"},
            "action": "cross_above", "target": "close", "timeframe": "4h"}},
            start=env["base"].index[650].isoformat(),
            end=env["base"].index[780].isoformat(), limit=3)
        assert out["n_matched"] >= 0
        assert out["timeframe"] == "1h"
        assert len(out["cases"]) == min(out["n_matched"], 3)

    def test_低周期timeframe_结构化拒绝(self, env):
        with pytest.raises(ResearchError) as ei:
            self._call(env, {"condition": {
                "subject": {"indicator": "RSI", "params": {"period": 14},
                            "field": "rsi"},
                "action": "lt", "target": 30.0, "timeframe": "1m"}})
        assert ei.value.code == "TIMEFRAME_NOT_HIGHER"

    def test_未知timeframe_附合法清单(self, env):
        with pytest.raises(ResearchError) as ei:
            self._call(env, {"condition": {
                "subject": {"indicator": "RSI", "params": {"period": 14},
                            "field": "rsi"},
                "action": "lt", "target": 30.0, "timeframe": "2d"}})
        assert ei.value.code == "TIMEFRAME_UNKNOWN"

    def test_留痕指纹_有效周期归一(self, env):
        """ref_id 用有效周期指纹（timeframe 键优先于任务周期）。"""
        from core.analysis.condition_identity import canonical_condition_hash
        from core.persistence.repository import research as rr

        cond = {"subject": {"indicator": "RSI", "params": {"period": 14},
                            "field": "rsi"},
                "action": "lt", "target": 30.0, "timeframe": "4h"}
        self._call(env, {"condition": cond},
                   start=env["base"].index[650].isoformat(),
                   end=env["base"].index[780].isoformat())
        logs = rr.list_research_log(env["db"])
        last = [l for l in logs if l["entry_type"] == "inspect_cases"][-1]
        assert last["ref_id"] == canonical_condition_hash(cond, "1h")


# ---------------------------------------------------------------- 登记链（task 2.1/2.2）

class TestRegistrationTimeframe:
    """拒绝五类 + 成功登记 + 显式基准剥离幂等连续（B3 校验序）。"""

    def _register(self, env, when, then=None, ranges=None):
        from core.analysis.hypothesis_service import register_hypothesis

        return register_hypothesis(
            env["db"],
            conditions={"when_conditions": when,
                        "then_conditions": then or [
                            {"subject": "close", "action": "gt", "target": 0}]},
            statement="mtf 登记测试",
            data_ranges=ranges or [
                {"symbol": SYMBOL, "timeframe": "1h",
                 "start": "2023-06-01T00:00:00Z", "end": "2023-07-01T00:00:00Z"}])

    def _when(self, **extra):
        cond = {"subject": "close", "action": "gt", "target": 0.0}
        cond.update(extra)
        return [cond]

    def test_THEN携带timeframe_拒绝(self, env):
        with pytest.raises(ResearchError) as ei:
            self._register(env, self._when(),
                           then=[{"subject": "close", "action": "gt",
                                  "target": 0, "timeframe": "4h"}])
        assert ei.value.code == "TIMEFRAME_ON_THEN"
        assert "前向收益对照" in ei.value.message

    def test_未知周期_拒绝附合法清单(self, env):
        with pytest.raises(ResearchError) as ei:
            self._register(env, self._when(timeframe="2d"))
        assert ei.value.code == "TIMEFRAME_UNKNOWN"
        assert "4h" in (ei.value.detail or {}).get("legal_range", [])

    def test_低周期_拒绝附降基准指引(self, env):
        with pytest.raises(ResearchError) as ei:
            self._register(env, self._when(timeframe="1m"))
        assert ei.value.code == "TIMEFRAME_NOT_HIGHER"
        assert "降基准" in ei.value.message

    def test_区间周期不一致_拒绝(self, env):
        ranges = [
            {"symbol": SYMBOL, "timeframe": "1h",
             "start": "2023-06-01T00:00:00Z", "end": "2023-06-15T00:00:00Z"},
            {"symbol": SYMBOL, "timeframe": "4h",
             "start": "2023-06-15T00:00:00Z", "end": "2023-07-01T00:00:00Z"}]
        with pytest.raises(ResearchError) as ei:
            self._register(env, self._when(), ranges=ranges)
        assert ei.value.code == "RESEARCH_RANGE_INVALID"
        assert "基准周期" in ei.value.message

    def test_成功登记_timeframe键原样保留_显式基准剥离(self, env):
        out = self._register(env, [
            {"subject": "close", "action": "gt", "target": 0.0,
             "timeframe": "4h"},
            {"subject": "close", "action": "gt", "target": 0.0,
             "timeframe": "1h"},   # 显式基准——剥离
            {"subject": "close", "action": "gt", "target": 0.0},  # 缺省
        ])
        assert out["duplicated"] is False
        from core.persistence.repository import research as rr
        import json as _json
        hyp = rr.get_hypothesis(env["db"], out["hypothesis_id"])
        conds = _json.loads(hyp["conditions"])
        whens = conds["when_conditions"]
        assert whens[0].get("timeframe") == "4h"   # 高周期键原样保留
        assert "timeframe" not in whens[1]          # 显式基准剥离
        assert "timeframe" not in whens[2]          # 缺省无键

    def test_幂等键字面连续_显式基准命中缺省假设(self, env):
        a = self._register(env, self._when())
        b = self._register(env, self._when(timeframe="1h"))  # 显式基准
        assert b["duplicated"] is True
        assert b["hypothesis_id"] == a["hypothesis_id"]

    def test_高周期条件登记_不同身份不命中(self, env):
        a = self._register(env, self._when())
        b = self._register(env, self._when(timeframe="4h"))
        assert b["duplicated"] is False
        assert b["hypothesis_id"] != a["hypothesis_id"]


class TestRegistrationReachability:
    """路由级高周期可达校验（N2：附实际可得周期清单——「周期非法」
    与「周期无数据」语义区分；1w 无数据用例）。"""

    def _post(self, api_client, env, timeframe):
        api_client.app.state.db_path = env["db"]
        api_client.app.state.data_dir = env["data_dir"]
        resp = api_client.post("/api/research/hypotheses", json={
            "statement": "mtf 可达测试",
            "conditions": {
                "when_conditions": [
                    {"subject": "close", "action": "gt", "target": 0.0,
                     "timeframe": timeframe}],
                "then_conditions": [
                    {"subject": "close", "action": "gt", "target": 0}]},
            "data_ranges": [{"symbol": SYMBOL, "timeframe": "1h",
                             "start": "2023-06-10T00:00:00Z",
                             "end": "2023-06-20T00:00:00Z"}]})
        return resp

    def test_高周期可达_登记成功(self, api_client, env):
        resp = self._post(api_client, env, "4h")
        assert resp.status_code == 200, resp.text
        assert resp.json()["duplicated"] is False

    def test_1w无数据_拒绝附可得周期清单(self, api_client, env):
        """实测数据面：值域含 1w 但数据目录无 1w 文件——可达性拒绝
        附 find_parquet 探测的实际可得清单。"""
        resp = self._post(api_client, env, "1w")
        assert resp.status_code == 422
        body = resp.json()["detail"]
        assert body["code"] == "RESEARCH_SYMBOL_NOT_FOUND"
        avail = body["detail"]["available_timeframes"]
        assert "1h" in avail and "4h" in avail and "1w" not in avail


# ---------------------------------------------------------------- 四链一致锚（task 4.4）

class TestCrossChainConsistency:
    """同一高周期条件同数据在研究/Lab/策略/实盘四链投影逐点一致。

    - 研究链 vs Lab 链：engine._evaluate_conditions 与 condition_kernel
      mask_for 同掩码（Lab 入口形制）；
    - 研究链 vs 策略链：掩码投影（tf 键）与 resample_signals（索引
      推断 delta）逐点一致——跨族等价核心；
    - 策略链 vs 实盘链：_filter_forming_bar 时序可见性（每个评估时刻
      丢形成中 bar）与投影值逐点一致（实盘不变性——收口后实盘结果
      不变，回测对齐实盘）。
    """

    def test_研究链与Lab链_同掩码(self, env):
        from core.validation.engine import _evaluate_conditions

        ctx = _ctx(env)
        work = env["base"].copy()
        threshold = float(env["high"]["close"].median())
        cond = {"subject": "close", "action": "gt", "target": threshold,
                "timeframe": "4h"}
        m_research = mask_for(work.copy(), cond, mtf=ctx)
        m_lab = _evaluate_conditions(
            work, [cond], mtf=ctx).fillna(False).astype(bool)
        pd.testing.assert_series_equal(m_research, m_lab, check_names=False)

    def test_研究链与策略链_投影逐点一致(self, env):
        """掩码投影（tf 键走 mtf_alignment）与 executor.resample_signals
        （推断 delta 薄委托同一权威）——四链一致核心锚。"""
        from core.strategy.executor import resample_signals

        ctx = _ctx(env)
        work = env["base"].copy()
        threshold = float(env["high"]["close"].median())
        cond = {"subject": "close", "action": "gt", "target": threshold,
                "timeframe": "4h"}
        m_research = mask_for(work, cond, mtf=ctx)
        s_high = env["high"]["close"] > threshold   # 高周期轴布尔（策略链层模型）
        m_strategy = resample_signals(
            s_high.astype(bool), work.index)
        pd.testing.assert_series_equal(m_research, m_strategy,
                                       check_names=False)

    def test_策略链与实盘时序_逐点一致(self, env):
        """实盘不变性锚：模拟逐基准 bar 的评估时刻 now=基准 bar 开盘
        时刻——_filter_forming_bar 语义下可见的 4h close（收盘<=now 的
        末根）与 project_completed 投影值逐点一致。"""
        from core.data.mtf_alignment import project_completed
        from core.data.timeframes import TF_SECONDS

        base_index = env["base"].index
        close_4h = env["high"]["close"]
        projected = project_completed(close_4h, base_index, "4h")
        for i, now in enumerate(base_index):
            # _filter_forming_bar 语义：丢弃开盘+4h > now 的形成中 bar
            closed = close_4h[
                close_4h.index + pd.Timedelta(seconds=TF_SECONDS["4h"]) <= now]
            if len(closed) == 0:
                assert pd.isna(projected.iloc[i])
            else:
                assert projected.iloc[i] == pytest.approx(
                    float(closed.iloc[-1]))


# ---------------------------------------------------------------- 转化拒绝（task 5.1）

class TestTranslationReject:
    """混合周期假设转化显式拒绝（Q3=a）：登记/分析/案例检查不受影响，
    转化拒绝且无静默降级；考试链不可用为边界声明（无策略可跑，不可
    构造考试链「不受影响」断言——TD-1）。"""

    def test_when带timeframe_转化放行(self, env):
        """unblock-research-validation T10 翻转：when 组 timeframe 从
        拒绝翻转为放行——产物基因带第六属性+与显式无修饰请求同产物
        同签名（tf 严格高于基准写入基因；登记链经 canonical 化保留
        原键、翻译链提取——行为断言反向加强非仅删除）。"""
        from core.analysis.hypothesis_to_strategy import translate_hypothesis
        from core.analysis.hypothesis_service import register_hypothesis
        from core.persistence.repository.strategy import get_strategy
        import json as _json

        conds = {"when_conditions": [
            {"subject": {"indicator": "RSI", "params": {"period": 14},
                         "field": "rsi"}, "action": "lt", "target": 55,
             "timeframe": "4h"}],
            "then_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 14},
                             "field": "rsi"}, "action": "gt", "target": 70}]}
        out = register_hypothesis(
            env["db"], conditions=conds, statement="放行测试",
            data_ranges=[{"symbol": SYMBOL, "timeframe": "1h",
                          "start": "2023-06-01T00:00:00Z",
                          "end": "2023-07-01T00:00:00Z"}])
        result = translate_hypothesis(env["db"],
                                      hypothesis_id=out["hypothesis_id"],
                                      strategy_timeframe="1h")
        assert result["status"] == "draft"
        row = get_strategy(env["db"], result["strategy_id"])
        genes = _json.loads(row["dna_json"])["signal_genes"]
        assert genes and genes[0].get("timeframe") == "4h", \
            "严格高于基准的 tf 写入基因第六属性"

        # 对照：同条件显式无修饰 → 基因无 timeframe 键、签名不同
        out2 = register_hypothesis(
            env["db"],
            conditions={"when_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 14},
                             "field": "rsi"}, "action": "lt", "target": 55}],
                "then_conditions": [
                    {"subject": {"indicator": "RSI", "params": {"period": 14},
                                 "field": "rsi"}, "action": "gt",
                     "target": 70}]},
            statement="对照",
            data_ranges=[{"symbol": SYMBOL, "timeframe": "1h",
                          "start": "2023-06-01T00:00:00Z",
                          "end": "2023-07-01T00:00:00Z"}])
        # 上一假设仍 active——同身份重登记走终态变体路径：终态化后登记
        from core.analysis.hypothesis_service import transition_hypothesis
        transition_hypothesis(env["db"], out["hypothesis_id"],
                              target_status="abandoned", evidence_ref="翻转测试腾位")
        out2 = register_hypothesis(
            env["db"],
            conditions={"when_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 14},
                             "field": "rsi"}, "action": "lt", "target": 55}],
                "then_conditions": [
                    {"subject": {"indicator": "RSI", "params": {"period": 14},
                                 "field": "rsi"}, "action": "gt",
                     "target": 70}]},
            statement="对照",
            data_ranges=[{"symbol": SYMBOL, "timeframe": "1h",
                          "start": "2023-06-01T00:00:00Z",
                          "end": "2023-07-01T00:00:00Z"}])
        result2 = translate_hypothesis(env["db"],
                                       hypothesis_id=out2["hypothesis_id"],
                                       strategy_timeframe="1h")
        row2 = get_strategy(env["db"], result2["strategy_id"])
        genes2 = _json.loads(row2["dna_json"])["signal_genes"]
        assert "timeframe" not in genes2[0], "无修饰基因不含 tf 键"
        assert row2["gene_signature"] != row["gene_signature"], \
            "同基因异周期必异签名（D2）"

    def test_带键假设_求值与案例检查不受影响(self, env):
        """拒绝边界只限转化：同假设条件在求值链（掩码投影）与案例
        检查通道正常工作（登记侧已锚，此处锚求值侧同一条件可求值）。"""
        ctx = _ctx(env)
        cond = {"subject": "close", "action": "gt",
                "target": float(env["high"]["close"].min()) - 1,
                "timeframe": "4h"}
        m = mask_for(env["base"].copy(), cond, mtf=ctx)
        assert m.any()          # 求值正常（高周期键生效）


class TestMtfEvalContextLoadingGuards:
    """阶段四盲区消解：_load_tf 装载防御两分支（coverage 定向）。"""

    def test_higher_tf_data_missing_structured_reject(self, tmp_path):
        """高周期 parquet 缺失 → SYMBOL_NOT_FOUND 结构化拒绝（非裸
        FileNotFoundError / 非静默全 False）。"""
        data_dir = tmp_path / "market"
        data_dir.mkdir()
        base = make_ohlcv(n=300, freq="1h", start="2023-06-01", seed=7)
        base.to_parquet(data_dir / f"{SYMBOL}_1h.parquet")  # 无 4h
        ctx = MtfEvalContext(data_dir, SYMBOL, "1h")
        cond = {"subject": "close", "action": "gt", "target": 0.0,
                "timeframe": "4h"}
        with pytest.raises(ResearchError) as ei:
            mask_for(base.copy(), cond, mtf=ctx)
        assert ei.value.code == "RESEARCH_SYMBOL_NOT_FOUND"

    def test_naive_tz_parquet_localizes_utc(self, tmp_path):
        """高周期 parquet 无时区 → tz_localize("UTC") 兜底（装载不炸，
        求值照常产出）。"""
        data_dir = tmp_path / "market"
        data_dir.mkdir()
        base = make_ohlcv(n=300, freq="1h", start="2023-06-01", seed=7)
        base.to_parquet(data_dir / f"{SYMBOL}_1h.parquet")
        high_naive = make_ohlcv(n=100, freq="4h", start="2023-06-01", seed=7)
        high_naive.index = high_naive.index.tz_localize(None)
        high_naive.to_parquet(data_dir / f"{SYMBOL}_4h.parquet")
        ctx = MtfEvalContext(data_dir, SYMBOL, "1h")
        cond = {"subject": "close", "action": "gt",
                "target": float(high_naive["close"].min()) - 1,
                "timeframe": "4h"}
        m = mask_for(base.copy(), cond, mtf=ctx)
        assert len(m) == len(base)
        assert bool(m.any())

    def test_higher_tf_empty_parquet_structured_reject(self, tmp_path):
        """高周期 parquet 存在但空（0 bar）→ 数据不可装载结构化拒绝
        （与文件缺失的「周期不可达」分立两分支）。"""
        data_dir = tmp_path / "market"
        data_dir.mkdir()
        base = make_ohlcv(n=300, freq="1h", start="2023-06-01", seed=7)
        base.to_parquet(data_dir / f"{SYMBOL}_1h.parquet")
        pd.DataFrame(columns=["open", "high", "low", "close", "volume"]
                     ).to_parquet(data_dir / f"{SYMBOL}_4h.parquet")
        ctx = MtfEvalContext(data_dir, SYMBOL, "1h")
        cond = {"subject": "close", "action": "gt", "target": 0.0,
                "timeframe": "4h"}
        with pytest.raises(ResearchError) as ei:
            mask_for(base.copy(), cond, mtf=ctx)
        assert ei.value.code == "RESEARCH_SYMBOL_NOT_FOUND"
        assert "数据不可装载" in ei.value.message


# ------------------------------------- unblock-research-validation T2 组

import json

from core.analysis.hypothesis_service import register_hypothesis
from core.analysis.hypothesis_to_strategy import translate_hypothesis
from core.persistence.repository.strategy import get_strategy

_RANGES_1H = [{"symbol": SYMBOL, "timeframe": "1h",
               "start": "2023-06-01T00:00:00Z",
               "end": "2023-07-01T00:00:00Z"}]

_WHEN_RSI = {"subject": {"indicator": "RSI", "params": {"period": 14},
                         "field": "rsi"}, "action": "lt", "target": 55}
_THEN_RSI = {"subject": {"indicator": "RSI", "params": {"period": 30},
                         "field": "rsi"}, "action": "gt", "target": 70}


def _drop_hyp(db, conds) -> str:
    """绕过登记面校验直落假设行（翻译面纵深防御的构造入口——
    TIMEFRAME_UNKNOWN/TIMEFRAME_ON_THEN 等登记面先拦的形态只有
    直落才能到达翻译面）。"""
    import uuid as _uuid
    from core.persistence.repository import research as rr
    hid = f"hyp_{_uuid.uuid4().hex[:12]}"
    rr.save_hypothesis_with_log(
        db, hypothesis_id=hid,
        conditions=json.dumps(conds, ensure_ascii=False),
        conditions_hash=_uuid.uuid4().hex, statement="s",
        data_ranges=json.dumps(_RANGES_1H, ensure_ascii=False),
        ranges_hash=_uuid.uuid4().hex)
    return hid


class TestUnblockFourGroupGuard:
    """T2 四组三放行域缺口组（tester 补——spec delta strategy-expression
    Scenario 推导：非法键拒/等于基准归一三层全等/插件 tf 拒/加仓组
    放行键剥离/减仓抑制组拒绝+文案终稿/组合合法性）。"""

    def test_t2b_unknown_tf_rejected_with_legal_range(self, env):
        """T2b 非法周期键：when tf="2d"∉TF_SECONDS → TIMEFRAME_UNKNOWN
        附 legal_range（登记面同码族——纵深防御单点同语义）。"""
        import copy
        when = copy.deepcopy(_WHEN_RSI)
        when["timeframe"] = "2d"
        hid = _drop_hyp(env["db"], {"when_conditions": [when],
                                    "then_conditions": [_THEN_RSI]})
        with pytest.raises(ResearchError) as ei:
            translate_hypothesis(env["db"], hypothesis_id=hid,
                                 strategy_timeframe="1h")
        assert ei.value.code == "TIMEFRAME_UNKNOWN"
        assert "2d" in ei.value.message
        assert "1d" in ei.value.detail["legal_range"], "附合法清单"

    def test_t2d_prime_base_tf_normalized_three_layer(self, env):
        """T2d'/T2d'' 等于基准归一：登记面 canonical 化剥键（落库无
        tf 键+同指纹幂等命中）+翻译面归一不写入基因+与显式无修饰
        请求同产物同签名（分析面短路锚=本文件既有「显式基准等于
        缺省_同掩码」用例，三层齐）。"""
        from core.analysis.hypothesis_service import transition_hypothesis
        import copy
        # 第一层：登记面 canonical 化——tf==基准落库剥键
        when_explicit = copy.deepcopy(_WHEN_RSI)
        when_explicit["timeframe"] = "1h"          # ==基准周期
        out_a = register_hypothesis(
            env["db"],
            conditions={"when_conditions": [when_explicit],
                        "then_conditions": [_THEN_RSI]},
            statement="显式基准", data_ranges=_RANGES_1H)
        from core.persistence.repository import research as rr
        row_a = next(h for h in rr.list_hypotheses(env["db"])
                     if h["hypothesis_id"] == out_a["hypothesis_id"])
        stored = json.loads(row_a["conditions"])
        assert "timeframe" not in stored["when_conditions"][0], \
            "登记面 canonical 化剥键（显式基准与缺省同指纹）"
        # 同指纹幂等：无修饰重复登记命中 active 既有（duplicated）
        out_b = register_hypothesis(
            env["db"],
            conditions={"when_conditions": [copy.deepcopy(_WHEN_RSI)],
                        "then_conditions": [_THEN_RSI]},
            statement="无修饰", data_ranges=_RANGES_1H)
        assert out_b["hypothesis_id"] == out_a["hypothesis_id"]
        assert out_b.get("duplicated") is True, "归一后同指纹字面连续"
        # 第二层：翻译面归一——产物基因无 timeframe 键
        res_a = translate_hypothesis(env["db"],
                                     hypothesis_id=out_a["hypothesis_id"],
                                     strategy_timeframe="1h",
                                     data_dir=env["data_dir"])
        row_pa = get_strategy(env["db"], res_a["strategy_id"])
        genes_a = json.loads(row_pa["dna_json"])["signal_genes"]
        assert all("timeframe" not in g for g in genes_a), \
            "tf==基准归一不写入基因"
        # 第三层：与显式无修饰请求同产物同签名全等
        transition_hypothesis(env["db"], out_a["hypothesis_id"],
                              target_status="abandoned",
                              evidence_ref="归一锚腾位")
        out_c = register_hypothesis(
            env["db"],
            conditions={"when_conditions": [copy.deepcopy(_WHEN_RSI)],
                        "then_conditions": [_THEN_RSI]},
            statement="无修饰对照", data_ranges=_RANGES_1H)
        res_c = translate_hypothesis(env["db"],
                                     hypothesis_id=out_c["hypothesis_id"],
                                     strategy_timeframe="1h",
                                     data_dir=env["data_dir"])
        row_pc = get_strategy(env["db"], res_c["strategy_id"])
        assert row_pa["gene_signature"] == row_pc["gene_signature"], \
            "归一产物与无修饰产物同签名（不拒绝亦不异签名）"

    def test_t2e_plugin_condition_with_tf_rejected(self, env):
        """T2e 插件源条件带 timeframe：放行组内仍拒（signal_sources
        结构无周期槽位）——登记面放行（when 组）+翻译面拒绝的分层。"""
        from core.analysis.plugin_service import register_formula_plugin
        col = register_formula_plugin(env["db"], name="mom_tf",
                                      expression="zscore(close, 20)")["column"]
        when_plugin = {"subject": col, "action": "gt", "target": 0.5,
                       "timeframe": "4h"}
        out = register_hypothesis(
            env["db"],
            conditions={"when_conditions": [when_plugin],
                        "then_conditions": [_THEN_RSI]},
            statement="插件 tf", data_ranges=_RANGES_1H)
        assert out["hypothesis_id"], "登记面 when 组放行（可登记）"
        with pytest.raises(ResearchError) as ei:
            translate_hypothesis(env["db"],
                                 hypothesis_id=out["hypothesis_id"],
                                 strategy_timeframe="1h",
                                 data_dir=env["data_dir"])
        assert ei.value.code == "RESEARCH_NOT_EXPRESSIBLE"
        assert "插件列条件暂不支持 timeframe" in ei.value.message, \
            "detail 注明无槽位（spec：插件源条件携带 timeframe MUST 拒）"

    def test_t2f_1_add_trigger_group_translated_with_tf(self, env):
        """T2f-1/T2f-5 加仓组放行（补洞反向锚）：add_trigger 带 tf
        登记翻译双放行——基因 timeframe 写入+condition 纯净（键剥离，
        非旧形态静默丢弃周期语义）。"""
        import copy
        add_tf = copy.deepcopy(_WHEN_RSI)
        add_tf["timeframe"] = "4h"
        out = register_hypothesis(
            env["db"],
            conditions={"when_conditions": [copy.deepcopy(_WHEN_RSI)],
                        "then_conditions": [_THEN_RSI],
                        "add_trigger_conditions": [add_tf]},
            statement="加仓组 tf", data_ranges=_RANGES_1H)
        res = translate_hypothesis(env["db"],
                                   hypothesis_id=out["hypothesis_id"],
                                   strategy_timeframe="1h",
                                   data_dir=env["data_dir"])
        assert res["status"] == "draft"
        genes = json.loads(
            get_strategy(env["db"], res["strategy_id"])["dna_json"]
        )["signal_genes"]
        adds = [g for g in genes if g["role"] == "add_trigger"]
        assert adds and adds[0]["timeframe"] == "4h", \
            "ADD_TRIGGER 基因 tf 写入（spec：加仓触发组多周期放行）"
        assert "timeframe" not in adds[0]["condition"], \
            "condition 保持 {type,...} 纯净（键剥离）"

    @pytest.mark.parametrize("group_key", [
        "exit_partial_conditions", "add_suppress_conditions"])
    def test_t2f_3_scaling_reject_groups_copy_anchor(self, env, group_key):
        """T2f-3/T2f-5 分批拒绝组：exit_partial/add_suppress 带 tf →
        翻译面结构化拒绝+文案终稿（三组全部纳入防线——静默穿透
        洞封闭；登记面同拒见 test_strategy_param_declaration）。"""
        import copy
        bad = copy.deepcopy(_WHEN_RSI)
        bad["timeframe"] = "4h"
        conds = {"when_conditions": [copy.deepcopy(_WHEN_RSI)],
                 "then_conditions": [_THEN_RSI], group_key: [bad]}
        hid = _drop_hyp(env["db"], conds)
        with pytest.raises(ResearchError) as ei:
            translate_hypothesis(env["db"], hypothesis_id=hid,
                                 strategy_timeframe="1h")
        assert ei.value.code == "RESEARCH_NOT_EXPRESSIBLE"
        assert "仅 when 与加仓触发组支持" in ei.value.message, \
            "文案指明组别支持范围（spec：附组别支持范围指路）"

    def test_t2f_4_then_copy_final_anchor(self, env):
        """T2f-4 THEN 组文案终稿锚：then 带 tf → 翻译拒，文案含
        前向收益对照语义说明+组别支持范围（逐字锚）。"""
        import copy
        then_tf = copy.deepcopy(_THEN_RSI)
        then_tf["timeframe"] = "4h"
        hid = _drop_hyp(env["db"], {"when_conditions": [
            copy.deepcopy(_WHEN_RSI)], "then_conditions": [then_tf]})
        with pytest.raises(ResearchError) as ei:
            translate_hypothesis(env["db"], hypothesis_id=hid,
                                 strategy_timeframe="1h")
        assert ei.value.code == "RESEARCH_NOT_EXPRESSIBLE"
        assert "THEN 条件为前向收益对照语义" in ei.value.message
        assert "仅 when 与加仓触发组支持 timeframe 键" in ei.value.message

    def test_t2g_1_policy_plus_when_tf_composes_legal_draft(self, env):
        """T2g-1 组合合法性：trigger_policy+when 侧 timeframe 同假设
        → 翻译合法产 draft（不触发 MTF×policy 互斥——基因级 tf 非
        layers 分支），产物 policy 与基因 tf 双在场（防两特性错误
        互斥）。"""
        import copy
        when_tf = copy.deepcopy(_WHEN_RSI)
        when_tf["timeframe"] = "4h"
        out = register_hypothesis(
            env["db"],
            conditions={"when_conditions": [when_tf],
                        "then_conditions": [_THEN_RSI]},
            statement="policy 组合", data_ranges=_RANGES_1H,
            trigger_policy={"mode": "first_true"})
        res = translate_hypothesis(env["db"],
                                   hypothesis_id=out["hypothesis_id"],
                                   strategy_timeframe="1h",
                                   data_dir=env["data_dir"])
        assert res["status"] == "draft"
        from core.strategy.dna import StrategyDNA
        dna = StrategyDNA.from_json(
            get_strategy(env["db"], res["strategy_id"])["dna_json"])
        assert dna.entry_trigger_policy == {"mode": "first_true"}
        assert dna.signal_genes[0].timeframe == "4h"
