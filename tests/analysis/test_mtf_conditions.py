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

from core.analysis.condition_kernel import MtfEvalContext, _mask_for
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
        a = _mask_for(work, cond)
        b = _mask_for(work.copy(), cond, mtf=ctx)
        pd.testing.assert_series_equal(a, b)

    def test_显式基准等于缺省_同掩码(self, env):
        """timeframe=基准周期 → 与无键条件同掩码（同语义同求值）。"""
        work = env["base"].copy()
        plain = {"subject": "close", "action": "gt", "target": 0.0}
        explicit = {**plain, "timeframe": "1h"}
        ctx = MtfEvalContext(env["data_dir"], SYMBOL, "1h")
        pd.testing.assert_series_equal(
            _mask_for(work, plain, mtf=ctx),
            _mask_for(work.copy(), explicit, mtf=ctx))

    def test_无ctx带高周期键_显式ResearchError(self, env):
        """D-final 兜底退役：工程接线错误显式拒绝（非静默回退值投影）。"""
        cond = {"subject": "close", "action": "gt", "target": 0.0,
                "timeframe": "4h"}
        with pytest.raises(ResearchError) as ei:
            _mask_for(env["base"].copy(), cond)
        assert ei.value.code == "RESEARCH_MTF_CONTEXT_MISSING"

    def test_低周期键_TIMEFRAME_NOT_HIGHER(self, env):
        cond = {"subject": "close", "action": "gt", "target": 0.0,
                "timeframe": "1m"}
        ctx = MtfEvalContext(env["data_dir"], SYMBOL, "1h")
        with pytest.raises(ResearchError) as ei:
            _mask_for(env["base"].copy(), cond, mtf=ctx)
        assert ei.value.code == "TIMEFRAME_NOT_HIGHER"
        assert "降基准" in ei.value.message

    def test_未知周期键_TIMEFRAME_UNKNOWN附合法清单(self, env):
        cond = {"subject": "close", "action": "gt", "target": 0.0,
                "timeframe": "2d"}
        ctx = MtfEvalContext(env["data_dir"], SYMBOL, "1h")
        with pytest.raises(ResearchError) as ei:
            _mask_for(env["base"].copy(), cond, mtf=ctx)
        assert ei.value.code == "TIMEFRAME_UNKNOWN"
        assert "1h" in (ei.value.detail or {}).get("legal_range", [])

    def test_字面后缀列名subject_照常列引用命中(self, env):
        """无 timeframe 键的后缀列名（merged 列形制）在 work 上精确命中
        ——不进掩码路径（B5：merge_to_base 降格为列引用投影器）。"""
        work = env["base"].copy()
        work["ema_20_4h"] = 1.0           # 模拟 Lab merged 后缀列
        work.loc[work.index[10], "ema_20_4h"] = 100.0
        cond = {"subject": "ema_20_4h", "action": "gt", "target": 50.0}
        mask = _mask_for(work, cond)
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

        m3 = _mask_for(work.copy(), cond3)
        m5 = _mask_for(work.copy(), cond5)
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
        m = _mask_for(work, cond, mtf=ctx)
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
        m = _mask_for(work, cond, mtf=ctx)
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
        m_a = _mask_for(work_full, cond,
                        mtf=MtfEvalContext(env["data_dir"], SYMBOL, "1h",
                                           eval_start=env["base"].index[200]))
        m_b = _mask_for(env["base"].iloc[:-4].copy(), cond,
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
        m = _mask_for(work, cond, mtf=ctx)
        # 独立对照（_compute_indicator 返回 {列名: series}）
        ema5 = _compute_indicator(env["high"].copy(), "EMA", {"period": 5})
        col = [c for c in ema5 if c.startswith("ema")][0]
        cross = (ema5[col].shift(1) < 0) & (ema5[col] >= 0)  # 恒 False（价>0）
        assert not m.any()   # 阈值 0 下穿越不可能——安全语义锚
        # 换可触发语义：close cross_above EMA5（高周期轴，列名类 target）
        cond2 = {"subject": "close",
                 "action": "cross_above",
                 "target": col, "timeframe": "4h"}
        m2 = _mask_for(env["base"].copy(), cond2, mtf=ctx)
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
        m1 = _mask_for(work, cond, mtf=ctx)
        cache_cols = [c for c in work.columns if c.startswith("__mtf_mask_")]
        assert len(cache_cols) == 1        # 幂等富集落列
        m2 = _mask_for(work, cond, mtf=ctx)
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
            _mask_for(env["base"].copy(), cond, mtf=ctx)
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
            _mask_for(env["base"].copy(), cond, mtf=ctx)
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
      _mask_for 同掩码（Lab 入口形制）；
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
        m_research = _mask_for(work.copy(), cond, mtf=ctx)
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
        m_research = _mask_for(work, cond, mtf=ctx)
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
