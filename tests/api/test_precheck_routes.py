"""假设预检器路由集成测试（add-hypothesis-precheck tasks 2.1/2.2 验证锚）。

覆盖：必填键 422 附键名（卡1 机制）/validate_range 异构与无数据 422/
正常 200 携带 verdict+留痕 entry_type=precheck 不挂假设（R4 结构性：
judged_ranges_union 按假设过滤，None 条目天然不进并集）/大区间 202+
轮询后留痕类型仍为 precheck（异步回调分派——增量评估问题①）/
预检区间不消耗验证资格（R4 S11 行为级：预检过的区间经真实判定点
_hypothesis_precheck 放行——含挂假设 id 即 409 的可红对照组）。
"""
import json
import time

import pytest

pytestmark = [pytest.mark.integration]

from core.persistence.db_ext import init_db_ext
from core.persistence.repository import research as rr
from tests.helpers.data_factory import make_ohlcv

SYMBOL = "BTCUSDT"


@pytest.fixture()
def precheck_env(tmp_path):
    """900 bar 4h 数据 + 独立 db（留痕断言需直查库）。"""
    df = make_ohlcv(n=900, freq="4h", start="2024-01-01", seed=13)
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    df.to_parquet(data_dir / f"{SYMBOL}_4h.parquet")
    db = tmp_path / "quant.db"
    init_db_ext(db)
    return {"data_dir": data_dir, "db": db, "df": df}


def _wire(api_client, env):
    api_client.app.state.db_path = env["db"]
    api_client.app.state.data_dir = env["data_dir"]


def _body(params, df, **range_extra):
    """请求区间=数据尾段（index[650, 900)——前缀 650 覆盖 warmup 600）。"""
    rng = {"symbol": SYMBOL, "timeframe": "4h",
           "start": df.index[650].isoformat(),
           "end": df.index[-1].isoformat()}
    rng.update(range_extra)
    return {"template": "precheck_hypothesis", "params": params,
            "data_range": rng}


def _validate_range(df):
    return {"symbol": SYMBOL, "timeframe": "4h",
            "start": df.index[650].isoformat(),
            "end": df.index[-1].isoformat()}


def _conds():
    return [{"subject": "close", "action": "gt", "target": "0"}]


class TestPrecheckRouteContract:
    def test_missing_required_key_422_names_the_key(self, api_client, precheck_env):
        """卡1 机制：缺 when_conditions → 422 附键名（缺 validate_range 同族，
        本例钉锚一个即可——机制为 _require_required_keys 单点）。"""
        _wire(api_client, precheck_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            {"validate_range": _validate_range(precheck_env["df"])},
            precheck_env["df"]))
        assert resp.status_code == 422, resp.text
        assert "when_conditions" in resp.text

    def test_validate_range_timeframe_mismatch_422(self, api_client, precheck_env):
        _wire(api_client, precheck_env)
        vr = dict(_validate_range(precheck_env["df"]), timeframe="1d")
        resp = api_client.post("/api/research/analysis", json=_body(
            {"when_conditions": _conds(), "validate_range": vr},
            precheck_env["df"]))
        assert resp.status_code == 422, resp.text
        assert "RESEARCH_PARAM_INVALID" in resp.text

    def test_validate_range_no_data_422(self, api_client, precheck_env):
        _wire(api_client, precheck_env)
        vr = dict(_validate_range(precheck_env["df"]),
                  start="2030-01-01T00:00:00+00:00",
                  end="2030-02-01T00:00:00+00:00")
        resp = api_client.post("/api/research/analysis", json=_body(
            {"when_conditions": _conds(), "validate_range": vr},
            precheck_env["df"]))
        assert resp.status_code == 422, resp.text
        assert "RESEARCH_SYMBOL_NOT_FOUND" in resp.text

    def test_ok_200_with_verdict_and_precheck_log(
            self, api_client, precheck_env):
        """正常 200 携 verdict + research_log 落 precheck 条目（不挂假设）。"""
        _wire(api_client, precheck_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            {"when_conditions": _conds(),
             "validate_range": _validate_range(precheck_env["df"])},
            precheck_env["df"]))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["template"] == "precheck_hypothesis"
        assert body["verdict"] in ("pass", "warn", "reject_suggestion")
        assert body["payload"]["budget_check"]["warmup_bars"] == 200

        entries = rr.list_research_log(
            precheck_env["db"], hypothesis_id=None)
        precheck = [e for e in entries if e["entry_type"] == "precheck"]
        assert len(precheck) == 1
        summary = json.loads(precheck[0]["summary"])
        assert summary["verdict"] == body["verdict"]
        assert summary["threshold"] == 5
        assert precheck[0]["hypothesis_id"] is None    # R4：不挂假设
        assert precheck[0]["ref_id"] == "precheck_hypothesis"  # 显式路径来源
        assert json.loads(precheck[0]["data_ranges"]) == [
            {"symbol": SYMBOL, "timeframe": "4h",
             "start": precheck_env["df"].index[650].isoformat(),
             "end": precheck_env["df"].index[-1].isoformat()}]  # 区间原值回读

    def test_precheck_log_not_in_judged_union(self, api_client, precheck_env):
        """R4 场景：预检条目不进判定并集——按假设过滤的日志查询捞不到
        None 挂载的 precheck 条目（judged_ranges_union 消费同款过滤），
        预检过的区间仍可用于隔离验证。"""
        _wire(api_client, precheck_env)
        api_client.post("/api/research/analysis", json=_body(
            {"when_conditions": _conds(),
             "validate_range": _validate_range(precheck_env["df"])},
            precheck_env["df"]))
        # 无假设视角：None 过滤能捞到（留痕可审计）
        assert any(e["entry_type"] == "precheck"
                   for e in rr.list_research_log(
                       precheck_env["db"], hypothesis_id=None))
        # 假设视角：任何假设 id 过滤都捞不到（不进并集的结构性证明）
        for ghost in ("hyp_a", "hyp_b"):
            assert not any(
                e["entry_type"] == "precheck"
                for e in rr.list_research_log(
                    precheck_env["db"], hypothesis_id=ghost))


class TestValidateRangeTimestampContract:
    """CARD-02：validate_range 时间串可解析性 422 前置。

    契约依据：validate_range 声明形制为 UTC ISO8601 **串**（与
    data_range 的 DataRangeItem 先例双层语义对齐——Pydantic 类型层
    拒非 str 形 + field_validator 拒不可解析 str）。修复前非法串
    裸穿落 500 RESEARCH_TEMPLATE_INTERNAL——本用例锚「输入形制错误
    → 422 RESEARCH_PARAM_INVALID」而非 500。
    """

    @pytest.mark.parametrize("key", ["start", "end"])
    @pytest.mark.parametrize(
        "bad_value, form",
        [("not-a-date", "非法串"),
         ({"a": 1}, "非字符串 dict 形"),
         (None, "None 形"),
         (20240101, "int 形——pd.Timestamp 按 epoch 解释为 1970")],
        ids=["非法串", "dict形", "None形", "int形"])
    def test_unparseable_timestamp_422_not_500(
            self, api_client, precheck_env, key, bad_value, form):
        _wire(api_client, precheck_env)
        vr = dict(_validate_range(precheck_env["df"]))
        vr[key] = bad_value
        resp = api_client.post("/api/research/analysis", json=_body(
            {"when_conditions": _conds(), "validate_range": vr},
            precheck_env["df"]))
        assert resp.status_code == 422, (
            f"{key}={bad_value!r}（{form}）须结构化拒绝，"
            f"实际 {resp.status_code}: {resp.text[:200]}")
        assert "RESEARCH_PARAM_INVALID" in resp.text


class TestPrecheckHeavySemantics:
    def test_over_10000_rows_goes_202_and_logs_precheck(
            self, api_client, db_path, tmp_path):
        """审查裁决 1：行数超 _HEAVY_ROWS 照走 202；异步完成回调留痕
        entry_type 仍为 precheck（双分派——仅接快路径会使异步主路径落
        entry_type=analysis）。

        db 用 api_client 启动库不覆写：executor 为模块级单例，任务表
        绑定 lifespan 启动时的 db_path——覆写 state.db_path 会使任务
        写入启动库而轮询查覆写库（404 分裂）。
        """
        df = make_ohlcv(n=11000, freq="4h", start="2020-01-01", seed=17)
        data_dir = tmp_path / "market"
        data_dir.mkdir()
        df.to_parquet(data_dir / f"{SYMBOL}_4h.parquet")
        api_client.app.state.data_dir = data_dir

        vr = {"symbol": SYMBOL, "timeframe": "4h",
              "start": df.index[650].isoformat(),
              "end": df.index[-1].isoformat()}
        resp = api_client.post("/api/research/analysis", json={
            "template": "precheck_hypothesis",
            "params": {"when_conditions": _conds(), "validate_range": vr},
            "data_range": dict(vr)})
        assert resp.status_code == 202, resp.text
        task_id = resp.json()["task_id"]

        out = {}
        for _ in range(120):   # 轮询至终态（线程池 + 大区间装载）
            poll = api_client.get(f"/api/research/analysis/tasks/{task_id}")
            out = poll.json()
            if out.get("status") in ("succeeded", "failed"):
                break
            time.sleep(0.5)
        assert out.get("status") == "succeeded", out
        result = out["result"]
        assert result["template"] == "precheck_hypothesis"
        assert result["verdict"] in ("pass", "warn", "reject_suggestion")

        entries = rr.list_research_log(db_path, hypothesis_id=None)
        precheck = [e for e in entries if e["entry_type"] == "precheck"]
        assert len(precheck) == 1                 # 异步回调分派为 precheck
        assert not any(e["entry_type"] == "analysis"          # 未落 analysis
                       and e.get("ref_id") == "precheck_hypothesis"
                       for e in entries)


class TestPrecheckRangeEligibility:
    """specs R4 S11 行为级：预检不消耗验证资格。

     WHEN 某区间经预检后仍以该区间提交隔离验证
    THEN 不被判定并集拒绝（409 抛出点=validation_service.
    _hypothesis_precheck 的并集求交步——真实判定逻辑，非日志过滤
    视角的结构推断）。对照组（可红证明）：手工插入一条挂该假设 id
    的日志携带同区间 → 判定点即 409——证明本用例的放行断言对
    「留痕挂假设 id」的回归方向真的会红，非恒绿。
    """

    def test_prechecked_range_passes_judged_union(
            self, api_client, precheck_env):
        from core.analysis.hypothesis_service import ResearchError
        from core.analysis.validation_service import (
            _hypothesis_precheck, _range_hash,
        )

        _wire(api_client, precheck_env)
        df = precheck_env["df"]
        # X=预检区间 [650,775)；Y=假设自报区间 [775,900)——Y 与 X 不相交，
        # 排除自报并集对 X 的干扰，让判定只取决于 precheck 留痕归属
        range_x = {"symbol": SYMBOL, "timeframe": "4h",
                   "start": df.index[650].isoformat(),
                   "end": df.index[774].isoformat()}
        range_y = {"symbol": SYMBOL, "timeframe": "4h",
                   "start": df.index[775].isoformat(),
                   "end": df.index[-1].isoformat()}

        # 1) 区间 X 经预检（落 precheck 留痕，hypothesis_id=None）
        resp = api_client.post("/api/research/analysis", json={
            "template": "precheck_hypothesis",
            "params": {"when_conditions": _conds(),
                       "validate_range": dict(range_x)},
            "data_range": dict(range_x)})
        assert resp.status_code == 200, resp.text

        # 2) 登记假设（自报 Y；挂接预检自动跑 Y，与 X 无关）
        reg = api_client.post("/api/research/hypotheses", json={
            "conditions": {
                "when_conditions": _conds(),
                "then_conditions": _conds(),
            },
            "statement": "预检区间资格测试假设",
            "data_ranges": [range_y],
        })
        assert reg.status_code == 200, reg.text
        hyp_id = reg.json()["hypothesis_id"]

        # 3) 行为级判定点：以 X 提交隔离验证的前置检查 → 放行（None）
        rh = _range_hash(range_x)
        assert _hypothesis_precheck(
            precheck_env["db"], hyp_id, "str_eligibility_probe", rh,
            range_x, idem_level="isolated_backtest") is None

        # 4) 对照组：日志一旦挂上假设 id（回归方向：留痕错误挂 id），
        #    同判定点即 409——放行断言的可红性证明
        rr.append_research_log(
            precheck_env["db"], hypothesis_id=hyp_id,
            entry_type="precheck", ref_id="precheck_hypothesis",
            data_ranges=json.dumps([range_x]), summary="{}")
        with pytest.raises(ResearchError) as ei:
            _hypothesis_precheck(
                precheck_env["db"], hyp_id, "str_eligibility_probe", rh,
                range_x, idem_level="isolated_backtest")
        assert ei.value.code == "RESEARCH_RANGE_OVERLAP"
