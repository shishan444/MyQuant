"""案例抽查服务单测（add-inspect-cases 任务 3.1）。

验收匹配能力类型（0.2.7 原则）：数据检视工具验取数正确性——期望值
独立于被测内核推导（测试内自调 _compute_indicator 现算 rsi_14 列做
numpy 直接比较，不用 _mask_for 生成自身期望）。三测试类：

- TestSelectorAndLimits：参数化拒绝面 + action 防降级锚（断言拒绝码
  而非 200+空清单——engine 对未知 action 静默全 False 是降级路径）
  + subject 越列 + 扫描候选三键直喂零翻译。
- TestSamplingAndWindows：位置真相独立推导 + 等距取样（首尾保底/
  H4 中位/不足全返）+ trigger_index 指向性 + bar 守恒 + H3 end 外扩
  + 确定性 + timestamps 归位/去重/超发。
- TestDisciplineAndAudit：指标口径独立对照 + 有效域披露 + 留痕指纹
  + H1 拒绝零副作用 + F1 体积两断言 + F2-F6 六组断言。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

import core.analysis.case_inspection as ci
from core.analysis.case_inspection import (
    _MAX_SERIALIZED_BYTES,
    inspect_cases,
)
from core.analysis.condition_identity import canonical_condition_hash
from core.analysis.hypothesis_service import ResearchError
from core.analysis.registry_meta import derive_warmup
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import research as rr
from tests.helpers.data_factory import make_ohlcv

SYMBOL = "BTCUSDT"
N_BARS = 800
WARMUP = derive_warmup(["RSI"])  # max(200, 14*3) = 200


@pytest.fixture()
def env(tmp_path: Path):
    """800 bar 1d 数据 + 独立 db（regime_split api 先例形制）。"""
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    df = make_ohlcv(n=N_BARS, freq="1d", start="2023-06-01", seed=13)
    df.to_parquet(data_dir / f"{SYMBOL}_1d.parquet")
    db = tmp_path / "quant.db"
    init_db_ext(db)
    return {"data_dir": data_dir, "db": db, "df": df}


def _expected_hits(df: pd.DataFrame, action: str = "lt",
                   target: float = 30.0) -> tuple[np.ndarray, np.ndarray]:
    """位置真相：测试内独立现算 rsi_14 列 numpy 比较（不经被测模块）。

    Returns:
        (有效域命中位置数组, rsi 数值数组)。
    """
    from core.features.indicators import _compute_indicator

    cols = _compute_indicator(df.copy(), "RSI", {"period": 14})
    rsi = cols["rsi_14"]
    rv = rsi.to_numpy()
    domain = np.arange(len(df)) >= WARMUP
    if action == "lt":
        cond = rv < target
    elif action == "gt":
        cond = rv > target
    elif action == "touch":
        tol = max(abs(target) * 0.005, float(np.nanstd(rv)) * 0.1)
        cond = np.abs(rv - target) <= tol
    elif action == "spike":
        ra = rsi.rolling(20, min_periods=1).mean().to_numpy()
        cond = rv >= ra * target
    else:  # pragma: no cover - 测试内仅用上述动作
        raise ValueError(action)
    return np.where(cond & domain)[0], rv


def _call(env, **kwargs):
    """缺省 condition 形态调用（rsi_14 lt 30 / indicator=RSI）。"""
    params = dict(
        symbol=SYMBOL, timeframe="1d",
        selector={"condition": {"subject": {"indicator": "RSI", "params": {"period": 14}},
                                "action": "lt", "target": 30.0}},
        indicator="RSI")
    params.update(kwargs)
    return inspect_cases(env["db"], env["data_dir"], **params)


def _err(exc_info) -> ResearchError:
    return exc_info.value


class TestSelectorAndLimits:
    """参数与 selector 结构拒绝面（结构校验=零装载零留痕）。"""

    @pytest.mark.parametrize("limit,ok", [(0, False), (6, False),
                                          (1, True), (5, True)])
    def test_limit_bounds(self, env, limit, ok):
        if ok:
            out = _call(env, limit=limit)
            assert 1 <= len(out["cases"]) <= limit
        else:
            with pytest.raises(ResearchError) as ei:
                _call(env, limit=limit)
            assert _err(ei).code == "RESEARCH_PARAM_INVALID"
            assert ei.value.detail["param"] == "limit"
            assert ei.value.detail["legal_range"] == "1..5"

    @pytest.mark.parametrize("cb,ok", [(0, False), (21, False),
                                       (1, True), (20, True)])
    def test_context_bars_bounds(self, env, cb, ok):
        if ok:
            out = _call(env, context_bars=cb)
            case = out["cases"][0]
            assert case["bars_before"] <= cb and case["bars_after"] <= cb
        else:
            with pytest.raises(ResearchError) as ei:
                _call(env, context_bars=cb)
            assert _err(ei).code == "RESEARCH_PARAM_INVALID"
            assert ei.value.detail["param"] == "context_bars"

    @pytest.mark.parametrize("field", ["limit", "context_bars"])
    def test_bool_excluded(self, env, field):
        with pytest.raises(ResearchError) as ei:
            _call(env, **{field: True})
        assert _err(ei).code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["param"] == field

    def test_selector_dual_keys_rejected(self, env):
        with pytest.raises(ResearchError) as ei:
            _call(env, selector={"condition": {"subject": {"indicator": "RSI", "params": {"period": 14}},
                                               "action": "lt", "target": 30.0},
                                 "timestamps": ["2024-01-01T00:00:00+00:00"]})
        assert _err(ei).code == "RESEARCH_PARAM_INVALID"

    def test_selector_empty_rejected(self, env):
        with pytest.raises(ResearchError):
            _call(env, selector={})

    def test_selector_missing_form_rejected(self, env):
        with pytest.raises(ResearchError):
            _call(env, selector={"cond": {}})

    def test_condition_missing_key_rejected(self, env):
        with pytest.raises(ResearchError):
            _call(env, selector={"condition": {"subject": {"indicator": "RSI", "params": {"period": 14}},
                                               "action": "lt"}})

    def test_timestamps_overrun_rejected(self, env):
        df = env["df"]
        with pytest.raises(ResearchError) as ei:
            _call(env, selector={"timestamps": [
                df.index[i].isoformat() for i in range(6)]})
        assert _err(ei).code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["param"] == "selector.timestamps"

    def test_timestamps_five_ok(self, env):
        df = env["df"]
        out = _call(env, selector={"timestamps": [
            df.index[i].isoformat() for i in (300, 350, 400, 450, 500)]})
        assert len(out["cases"]) == 5
        assert out["n_matched"] == 5

    def test_unknown_action_rejected_not_silent(self, env):
        """防降级锚：未知 action 必须拒绝（engine 静默全 False 是降级路径，
        不前置拦截会把调用错误伪装成无命中）。"""
        with pytest.raises(ResearchError) as ei:
            _call(env, selector={"condition": {"subject": {"indicator": "RSI", "params": {"period": 14}},
                                               "action": "frobnicate",
                                               "target": 30.0}})
        assert _err(ei).code == "RESEARCH_PARAM_INVALID"
        assert "legal_range" in ei.value.detail

    def test_subject_not_in_output_columns(self, env):
        """字符串 subject 越列仍拒（字符串不携带参数——默认边界即语义）；
        结构化 subject（异指标）0.2.14 起放行走任意参数现算（D-r3）。"""
        with pytest.raises(ResearchError) as ei:
            _call(env, selector={"condition": {"subject": "funding_rate",
                                               "action": "lt", "target": 30.0}})
        assert _err(ei).code == "RESEARCH_COLUMN_NOT_AVAILABLE"
        assert "rsi_14" in ei.value.detail["legal_range"]
        # 结构化异指标：resolve 校验即放行（非默认参数条件直接喂入）
        out = _call(env, selector={"condition": {
            "subject": {"indicator": "ADX", "params": {"period": 14}},
            "action": "lt", "target": 30.0}})
        assert out["n_matched"] >= 0   # 成功执行（命中数由数据决定）

    def test_scan_candidate_direct_feed(self, env):
        """扫描候选三键直喂零翻译：n_matched 与独立推导一致。"""
        hits, _ = _expected_hits(env["df"])
        out = _call(env, limit=3)
        assert out["n_matched"] == len(hits)
        assert out["sampling"] == "even"


class TestSamplingAndWindows:
    """取样与窗口契约（位置真相独立推导）。"""

    def test_hit_positions_truth(self, env):
        """返回触发位置与独立推导的等距取样位置一一对应。"""
        hits, _ = _expected_hits(env["df"])
        expected_pos = hits[[0, 5, 10]]  # linspace(0,10,3) = 0,5,10
        out = _call(env, limit=3)
        got_ts = [c["trigger_ts"] for c in out["cases"]]
        want_ts = [env["df"].index[p].isoformat() for p in expected_pos]
        assert got_ts == want_ts

    def test_even_sampling_first_last_guaranteed(self, env):
        hits, _ = _expected_hits(env["df"])
        out = _call(env, limit=3)
        assert out["cases"][0]["trigger_ts"] == env["df"].index[hits[0]].isoformat()
        assert out["cases"][-1]["trigger_ts"] == env["df"].index[hits[-1]].isoformat()
        # n_matched 与独立计数一致（抽查代表性披露）
        assert out["n_matched"] == len(hits)

    def test_limit1_median_not_first(self, env):
        """H4：limit=1 取中位命中（等距单点退化是中心，非 linspace 端点）。"""
        hits, _ = _expected_hits(env["df"])
        median_pos = hits[round((len(hits) - 1) / 2)]
        out = _call(env, limit=1)
        assert len(out["cases"]) == 1
        assert out["cases"][0]["trigger_ts"] == env["df"].index[median_pos].isoformat()
        assert median_pos != hits[0]

    def test_fewer_hits_return_all(self, env):
        """命中不足限额全返（RSI<25 独立推导恰 1 处）。"""
        hits, _ = _expected_hits(env["df"], target=25.0)
        assert len(hits) == 1
        out = _call(env, selector={"condition": {"subject": {"indicator": "RSI", "params": {"period": 14}},
                                                 "action": "lt",
                                                 "target": 25.0}},
                    limit=3)
        assert out["n_matched"] == 1
        assert len(out["cases"]) == 1

    def test_trigger_index_points_at_trigger(self, env):
        out = _call(env, limit=5)
        for case in out["cases"]:
            assert case["window"][case["trigger_index"]][0] == case["trigger_ts"]

    def test_bar_conservation(self, env):
        """bar 守恒：bars_before + bars_after + 1 == len(window)。"""
        out = _call(env, limit=5, context_bars=6)
        for case in out["cases"]:
            assert case["bars_before"] + case["bars_after"] + 1 == len(case["window"])

    def test_tail_trigger_truncation_disclosed(self, env):
        """尾部触发：点名数据最后一根 → bars_after=0 如实披露不拒绝。"""
        df = env["df"]
        out = _call(env, selector={"timestamps": [df.index[-1].isoformat()]})
        case = out["cases"][0]
        assert case["bars_after"] == 0
        assert case["bars_before"] == 8
        assert len(case["window"]) == 9

    def test_end_expansion_h3(self, env):
        """H3：end 显式装载终点外扩 context_bars 根（尾部窗口完整）。

        期望独立证明：窗口末端 bar ts > end 即证外扩生效（缺失则实现
        退化为一传 end 不外扩、半窗被截断披露义务掩盖）。

        注：本区间外扩区 (290,298] 恰零命中——对求值域上界缺陷不敏感
        （守护该语义的是下方 test_end_domain_excludes_expansion_zone）。
        """
        df = env["df"]
        hits, _ = _expected_hits(df)  # [262, 287, 288, 289, ...]
        start, end = df.index[250], df.index[290]
        out = _call(env, start=start.isoformat(), end=end.isoformat(),
                    limit=5)
        # 请求区间 [250,290] 内命中 4 处（求值域上界=end，外扩区不产命中）
        in_range = [p for p in hits if 250 <= p <= 290]
        assert len(in_range) == 4
        assert out["n_matched"] == 4
        for case, p in zip(out["cases"], in_range):
            assert case["bars_after"] == 8  # 外扩后窗口完整（不因 end 截断）
            if 290 - p <= 8:  # 距 end ≤ context_bars 的命中：窗口跨过 end 边界
                assert case["window"][-1][0] > end.isoformat()

    def test_end_domain_excludes_expansion_zone(self, env):
        """求值域上界=end 的敏感锚（fix-inspect-cases-end-domain 1.1）。

        区间 [715,722]：区间内命中 722 一处、外扩区 (722,730] 含
        725/726/727 三处——无上界实现 n_matched=4 必红（外扩区混入），
        正确实现 n_matched=1。期望独立推导对照，不用被测路径生成。
        """
        df = env["df"]
        hits, _ = _expected_hits(df)
        start, end = df.index[715], df.index[722]
        in_range = [p for p in hits if 715 <= p <= 722]
        expansion = [p for p in hits if 722 < p <= 730]
        assert len(in_range) == 1 and in_range[0] == 722  # 构造前提自检
        assert len(expansion) == 3  # 外扩区含命中——敏感性前提
        out = _call(env, start=start.isoformat(), end=end.isoformat(),
                    limit=5)
        assert out["n_matched"] == 1  # 无上界实现此处为 4
        assert len(out["cases"]) == 1
        assert out["cases"][0]["trigger_ts"] == df.index[722].isoformat()

    def test_determinism_byte_identical(self, env):
        a = _call(env, limit=3)
        b = _call(env, limit=3)
        assert json.dumps(a, ensure_ascii=False) == json.dumps(b, ensure_ascii=False)

    def test_timestamp_alignment_and_reachability(self, env):
        """点名归位 index <= ts 最后位置（非 bar 时点）；早于首 bar 拒。"""
        df = env["df"]
        pos = 400
        off_bar = (df.index[pos] + pd.Timedelta(hours=1)).isoformat()
        out = _call(env, selector={"timestamps": [off_bar]})
        assert out["cases"][0]["trigger_ts"] == df.index[pos].isoformat()
        with pytest.raises(ResearchError) as ei:
            _call(env, selector={"timestamps": [
                (df.index[0] - pd.Timedelta(days=1)).isoformat()]})
        assert _err(ei).code == "RESEARCH_DATA_INSUFFICIENT"

    def test_duplicate_timestamps_overrun_by_raw_count(self, env):
        """H2：原始 6 个含 1 对重复按原始名单数拒（先判超发后去重）。"""
        df = env["df"]
        base = [df.index[300 + i].isoformat() for i in range(5)]
        raw = base + [base[0]]  # 6 个原始、去重后 5
        with pytest.raises(ResearchError) as ei:
            _call(env, selector={"timestamps": raw})
        assert _err(ei).code == "RESEARCH_PARAM_INVALID"

    def test_duplicate_dedupe_and_sorted_output(self, env):
        """F6：重复去重（n_matched 去重计数、echo 回显原始）+ 乱序升序。"""
        df = env["df"]
        t200, t300, t400 = (df.index[i].isoformat() for i in (200, 300, 400))
        out = _call(env, selector={"timestamps": [t400, t200, t300, t200]})
        assert out["n_matched"] == 3
        assert out["selector_echo"]["timestamps"] == [t400, t200, t300, t200]
        ts_list = [c["trigger_ts"] for c in out["cases"]]
        assert ts_list == sorted(ts_list)
        assert out["sampling"] == "explicit"


class TestDisciplineAndAudit:
    """口径独立对照 + 有效域 + 留痕 + 零副作用 + 体积 + F2-F6。"""

    def test_indicator_values_independent(self, env):
        """指标口径独立对照：触发点读数与 df 列直接取值逐字段一致。"""
        _, rsi = _expected_hits(env["df"])
        out = _call(env, limit=5)
        for case in out["cases"]:
            pos = env["df"].index.get_loc(pd.Timestamp(case["trigger_ts"]))
            want = float(rsi[pos])
            assert case["indicator_values"]["rsi_14"] == pytest.approx(want)

    def test_effective_start_and_warmup_exclusion(self, env):
        """缺省全量：effective_start=首 bar+warmup 位置；warmup 段不入命中。"""
        df = env["df"]
        out = _call(env, limit=3)
        assert out["effective_start"] == df.index[WARMUP].isoformat()
        for case in out["cases"]:
            assert pd.Timestamp(case["trigger_ts"]) >= df.index[WARMUP]

    def test_audit_trail_fingerprint(self, env):
        df = env["df"]
        cond = {"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt", "target": 30.0}
        out = _call(env, selector={"condition": cond})
        rows = [r for r in rr.list_research_log(env["db"])
                if r["entry_type"] == "inspect_cases"]
        assert len(rows) == 1
        assert rows[0]["ref_id"] == canonical_condition_hash(cond, "1d")
        assert rows[0]["hypothesis_id"] is None
        ranges = json.loads(rows[0]["data_ranges"])
        assert ranges["symbol"] == SYMBOL and ranges["timeframe"] == "1d"
        assert "start" in ranges and "end" in ranges
        assert out["limit"] == 3  # 披露回显

    def test_rejection_zero_side_effect(self, env):
        """H1 分层：结构校验拒绝零留痕；列校验拒绝（装载后）零留痕。"""
        _call(env)  # 一条成功留痕作基数
        base = len(rr.list_research_log(env["db"]))
        with pytest.raises(ResearchError):  # 结构校验（装载前）
            _call(env, limit=8)
        with pytest.raises(ResearchError):  # 列校验（装载现算后——字符串越列）
            _call(env, selector={"condition": {"subject": "funding_rate",
                                               "action": "lt", "target": 30.0}})
        with pytest.raises(ResearchError):  # timestamps 超发
            _call(env, selector={"timestamps": [
                env["df"].index[i].isoformat() for i in range(6)]})
        assert len(rr.list_research_log(env["db"])) == base

    def test_payload_size_budget_and_guard(self, env, monkeypatch):
        """F1 体积两断言：极限组合真实序列化 ≤24576；超限分支契约。"""
        out = _call(env, selector={"condition": {"subject": {"indicator": "RSI", "params": {"period": 14}},
                                                 "action": "gt",
                                                 "target": 40.0}},
                    limit=5, context_bars=20)
        assert out["n_matched"] >= 5  # 极限组合成立前提
        serialized = json.dumps(out, ensure_ascii=False).encode("utf-8")
        assert len(serialized) <= _MAX_SERIALIZED_BYTES
        # 超限分支：阈值压到 100 字节触发（防御对象=形制退化）
        import core.analysis.case_inspection as ci

        monkeypatch.setattr(ci, "_MAX_SERIALIZED_BYTES", 100)
        with pytest.raises(ResearchError) as ei:
            ci.inspect_cases(env["db"], env["data_dir"], symbol=SYMBOL,
                             timeframe="1d",
                             selector={"condition": {"subject": {"indicator": "RSI", "params": {"period": 14}},
                                                     "action": "gt",
                                                     "target": 40.0}},
                             limit=5, context_bars=20, indicator="RSI")
        assert _err(ei).code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["bytes"] > ei.value.detail["limit"] == 100

    def test_pattern_action_rejected_with_reason(self, env):
        """F2：pattern 族动作拒绝 detail 分层说明。"""
        with pytest.raises(ResearchError) as ei:
            _call(env, selector={"condition": {"subject": {"indicator": "RSI", "params": {"period": 14}},
                                               "action": "divergence_top",
                                               "target": 30.0}})
        assert _err(ei).code == "RESEARCH_PARAM_INVALID"
        assert "pattern" in ei.value.detail.get("reason", "")

    @pytest.mark.parametrize("action,target,expect_hits", [
        ("touch", 45.0, 33),   # 容差带命中（独立推导 33）
        ("spike", 1.2, 81),    # 滚动均值倍数命中（独立推导 81）
    ])
    def test_nonnumeric_actions_hit(self, env, action, target, expect_hits):
        """14 键内非数值动作可正常命中（不被校验层误拒/降级）。"""
        hits, _ = _expected_hits(env["df"], action=action, target=target)
        assert len(hits) == expect_hits
        out = _call(env, selector={"condition": {"subject": {"indicator": "RSI", "params": {"period": 14}},
                                                 "action": action,
                                                 "target": target}},
                    limit=3)
        assert out["n_matched"] == len(hits)

    def test_target_column_missing_rejected(self, env):
        with pytest.raises(ResearchError) as ei:
            _call(env, selector={"condition": {"subject": {"indicator": "RSI", "params": {"period": 14}},
                                               "action": "cross_above",
                                               "target": "no_such_col"}})
        assert _err(ei).code == "RESEARCH_COLUMN_NOT_AVAILABLE"

    def test_numeric_string_target_passes(self, env):
        """数值 target 不受列校验（"25" 可解析直过）。"""
        hits, _ = _expected_hits(env["df"], target=25.0)
        out = _call(env, selector={"condition": {"subject": {"indicator": "RSI", "params": {"period": 14}},
                                                 "action": "lt",
                                                 "target": "25"}})
        assert out["n_matched"] == len(hits) == 1

    def test_zero_hits_valid_empty_result(self, env):
        """F4：命中 0 → 200 空清单 + 留痕照落（合法检查产出）。"""
        base = len(rr.list_research_log(env["db"]))
        out = _call(env, selector={"condition": {"subject": {"indicator": "RSI", "params": {"period": 14}},
                                                 "action": "lt",
                                                 "target": 18.0}})
        assert out["n_matched"] == 0
        assert out["cases"] == []
        assert len(rr.list_research_log(env["db"])) == base + 1

    def test_timestamps_with_start_rejected(self, env):
        """F5：timestamps 与区间参数同传拒（点名自带定位）。"""
        df = env["df"]
        with pytest.raises(ResearchError) as ei:
            _call(env, selector={"timestamps": [df.index[300].isoformat()]},
                  start=df.index[250].isoformat())
        assert _err(ei).code == "RESEARCH_PARAM_INVALID"

    def test_short_data_none_indicator_tolerance(self, env, tmp_path):
        """None 容错：指标输出列在该位置不可得（NaN）→ indicator_values
        如实 None 不崩溃。点名数据首根（RSI 第 0 根 NaN，实证
        pandas_ta min_periods 行为）；兼验头部截断披露 bars_before=0。"""
        data_dir = tmp_path / "tiny"
        data_dir.mkdir()
        df = make_ohlcv(n=60, freq="1d", start="2024-01-01", seed=7)
        df.to_parquet(data_dir / f"{SYMBOL}_1d.parquet")
        db = tmp_path / "tiny.db"
        init_db_ext(db)
        out = inspect_cases(db, data_dir, symbol=SYMBOL, timeframe="1d",
                            selector={"timestamps": [df.index[0].isoformat()]},
                            indicator="RSI")
        case = out["cases"][0]
        assert case["indicator_values"]["rsi_14"] is None
        assert case["bars_before"] == 0
        assert len(case["window"]) == 9


class TestGapClosureValidation:
    """阶段四盲区消解：selector/target 校验族与容错分支直测
    （_parse_selector 装载前校验，无数据依赖）。"""

    def test_selector非对象_拒(self) -> None:
        with pytest.raises(ResearchError, match="selector"):
            ci._parse_selector("not-a-dict", None, None)

    def test_双形态同传_拒(self) -> None:
        with pytest.raises(ResearchError):
            ci._parse_selector(
                {"condition": {"subject": "close", "action": "gt",
                               "target": 1},
                 "timestamps": ["2024-01-01"]}, None, None)

    def test_condition缺键_拒(self) -> None:
        with pytest.raises(ResearchError, match="三键"):
            ci._parse_selector({"condition": {"subject": "close"}}, None, None)

    def test_action非字符串_拒(self) -> None:
        with pytest.raises(ResearchError, match="action"):
            ci._parse_selector(
                {"condition": {"subject": "close", "action": 1,
                               "target": 1}}, None, None)

    def test_subject非法_结构化拒绝(self) -> None:
        with pytest.raises(ResearchError) as ei:
            ci._parse_selector(
                {"condition": {"subject": {"indicator": "GHOST"},
                               "action": "gt", "target": 1}}, None, None)
        assert ei.value.code == "RESEARCH_CONDITION_INVALID"

    def test_subject类型错_拒(self) -> None:
        with pytest.raises(ResearchError, match="subject"):
            ci._parse_selector(
                {"condition": {"subject": 123, "action": "gt",
                               "target": 1}}, None, None)

    def test_timestamps空_拒(self) -> None:
        with pytest.raises(ResearchError, match="非空"):
            ci._parse_selector({"timestamps": []}, None, None)

    def test_timestamps超发_拒(self) -> None:
        with pytest.raises(ResearchError):
            ci._parse_selector(
                {"timestamps": ["2024-01-01"] * (ci._MAX_TIMESTAMPS + 1)},
                None, None)

    def test_timestamps坏项_拒(self) -> None:
        with pytest.raises(ResearchError, match="不可解析"):
            ci._parse_selector({"timestamps": ["2024-01-01", "not-a-ts"]},
                               None, None)

    def test_timestamps形态带区间参数_拒(self) -> None:
        with pytest.raises(ResearchError, match="区间参数"):
            ci._parse_selector({"timestamps": ["2024-01-01"]},
                               "2024-01-01", "2024-02-01")

    def test_target_bool_放行(self) -> None:
        ci._validate_target(True, pd.Index(["close"]))  # 比较标志语义

    def test_target数值与可解析串_放行(self) -> None:
        ci._validate_target(100, pd.Index([]))
        ci._validate_target(100.5, pd.Index([]))
        ci._validate_target("100.5", pd.Index([]))  # 可解析串直过

    def test_target列名不在册且不可解码_拒(self) -> None:
        with pytest.raises(ResearchError) as ei:
            ci._validate_target("ghost_col", pd.Index(["close"]))
        assert ei.value.code == "RESEARCH_COLUMN_NOT_AVAILABLE"

    def test_target编码形列名_放行(self) -> None:
        ci._validate_target("rsi_14", pd.Index(["close"]))  # decode 放行

    def test_target类型非法_拒(self) -> None:
        with pytest.raises(ResearchError, match="类型非法"):
            ci._validate_target([1, 2], pd.Index(["close"]))


class TestGapClosureInspect3:
    """第三批：装载与求值域边界 + 多输出列缺失 None。

    不可达判定（第三批，装载层责任/前置拦截）：①indicator 缺填
    分支——_parse_selector 的 resolve_column_ref 对无 indicator 键的
    结构化 subject 先拦；②load None 分支——load_parquet 对损坏文件
    抛底层异常；③naive 索引 tz_localize——装载比较先炸。"""

    def test_无该档数据_SYMBOL_NOT_FOUND(self, env):
        with pytest.raises(ResearchError) as ei:
            ci.inspect_cases(env["db"], env["data_dir"], symbol=SYMBOL,
                             timeframe="5m",
                             selector={"condition": {
                                 "subject": {"indicator": "RSI",
                                             "params": {"period": 14}},
                                 "action": "lt", "target": 45}},
                             limit=3)
        assert ei.value.code == "RESEARCH_SYMBOL_NOT_FOUND"

    def test_数据不足有效域_start缺省(self, env):
        """start 不传 + 数据量不足 warmup 需求（150 根 < 200）→ 拒。"""
        import numpy as np
        import pandas as pd
        data_dir = env["data_dir"].parent / "tiny"
        data_dir.mkdir(exist_ok=True)
        idx = pd.date_range("2024-01-01", periods=150, freq="1d",
                            tz="UTC", name="timestamp")
        close = np.full(150, 100.0)
        pd.DataFrame({"open": close, "high": close, "low": close,
                      "close": close,
                      "volume": np.full(150, 10.0)},
                     index=idx).to_parquet(data_dir / f"{SYMBOL}_1d.parquet")
        with pytest.raises(ResearchError, match="有效域"):
            ci.inspect_cases(env["db"], data_dir, symbol=SYMBOL,
                             timeframe="1d",
                             selector={"condition": {
                                 "subject": {"indicator": "RSI",
                                             "params": {"period": 14}},
                                 "action": "lt", "target": 45}},
                             limit=3)

    def test_额外指标列请求_读数含其输出列(self, env):
        """indicator 参数（额外读数指标）的多输出列进读数集。"""
        out = ci.inspect_cases(
            env["db"], env["data_dir"], symbol=SYMBOL, timeframe="1d",
            selector={"condition": {
                "subject": {"indicator": "RSI",
                            "params": {"period": 14}},
                "action": "lt", "target": 100.0}},
            indicator="MACD", limit=3)
        for case in out["cases"]:
            iv = case["indicator_values"]
            assert isinstance(iv, dict)
            # MACD 默认矩阵输出列在读数集（值或 None）
            assert any("macd" in k for k in iv) or iv == {}


class TestGapClosureTfBase:
    def test_条件带timeframe而基准未知_拒(self, env):
        with pytest.raises(ResearchError) as ei:
            ci.inspect_cases(env["db"], env["data_dir"], symbol=SYMBOL,
                             timeframe="7m",
                             selector={"condition": {
                                 "subject": {"indicator": "RSI",
                                             "params": {"period": 14}},
                                 "action": "lt", "target": 45,
                                 "timeframe": "1d"}},
                             limit=3)
        assert ei.value.code == "TIMEFRAME_UNKNOWN"
