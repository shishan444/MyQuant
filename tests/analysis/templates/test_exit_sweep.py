"""退出参数扫描测试（add-research-analysis-tools 0.4.1 任务 6.8，B6 用例组）。

两轨：真实引擎轨道（网格完整性/费滑/异步/留痕/S5 双向守护/纯噪声
结构性质）+ 判定轨道（monkeypatch run_backtest_on_range 注入受控
metrics——三道门四值与最差聚合的确定性构造，引擎路径另轨覆盖）。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

import core.analysis.backtest_bridge as bb
from core.analysis.hypothesis_service import ResearchError
from core.analysis.isolation import judged_ranges_union
from core.analysis.templates.exit_sweep import exit_sweep
from core.persistence.db_ext import init_db_ext

SYMBOL = "BTCUSDT"
RNG_DEF = {"symbol": SYMBOL, "timeframe": "4h",
           "start": "2023-04-01T00:00:00Z", "end": "2023-06-01T00:00:00Z"}
_COND = {"subject": {"indicator": "RSI", "params": {"period": 14},
                     "field": "rsi"}, "action": "lt", "target": 55}


def _make_env(tmp_path: Path, n: int = 1400, freq_noise: float = 260.0):
    """高频触发数据（先降后升双趋势——RSI<55 常态触发，交易充分）。"""
    d = tmp_path / "market"
    d.mkdir(exist_ok=True)
    rng = np.random.default_rng(11)
    idx = pd.date_range("2023-01-01", periods=n, freq="4h", tz="UTC",
                        name="timestamp")
    seg1 = np.linspace(70000, 56000, n // 2)
    seg2 = np.linspace(56000, 76000, n - n // 2)
    close = np.concatenate([seg1, seg2]) + rng.normal(0, freq_noise, n)
    pd.DataFrame({"open": close, "high": close + 150, "low": close - 150,
                  "close": close, "volume": rng.uniform(100, 900, n),
                  "trades": 40}, index=idx).to_parquet(
        d / f"{SYMBOL}_4h.parquet")
    db = tmp_path / "q.db"
    init_db_ext(db)
    return d, db


def _random_walk_ohlcv(n: int, seed: int) -> pd.DataFrame:
    """纯随机游走 OHLCV（B6 结构性质用例——无任何可 exploitation 结构）。"""
    rng = np.random.default_rng(seed)
    steps = rng.normal(0, 150, n)
    close = 60000 + np.cumsum(steps)
    return pd.DataFrame({
        "open": close, "high": close + 80, "low": close - 80,
        "close": close, "volume": rng.uniform(100, 900, n), "trades": 40,
    }, index=pd.date_range("2023-01-01", periods=n, freq="4h", tz="UTC",
                           name="timestamp"))


def _grid(stops, tps):
    return {"modes": [{"mode": "pct", "stops": list(stops),
                       "take_profits": list(tps)}]}


def _fake_bridge(states_by_pos):
    """受控 metrics 注入（判定轨道）——按 (stop, tp) 序偶给出
    (total_trades, trade_returns) 映射的假回测桥。"""
    def _fake(db_path, data_dir, dna, symbol, timeframe, start, end,
              warmup_bars, **kw):
        key = (round(dna.risk_genes.stop_loss, 6),
               round(dna.risk_genes.take_profit, 6))
        trades, rets = states_by_pos[key]
        metrics = {"total_return": float(sum(rets)), "sharpe_ratio": 1.0,
                   "max_drawdown": 0.1,
                   "win_rate": (sum(1 for r in rets if r > 0) / len(rets)
                                if rets else None),
                   "total_trades": trades, "liquidated": False,
                   "alpha": 0.0, "market_annual_return": 0.0,
                   "benchmark_return": None, "excess_return": None}
        return metrics, list(rets), 200, 500
    return _fake


class TestValidationOrder:
    """校验确定序（①→⑥全序固定）与七项拒绝面。"""

    @pytest.fixture(autouse=True)
    def _env(self, tmp_path):
        self.d, self.db = _make_env(tmp_path)

    def _reject(self, grid, keyword):
        with pytest.raises(ResearchError) as ei:
            exit_sweep(self.db, self.d, dict(RNG_DEF), condition=_COND,
                       direction="long", grid=grid)
        return ei.value

    def test_order_reports_first_violation(self):
        """全序锚：同时违反②（非法模式）与⑥（超限）报②。"""
        exc = self._reject({"modes": [
            {"mode": "trailing", "stops": [0.01] * 10,
             "take_profits": [0.02] * 6},
        ]}, "模式")
        assert "引擎未支持" in exc.message  # ② 先于 ⑥

    def test_duplicate_mode_rejected(self):
        """①mode 唯一性：重复模式条目拒绝（detail 指明重复模式名）。"""
        exc = self._reject({"modes": [
            {"mode": "pct", "stops": [0.01], "take_profits": [0.02]},
            {"mode": "pct", "stops": [0.02], "take_profits": [0.03]},
        ]}, "")
        assert "重复模式" in exc.message
        assert exc.detail["value"] == "pct"

    def test_unsupported_mode_rejected_with_legal_set(self):
        """②非法模式（S5 联动）：未知/形变模式 422 披露合法集（常量
        引用值不写字面）。0.4.2 扩分派后 atr/trail 已入合法集——本锚
        的 bad 集合同任务翻转（原 atr/trail 用例退役）。"""
        from core.backtest.engine import ENGINE_SUPPORTED_SL_MODES
        for bad in ("trailing", "pct ", "ATR"):
            exc = self._reject(_grid([0.01], [0.02]) | {
                "modes": [{"mode": bad, "stops": [0.01],
                           "take_profits": [0.02]}]}, "")
            assert "引擎未支持" in exc.message
            assert exc.detail["legal_range"] == list(
                ENGINE_SUPPORTED_SL_MODES)

    def test_empty_lists_rejected(self):
        """③空列表拒（乘积视 0 静默零行）。"""
        for key in ("stops", "take_profits"):
            grid = {"modes": [{"mode": "pct", "stops": [0.01],
                               "take_profits": [0.02]}]}
            grid["modes"][0][key] = []
            exc = self._reject(grid, "")
            assert "非空列表" in exc.message

    @pytest.mark.parametrize(
        ("value", "ok"),
        [(0.004, False), (0.005, True), (0.20, True), (0.21, False)],
        ids=["0.004 拒", "0.005 过", "0.20 过", "0.21 拒"])
    def test_stop_value_range_referenced(self, value, ok):
        """④值域模式感知（引用式断言——SL_PCT_MIN/MAX 同源四点）。"""
        from core.strategy.validator import SL_PCT_MAX, SL_PCT_MIN
        assert (SL_PCT_MIN, SL_PCT_MAX) == (0.005, 0.20)
        if ok:
            out = exit_sweep(self.db, self.d, dict(RNG_DEF),
                             condition=_COND, direction="long",
                             grid=_grid([value], [0.02]))
            assert out["sample_size"] == 1
        else:
            exc = self._reject(_grid([value], [0.02]), "")
            assert exc.detail["legal_range"] == [0.005, 0.20]

    @pytest.mark.parametrize(
        ("bad", "kind"),
        [(None, "非数值"), (0, "非正值"), (-0.02, "负值")],
        ids=["None 拒", "零拒", "负拒"])
    def test_take_profit_positive_required(self, bad, kind):
        """⑤止盈条目必填正值（A5：None/非正值——None 落非数值拒绝面）。"""
        exc = self._reject(_grid([0.01], [bad]), "")
        assert exc.code == "RESEARCH_PARAM_INVALID"
        assert "take_profits" in exc.detail["param"]

    def test_total_groups_cap(self):
        """⑥上限 Σ≤50：49/50 过、51 拒（跨模式合计）。"""
        out = exit_sweep(self.db, self.d, dict(RNG_DEF), condition=_COND,
                         direction="long",
                         grid=_grid([0.01] * 7, [0.02] * 7))  # 49
        assert out["sample_size"] == 49
        exc = self._reject(_grid([0.01] * 7 + [0.015], [0.02] * 7 - [0.02]),
                           "") if False else self._reject(
            _grid([0.01] * 7, [0.02] * 7 + [0.03]), "")
        assert exc.detail["limit"] == 50

    def test_two_phase_distinction_red_anchor(self):
        """两期分野红锚：校验期越界=整批 422 无部分结果——不存在
        「越界只标组继续跑」（通道混同必红）。"""
        exc = self._reject(_grid([0.01], [0.02] * 51), "")
        assert exc.code == "RESEARCH_PARAM_INVALID"
        assert "results" not in (exc.detail or {})

    def test_grid_structure_rejected(self):
        """闸门盲区消解：校验序①原始面——grid 非 dict / modes 非
        列表 / 条目缺三键，全部 422 结构化拒绝。"""
        for bad in (["pct"],  # 非 dict
                    {"modes": "pct"},  # modes 非列表
                    {"modes": []},  # 空列表
                    {"modes": [{"mode": "pct", "stops": [0.01]}]}):  # 缺 tp
            exc = self._reject(bad, "")
            assert exc.code == "RESEARCH_PARAM_INVALID", bad
            assert exc.detail["param"] in ("grid", "grid.modes[0]")

    def test_direct_call_direction_and_either_or_rejected(self):
        """闸门盲区消解：模板直调层 direction 枚举与二选一拒绝面
        （路由层键组先拦，直调防线独立存在——与 counter_profile
        直调层同族）。"""
        with pytest.raises(ResearchError) as ei:
            exit_sweep(self.db, self.d, dict(RNG_DEF), condition=_COND,
                       direction="mixed", grid=_grid([0.01], [0.02]))
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["param"] == "direction"
        assert ei.value.detail["legal_range"] == ["long", "short"]

        with pytest.raises(ResearchError) as ei2:
            exit_sweep(self.db, self.d, dict(RNG_DEF), direction="long",
                       grid=_grid([0.01], [0.02]))  # 两键全缺
        assert ei2.value.code == "RESEARCH_PARAM_INVALID"
        assert ei2.value.detail["param"] == "condition|conditions"
        with pytest.raises(ResearchError) as ei3:
            exit_sweep(self.db, self.d, dict(RNG_DEF), condition=_COND,
                       conditions=[_COND], direction="long",
                       grid=_grid([0.01], [0.02]))  # 两键同传
        assert ei3.value.detail["param"] == "condition|conditions"


class TestConditionsDomainValidation:
    """CARD-05：exit_sweep 条件条数域+形制校验前置（共享层单点
    validate_conditions_array——与 condition_stats/event_study/
    counter_profile 路由同源）。三缺口修复锚：×6 条原 200 放行、
    [] 原误导 NOT_EXPRESSIBLE、缺三键原走翻译链报 NOT_EXPRESSIBLE。"""

    @pytest.fixture(autouse=True)
    def _env(self, tmp_path):
        self.d, self.db = _make_env(tmp_path)

    def _run(self, **kw):
        params: dict = {"direction": "long",
                        "grid": _grid([0.01], [0.02])}
        params.update(kw)
        return exit_sweep(self.db, self.d, dict(RNG_DEF), **params)

    @pytest.mark.parametrize(
        ("conditions", "code"),
        [([_COND] * 6, "RESEARCH_PARAM_INVALID"),
         ([], "RESEARCH_PARAM_INVALID"),
         ([{"subject": "close", "action": "gt"}],
          "RESEARCH_CONDITION_INVALID")],
        ids=["6 条超限拒", "空数组拒", "缺三键形制拒"])
    def test_conditions_domain_and_form(self, conditions, code):
        with pytest.raises(ResearchError) as ei:
            self._run(conditions=conditions)
        assert ei.value.code == code

    def test_single_condition_form_checked_symmetrically(self):
        """单条件包装 [condition] 同受校验（对称覆盖——缺三键 422
        CONDITION_INVALID，原走翻译链）。"""
        with pytest.raises(ResearchError) as ei:
            self._run(condition={"subject": "close", "action": "gt"})
        assert ei.value.code == "RESEARCH_CONDITION_INVALID"

    def test_legal_conditions_and_single_unchanged(self):
        """合法对照：指标类 conditions/单条件两形制照常执行（verdict
        四值域——修复只收拒绝面不动合法路径）。"""
        for kw in ({"conditions": [_COND]}, {"condition": _COND}):
            out = self._run(**kw)
            assert out["payload"]["verdict"] in (
                "plateau", "indeterminate", "spike", "unsupported")


class TestExecutionAndGates:
    """真实引擎轨道：网格完整性/费滑/两期分野执行侧/劣结构/纯噪声。"""

    def test_grid_20_rows_complete_with_fees(self, tmp_path):
        """20 组网格（5×4 单模式 pct）结果行完整、费滑已计入（与手算
        对照：net_expectation == trade_returns 均值，费滑引擎侧已含）。"""
        d, db = _make_env(tmp_path)
        out = exit_sweep(db, d, dict(RNG_DEF), condition=_COND,
                         direction="long",
                         grid=_grid([0.01, 0.02, 0.03, 0.05, 0.08],
                                    [0.02, 0.04, 0.06, 0.08]))
        rows = out["payload"]["results"]
        assert len(rows) == 20
        assert out["sample_size"] == 20
        for r in rows:
            assert r["status"] == "ok"
            assert {"mode", "stop_loss", "take_profit", "win_rate",
                    "profit_factor", "net_expectation", "max_drawdown",
                    "total_trades", "status", "state"} <= set(r)
            assert isinstance(r["max_drawdown"], float)  # 引擎直出
        # 费滑计入锚：挑一组经 run_backtest_on_range 独立重放对照
        r0 = rows[0]
        from core.analysis.hypothesis_to_strategy import build_signal_genes
        from core.strategy.dna import (ExecutionGenes, LogicGenes,
                                       RiskGenes, StrategyDNA)
        genes, _ = build_signal_genes([_COND], [], db)
        dna = StrategyDNA(
            signal_genes=genes, logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(symbol=SYMBOL, timeframe="4h"),
            risk_genes=RiskGenes(stop_loss=r0["stop_loss"],
                                 take_profit=r0["take_profit"],
                                 direction="long"),
            signal_sources=[{"type": "rule"}])
        metrics, rets, _, _ = bb.run_backtest_on_range(
            db, d, dna, SYMBOL, "4h", RNG_DEF["start"], RNG_DEF["end"],
            out["payload"]["warmup_bars"])
        assert r0["total_trades"] == metrics["total_trades"]
        assert r0["net_expectation"] == pytest.approx(float(np.mean(rets)))

    def test_single_group_failure_does_not_block(self, tmp_path, monkeypatch):
        """执行期单组容错：构造一组回测失败 → 该组标注、其余正常。"""
        d, db = _make_env(tmp_path)
        orig = bb.run_backtest_on_range

        def flaky(db_path, data_dir, dna, *a, **kw):
            if abs(dna.risk_genes.stop_loss - 0.02) < 1e-9:
                raise ResearchError("RESEARCH_DATA_INSUFFICIENT", "构造失败")
            return orig(db_path, data_dir, dna, *a, **kw)

        monkeypatch.setattr(bb, "run_backtest_on_range", flaky)
        out = exit_sweep(db, d, dict(RNG_DEF), condition=_COND,
                         direction="long",
                         grid=_grid([0.01, 0.02, 0.03], [0.04]))
        rows = out["payload"]["results"]
        assert [r["status"] for r in rows] == ["ok", "failed", "ok"]
        assert rows[1]["failure_code"] == "RESEARCH_DATA_INSUFFICIENT"
        assert len(out["payload"]["failed_groups"]) == 1
        assert out["sample_size"] == 2

    def test_deepcopy_group_independence(self, tmp_path):
        """DNA 注入：各组参数互不污染（deepcopy 副本独立；平台默认
        position_size/leverage 不动）。"""
        d, db = _make_env(tmp_path)
        out = exit_sweep(db, d, dict(RNG_DEF), condition=_COND,
                         direction="long",
                         grid=_grid([0.01, 0.03], [0.02, 0.06]))
        pairs = [(r["stop_loss"], r["take_profit"])
                 for r in out["payload"]["results"]]
        assert pairs == [(0.01, 0.02), (0.01, 0.06),
                         (0.03, 0.02), (0.03, 0.06)]  # 展开三层序
        for r in out["payload"]["results"]:
            assert r["stop_loss"] in (0.01, 0.03)  # 无跨组污染

    def test_bad_structure_tp_below_stop_runs_honestly(self, tmp_path):
        """劣结构组合锚：tp<stop 的网格组校验期不拒——正常回测、结果
        如实呈现、三道门自然判 ineffective。"""
        d, db = _make_env(tmp_path)
        out = exit_sweep(db, d, dict(RNG_DEF), condition=_COND,
                         direction="long",
                         grid=_grid([0.05], [0.01]))  # tp < stop
        r = out["payload"]["results"][0]
        assert r["status"] == "ok"  # 不替用户筛掉坏选项

    def test_pure_random_walk_never_plateau(self, tmp_path):
        """纯噪声结构性质：随机游走数据下合法落点仅 spike/unsupported/
        indeterminate——绝不 plateau（无结构可 exploitation）。"""
        d = tmp_path / "market"
        d.mkdir()
        df = _random_walk_ohlcv(1400, seed=42)
        df.to_parquet(d / f"{SYMBOL}_4h.parquet")
        db = tmp_path / "q.db"
        init_db_ext(db)
        out = exit_sweep(db, d, dict(RNG_DEF), condition=_COND,
                         direction="long",
                         grid=_grid([0.01, 0.02, 0.03, 0.05, 0.08],
                                    [0.02, 0.04, 0.06, 0.08]))
        assert out["payload"]["verdict"] in ("spike", "unsupported",
                                             "indeterminate")


class TestThreeGates:
    """判定轨道（受控注入）：三道门四值+矩阵最差聚合+标尺内建。"""

    @pytest.fixture(autouse=True)
    def _env(self, tmp_path, monkeypatch):
        self.d, self.db = _make_env(tmp_path)
        self._mp = monkeypatch

    def _run(self, states, stops=None, tps=None):
        stops = stops or [0.01, 0.02, 0.03, 0.04]
        tps = tps or [0.03, 0.06]
        table = {(s, t): states.get((s, t), (0, []))
                 for s in stops for t in tps}
        self._mp.setattr(bb, "run_backtest_on_range", _fake_bridge(table))
        return exit_sweep(self.db, self.d, dict(RNG_DEF), condition=_COND,
                          direction="long", grid=_grid(stops, tps))

    def test_plateau_with_robust_ranges(self):
        """平坦区：全部 effective → plateau + 各轴稳健区间（人工游程
        对照——两轴各 4/2 值连续）。"""
        eff = (8, [0.01, 0.02, 0.015])
        out = self._run({(s, t): eff for s in (0.01, 0.02, 0.03, 0.04)
                         for t in (0.03, 0.06)})
        assert out["payload"]["verdict"] == "plateau"
        axes = out["payload"]["robust_ranges"]["pct"]["axes"]
        assert axes["stop_loss"]["robust_range"] == [0.01, 0.04]
        assert axes["take_profit"]["robust_range"] == [0.03, 0.06]

    def test_spike_single_effective_point(self):
        """尖峰：仅单点 effective → spike。"""
        out = self._run({(0.02, 0.06): (6, [0.01])})
        assert out["payload"]["verdict"] == "spike"

    def test_single_axis_plateau_other_axis_single_point(self):
        """矩阵用例（最差聚合证明）：止损轴连续有效段 × 止盈轴孤立
        有效点 → 整体 spike（任一轴弱则整体弱）。"""
        states = {(s, 0.06): (7, [0.01]) for s in (0.01, 0.02, 0.03, 0.04)}
        out = self._run(states)
        # 止损轴：4 值全 effective（各 stop 至少一组 effective）→ plateau；
        # 止盈轴：仅 0.06 有 effective 组 → 单点 → spike；最差 → spike
        assert out["payload"]["verdict"] == "spike"
        axes = out["payload"]["robust_ranges"]["pct"]["axes"]
        assert axes["stop_loss"]["verdict"] == "plateau"
        assert axes["take_profit"]["verdict"] == "spike"

    def test_all_ineffective_scored_unsupported(self):
        """无 effective 有参评 → unsupported。"""
        out = self._run({(s, t): (8, [-0.01]) for s in (0.01, 0.02)
                         for t in (0.03, 0.06)})
        assert out["payload"]["verdict"] == "unsupported"

    def test_qualification_gate_boundary(self):
        """资格门临界 4/5 两侧：交易数 4=ineffective、5+净期望>0=effective。"""
        out = self._run({(0.01, 0.03): (4, [0.01]), (0.02, 0.03): (5, [0.01])})
        rows = {r["stop_loss"]: r for r in out["payload"]["results"]
                if r["take_profit"] == 0.03}
        assert rows[0.01]["state"] == "ineffective"
        assert rows[0.02]["state"] == "effective"

    def test_effectiveness_gate_strictly_positive(self):
        """真伪门净期望>0 严格：恰 0 = ineffective。"""
        out = self._run({(0.01, 0.03): (6, [0.0, 0.0])})
        rows = [r for r in out["payload"]["results"]
                if r["take_profit"] == 0.03]
        assert rows[0]["net_expectation"] == 0.0
        assert rows[0]["state"] == "ineffective"

    def test_all_win_profit_factor_null_kept(self):
        """全胜组 profit_factor=null 键保留（亏损笔数 0——诚实缺失）。"""
        out = self._run({(0.01, 0.03): (6, [0.01, 0.02])})
        row = next(r for r in out["payload"]["results"]
                   if (r["stop_loss"], r["take_profit"]) == (0.01, 0.03))
        assert "profit_factor" in row
        assert row["profit_factor"] is None

    def test_gates_disclosed_and_not_injectable(self):
        """gates 披露五键；判定参数不可注入（携带 min_trades/gates 键
        判定仍按内建）。"""
        eff = (8, [0.01])
        table = {(s, t): eff for s in (0.01, 0.02) for t in (0.03, 0.06)}
        self._mp.setattr(bb, "run_backtest_on_range", _fake_bridge(table))
        out = exit_sweep(self.db, self.d, dict(RNG_DEF), condition=_COND,
                         direction="long",
                         grid=_grid([0.01, 0.02], [0.03, 0.06]) | {
                             "min_trades": 1, "gates": {"min_run": 9}})
        assert out["payload"]["gates"] == {
            "min_trades": 5, "min_run": 2,
            "effectiveness": "net_expectation_after_fees>0",
            "axes": ["stop_loss", "take_profit"], "aggregation": "worst"}
        assert out["payload"]["verdict"] == "plateau"  # 按内建标尺

    def test_axis_run_manual_crosscheck(self):
        """轴判定与人工游程核算独立对照：构造 stop 轴 1100-0110 形态
        （有效段长 2 两段）——游程 ≥2 → plateau。"""
        states = {  # stop 0.01,0.02 effective；0.03,0.04 ineffective
            (0.01, 0.03): (7, [0.01]), (0.02, 0.03): (7, [0.01]),
            (0.03, 0.03): (7, [-0.01]), (0.04, 0.03): (7, [-0.01]),
            (0.01, 0.06): (7, [-0.01]), (0.02, 0.06): (7, [-0.01]),
            (0.03, 0.06): (7, [0.01]), (0.04, 0.06): (7, [-0.01]),
        }
        out = self._run(states)
        axes = out["payload"]["robust_ranges"]["pct"]["axes"]
        # 人工核算：stop 轴 effective 投影 {0.01,0.02,0.03}（0.03 在
        # tp=0.06 行 effective）→ 连续段长 3 → plateau；tp 轴投影
        # {0.03,0.06}（0.03 行与 0.06 行各有 eff 组）→ 连续 → plateau
        # → 整体 plateau
        assert axes["stop_loss"]["verdict"] == "plateau"
        assert axes["stop_loss"]["robust_range"] == [0.01, 0.03]
        assert out["payload"]["verdict"] == "plateau"

    def test_single_value_axis_inference_disclosed(self):
        """单轴长度 1 推论披露：固定止损只扫止盈 → stop 轴入
        single_value_axes，整体恒不超 spike（预期行为非可疑信号）。"""
        out = self._run({(0.02, t): (7, [0.01]) for t in (0.03, 0.06)},
                        stops=[0.02])
        assert out["payload"]["single_value_axes"] == ["pct.stop_loss"]
        assert out["payload"]["verdict"] in ("spike", "unsupported",
                                             "indeterminate")

    def test_worst_order_parametrized(self):
        """整链 unsupported 场景（全组 trades=0 参评全无效——有参评点
        → unsupported，与全组失败的 indeterminate 分野）；_worst 纯
        函数两两全序见 TestWorstAggregationOrder（CARD-15）。"""
        orig_states = {}  # 全组无条目 → (0, []) → ineffective
        # 全部 trades=0 且 rets=[] → ineffective → 有参评 → unsupported
        out = self._run(orig_states)
        assert out["payload"]["verdict"] == "unsupported"


class TestWorstAggregationOrder:
    """CARD-15：_worst 最差聚合全序纯函数参数化——四值两两 6 对
    （_VERDICT_ORDER：plateau=0 < indeterminate=1 < spike=2 <
    unsupported=3，取序值大者=更差者）双向断言；两两完备覆盖即全序
    传递性证明（无序对全枚举）。"""

    @pytest.mark.parametrize(
        ("a", "b", "worst"),
        [("plateau", "indeterminate", "indeterminate"),
         ("plateau", "spike", "spike"),
         ("plateau", "unsupported", "unsupported"),
         ("indeterminate", "spike", "spike"),
         ("indeterminate", "unsupported", "unsupported"),
         ("spike", "unsupported", "unsupported")],
        ids=["plateau+indeterminate", "plateau+spike",
             "plateau+unsupported", "indeterminate+spike",
             "indeterminate+unsupported", "spike+unsupported"])
    def test_worst_pairwise_bidirectional(self, a, b, worst):
        from core.analysis.templates.exit_sweep import _worst
        assert _worst(a, b) == worst
        assert _worst(b, a) == worst  # 交换律——聚合序与传入序无关

    def test_all_groups_failed_indeterminate(self, tmp_path, monkeypatch):
        """四值完备性补齐（indeterminate 此前无测试触达）：全组执行期
        失败 → 两轴 indeterminate、整体 indeterminate（证据不足非证伪
        ——与 unsupported 的分野：后者有参评组全无效）；results 全
        failed 如实呈现、failed_groups 完整、sample_size=0。"""
        d, db = _make_env(tmp_path)

        def _always_fail(*a, **kw):
            raise ResearchError("RESEARCH_DATA_INSUFFICIENT", "构造全组失败")

        monkeypatch.setattr(bb, "run_backtest_on_range", _always_fail)
        out = exit_sweep(db, d, dict(RNG_DEF), condition=_COND,
                         direction="long",
                         grid=_grid([0.01, 0.02], [0.03, 0.06]))
        assert out["payload"]["verdict"] == "indeterminate"
        axes = out["payload"]["robust_ranges"]["pct"]["axes"]
        assert axes["stop_loss"]["verdict"] == "indeterminate"
        assert axes["take_profit"]["verdict"] == "indeterminate"
        assert all(r["status"] == "failed" for r in out["payload"]["results"])
        assert all(r["failure_code"] == "RESEARCH_DATA_INSUFFICIENT"
                   for r in out["payload"]["results"])
        assert len(out["payload"]["failed_groups"]) == 4
        assert out["sample_size"] == 0
        # confidence 三要素照常携带（gates_verdict 型——四值披露）
        assert out["confidence"]["verdict"] == "indeterminate"


class TestCapabilityDomainAndLog:
    """timeframe 拒绝锚/能力域对照/S5 双向守护/留痕不进并集。"""

    @pytest.fixture(autouse=True)
    def _env(self, tmp_path):
        self.d, self.db = _make_env(tmp_path)

    def test_timeframe_condition_accepted_and_low_tf_rejected(self):
        """unblock-research-validation T10 翻转：when 侧 timeframe 从
        拒绝翻转为放行——高周期条件（1d@4h 基准）经翻译链成功执行
        扫描；反向加强：低于基准的 tf（30m）仍被防线拒绝（校验链
        TIMEFRAME_NOT_HIGHER——错误码/detail 命名锚保持）。"""
        # 1d 高周期数据（掩码投影装载）
        idx_1d = pd.date_range("2023-01-01", periods=90, freq="1d",
                               tz="UTC", name="timestamp")
        rng = np.random.default_rng(5)
        c1d = 60000 + np.cumsum(rng.normal(0, 300, 90))
        pd.DataFrame({"open": c1d, "high": c1d + 150, "low": c1d - 150,
                      "close": c1d, "volume": rng.uniform(100, 900, 90),
                      "trades": 40}, index=idx_1d).to_parquet(
            self.d / f"{SYMBOL}_1d.parquet")
        cond = dict(_COND, timeframe="1d")
        out = exit_sweep(self.db, self.d, dict(RNG_DEF), condition=cond,
                         direction="long", grid=_grid([0.01], [0.02]))
        assert out["template"] == "exit_sweep"
        assert isinstance(out["payload"]["results"], list)
        # 反向加强：低于基准仍拒（校验链第二步）
        with pytest.raises(ResearchError) as ei:
            exit_sweep(self.db, self.d, dict(RNG_DEF),
                       condition=dict(_COND, timeframe="30m"),
                       direction="long", grid=_grid([0.01], [0.02]))
        assert ei.value.code == "TIMEFRAME_NOT_HIGHER"

    def test_capability_domain_both_supported(self, api_client):
        """能力域对照锚（unblock-research-validation 翻转）：同一
        timeframe 条件在 condition_stats（分析域掩码投影）与
        exit_sweep（翻译链基因级 tf）双双正常执行——两域能力对齐，
        防防线回潮（双向：任一侧回到拒绝即红）。"""
        api_client.app.state.db_path = self.db
        api_client.app.state.data_dir = self.d
        cond = dict(_COND, timeframe="1d")
        # 需 1d 数据供掩码投影与高周期装载
        idx_1d = pd.date_range("2023-01-01", periods=250, freq="1d",
                               tz="UTC", name="timestamp")
        rng = np.random.default_rng(5)
        c1d = 60000 + np.cumsum(rng.normal(0, 300, 250))
        pd.DataFrame({"open": c1d, "high": c1d + 150, "low": c1d - 150,
                      "close": c1d, "volume": rng.uniform(100, 900, 250),
                      "trades": 40}, index=idx_1d).to_parquet(
            self.d / f"{SYMBOL}_1d.parquet")
        ok = api_client.post("/api/research/analysis", json={
            "template": "condition_stats",
            "params": {"indicator": "RSI", "params": {"period": 14},
                       "condition": cond, "min_samples": 1,
                       "forward_bars": 6},
            "data_range": dict(RNG_DEF)})
        assert ok.status_code == 200, ok.text  # 掩码投影路径正常执行
        ok2 = api_client.post("/api/research/analysis", json={
            "template": "exit_sweep",
            "params": {"condition": cond, "direction": "long",
                       "grid": _grid([0.01], [0.02])},
            "data_range": dict(RNG_DEF)})
        assert ok2.status_code == 200, ok2.text  # 翻译链放行同族支持

    def test_engine_sl_mode_dispatch_double_guard(self):
        """S5 双向守护（0.4.2 分派实施后翻转形态）：pct/atr 双注入
        （各自语义正确值——pct 比例 0.02/0.04、atr 倍数 2.0/4.0）经
        共享符号回测结果**可区分** → 引擎消费 sl_mode（atr 按 ATR 倍数
        执行，非按 pct 锚定误执行）；atr/trail 必在引擎分派集内（常量
        回退必红）。0.4.1 原锚（逐位一致+在集外）随引擎分派实施同任务
        原子翻转——先扩常量后改本锚=红窗口，反之亦然。"""
        import copy as _copy

        from core.analysis.hypothesis_to_strategy import build_signal_genes
        from core.backtest.engine import ENGINE_SUPPORTED_SL_MODES
        from core.strategy.dna import (ExecutionGenes, LogicGenes,
                                       RiskGenes, StrategyDNA)
        genes, _ = build_signal_genes([_COND], [], self.db)
        base = StrategyDNA(
            signal_genes=genes, logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(symbol=SYMBOL, timeframe="4h"),
            risk_genes=RiskGenes(), signal_sources=[{"type": "rule"}])
        outs = {}
        for mode, sl, tp in (("pct", 0.02, 0.04), ("atr", 2.0, 4.0)):
            dna = _copy.deepcopy(base)
            dna.risk_genes.stop_loss = sl
            dna.risk_genes.take_profit = tp
            dna.risk_genes.sl_mode = mode
            m, rets, _, _ = bb.run_backtest_on_range(
                self.db, self.d, dna, SYMBOL, "4h",
                RNG_DEF["start"], RNG_DEF["end"], 200)
            outs[mode] = (m, list(rets))
        # 可区分断言：atr 按 ATR 倍数执行（止损=入场∓2×ATR）与 pct
        # 比例锚定（入场×0.98）在同一数据上结果不同——若引擎回退为不
        # 消费 sl_mode（atr 按 pct 静默误执行）则逐位一致，本锚变红
        assert outs["pct"][0] != outs["atr"][0] or \
            outs["pct"][1] != outs["atr"][1]
        # 分派集守护：atr/trail 在引擎真实分派集内（常量回退必红）
        assert "atr" in ENGINE_SUPPORTED_SL_MODES
        assert "trail" in ENGINE_SUPPORTED_SL_MODES

    @pytest.mark.parametrize(
        ("mode", "value", "ok"),
        [("atr", 2.0, True), ("atr", 0.5, True), ("atr", 5.0, True),
         ("atr", 8.0, False), ("atr", 0.05, False),
         ("pct", 2.0, False), ("pct", 0.05, True),
         ("trail", 0.05, True), ("trail", 2.0, False)],
        ids=["atr 2.0 过", "atr 0.5 过", "atr 5.0 过", "atr 8.0 拒",
             "atr 0.05 拒（误放防护）", "pct 2.0 拒", "pct 0.05 过",
             "trail 0.05 过", "trail 2.0 拒"])
    def test_mode_forked_value_domain(self, mode, value, ok):
        """④值域模式感知分档（0.4.2 值域配套改造——量纲双关的值域义务
        在消费方）：atr 档引 SL_ATR_MIN/MAX（倍数域），pct/trail 档引
        SL_PCT_MIN/MAX（比例域，trail 回撤比例与 pct 同域）——同值异判
        （2.0 在 atr 合法、在 pct/trail 非法；0.05 在 pct/trail 合法、
        在 atr 非法）。"""
        from core.strategy.validator import (
            SL_ATR_MAX, SL_ATR_MIN, SL_PCT_MAX, SL_PCT_MIN,
        )
        assert (SL_ATR_MIN, SL_ATR_MAX) == (0.5, 5.0)  # 引用式锚（常量单源）
        assert (SL_PCT_MIN, SL_PCT_MAX) == (0.005, 0.20)
        grid = {"modes": [{"mode": mode, "stops": [value],
                           "take_profits": [0.02] if mode != "atr"
                           else [4.0]}]}
        if ok:
            out = exit_sweep(self.db, self.d, dict(RNG_DEF),
                             condition=_COND, direction="long", grid=grid)
            assert out["sample_size"] == 1
        else:
            with pytest.raises(ResearchError) as ei:
                exit_sweep(self.db, self.d, dict(RNG_DEF), condition=_COND,
                           direction="long", grid=grid)
            expect_lo, expect_hi = ((SL_ATR_MIN, SL_ATR_MAX)
                                    if mode == "atr"
                                    else (SL_PCT_MIN, SL_PCT_MAX))
            assert ei.value.detail["legal_range"] == [expect_lo, expect_hi]

    def test_log_entry_not_in_judged_union(self, api_client):
        """留痕不进并集：成功执行后 judged_ranges_union 不含扫描区间；
        entry_type=exit_sweep、hypothesis_id 空、summary 四键。"""
        from core.analysis.hypothesis_service import register_hypothesis
        hyp = register_hypothesis(
            self.db,
            conditions={"when_conditions": [_COND],
                        "then_conditions": [dict(_COND)]},
            statement="s",
            data_ranges=[dict(RNG_DEF)])["hypothesis_id"]
        api_client.app.state.db_path = self.db
        api_client.app.state.data_dir = self.d
        resp = api_client.post("/api/research/analysis", json={
            "template": "exit_sweep",
            "params": {"condition": _COND, "direction": "long",
                       "grid": _grid([0.01, 0.02], [0.03])},
            "data_range": dict(RNG_DEF),
            "hypothesis_id": hyp})  # 携带 hypothesis_id 也被模板忽略
        assert resp.status_code == 200, resp.text
        import sqlite3
        with sqlite3.connect(self.db) as conn:
            rows = conn.execute(
                "SELECT entry_type, hypothesis_id, summary FROM research_log "
                "WHERE entry_type = 'exit_sweep'").fetchall()
        assert len(rows) == 1
        assert rows[0][1] is None  # 不挂假设
        summary = json.loads(rows[0][2])
        assert set(summary) == {"verdict", "robust_ranges", "grid_groups",
                                "failed_groups"}
        # 扫描区间不进判定并集（与登记区间可分离）
        union = judged_ranges_union(self.db, hyp)
        assert all(not (r["start"] < RNG_DEF["end"]
                        and r["end"] > RNG_DEF["start"]) or
                   r == {"start": "2023-04-01T00:00:00+00:00",
                         "end": "2023-06-01T00:00:00+00:00"}
                   for r in union)


class TestAsyncThreshold:
    """组数异步阈值（6.6）：9 组同步直返 / 11 组走 202+轮询到终态。"""

    def test_nine_groups_sync_direct(self, api_client, tmp_path):
        d, db = _make_env(tmp_path)
        api_client.app.state.db_path = db
        api_client.app.state.data_dir = d
        resp = api_client.post("/api/research/analysis", json={
            "template": "exit_sweep",
            "params": {"condition": _COND, "direction": "long",
                       "grid": _grid([0.01] * 9, [0.02])},
            "data_range": dict(RNG_DEF)})
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["payload"]["results"]) == 9

    def test_eleven_groups_async_handle(self, api_client, tmp_path):
        d, db = _make_env(tmp_path)
        api_client.app.state.db_path = db
        api_client.app.state.data_dir = d
        # executor 单例在 lifespan 起动时绑定原 db——重绑到本用例 db
        #（照 test_analysis_warmup_route 既有形制）
        import core.analysis.executor as exec_mod
        old = exec_mod._EXECUTOR
        exec_mod._EXECUTOR = None
        exec_mod.init_executor(db)
        try:
            resp = api_client.post("/api/research/analysis", json={
                "template": "exit_sweep",
                "params": {"condition": _COND, "direction": "long",
                           "grid": _grid([0.01] * 11, [0.02])},
                "data_range": dict(RNG_DEF)})
            assert resp.status_code == 202, resp.text
            task_id = resp.json()["task_id"]
            # 轮询到终态
            import time
            body = {}
            for _ in range(120):
                poll = api_client.get(
                    f"/api/research/analysis/tasks/{task_id}")
                if poll.status_code == 404:
                    time.sleep(0.5)  # 任务行落库竞态——视同 pending
                    continue
                assert poll.status_code == 200
                body = poll.json()
                if body.get("status") in ("succeeded", "failed"):
                    break
                time.sleep(0.5)
            assert body["status"] == "succeeded", body
            result = body["result"]["payload"] if isinstance(
                body.get("result"), dict) else json.loads(
                body["result"])["payload"]
            assert len(result["results"]) == 11
        finally:
            exec_mod._EXECUTOR = old
