"""R2 转化执行周期契约锚族（fix-mtf-execution-consistency 组 2）。

六用例（design 卡 R2 / spec delta「转化执行周期契约」四 Scenario）：
- 缺省继承：不传 strategy_timeframe → 产物执行周期=data_ranges[0].
  timeframe（策略行同值），回执 execution_timeframe 可查；
- 显式兼容：显式传参逐字节旧行为；假设基准恰 4h 的存量调用面产物
  不变（旧默认值与继承值同值）；
- A1 异周期拒绝：存量异周期 data_ranges（登记收紧前遗留——直写库
  构造）→ RESEARCH_PARAM_INVALID 指引显式传参，不静默取首条；
- 四入口默认一致：服务层直调/REST 端点/MCP 工具签名同继承语义；
- 回执第七键：谱系命中/新转化/同基因分流各返回点统一在场；
- 边界：REST 空串 422；服务层 None=继承哨兵。
"""
import json
import sqlite3
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.errors import ResearchError
from core.analysis.hypothesis_service import register_hypothesis
from core.analysis.hypothesis_to_strategy import translate_hypothesis
from core.persistence.db_ext import init_db_ext

_CONDS = {
    "when_conditions": [
        {"subject": {"indicator": "RSI", "params": {"period": 14}},
         "action": "lt", "target": 35}],
    "then_conditions": [
        {"subject": {"indicator": "RSI", "params": {"period": 30}},
         "action": "gt", "target": 60}],
}


def _ranges(tf: str = "30m"):
    return [{"symbol": "BTCUSDT", "timeframe": tf,
             "start": "2024-01-01T00:00:00Z",
             "end": "2024-02-01T00:00:00Z"}]


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    from core.analysis.executor import ensure_research_tables
    ensure_research_tables(d)
    return d


def _hyp(db, tf: str = "30m") -> str:
    return register_hypothesis(
        db, conditions=_CONDS, statement="s", data_ranges=_ranges(tf)
    )["hypothesis_id"]


def _exec_tf(db, sid: str) -> str:
    from core.persistence.repository.strategy import get_strategy
    row = get_strategy(db, sid)
    assert row is not None
    return json.loads(row["dna_json"])["execution_genes"]["timeframe"]


class TestR2TranslateTimeframeContract:
    def test_default_inherits_hypothesis_base_tf(self, db):
        """缺省继承：30m 假设不传参 → 产物 30m（行同值）+回执可查。"""
        hid = _hyp(db, "30m")
        out = translate_hypothesis(db, hypothesis_id=hid)
        assert out["execution_timeframe"] == "30m"
        assert _exec_tf(db, out["strategy_id"]) == "30m"
        from core.persistence.repository.strategy import get_strategy
        assert get_strategy(db, out["strategy_id"])["timeframe"] == "30m"

    def test_explicit_param_backward_compatible(self, db):
        """显式兼容：传 4h 旧行为不变；基准恰 4h 假设缺省产物同 4h
        （存量调用面产物不变——旧默认值与继承值同值）。"""
        hid = _hyp(db, "30m")
        out = translate_hypothesis(
            db, hypothesis_id=hid, strategy_timeframe="4h")
        assert out["execution_timeframe"] == "4h"
        assert _exec_tf(db, out["strategy_id"]) == "4h"
        # 基准恰 4h：缺省（继承）与旧默认（4h）同产物周期
        hid4 = _hyp(db, "4h")
        out4 = translate_hypothesis(db, hypothesis_id=hid4)
        assert out4["execution_timeframe"] == "4h"

    def test_a1_inconsistent_ranges_rejected(self, db):
        """A1 防御：存量异周期 data_ranges（直写库绕登记校验构造）→
        结构化拒绝指引显式传参，不静默取首条。"""
        hid = _hyp(db, "30m")
        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE research_hypotheses SET data_ranges=? "
                "WHERE hypothesis_id=?",
                (json.dumps(_ranges("30m") + _ranges("4h")), hid))
        with pytest.raises(ResearchError) as exc:
            translate_hypothesis(db, hypothesis_id=hid)
        assert exc.value.code == "RESEARCH_PARAM_INVALID"
        assert sorted(exc.value.detail["timeframes"]) == ["30m", "4h"]
        assert "strategy_timeframe" in exc.value.detail["need_hint"]
        # 显式传参不受此拦（用户指定优先）
        out = translate_hypothesis(
            db, hypothesis_id=hid, strategy_timeframe="30m")
        assert out["execution_timeframe"] == "30m"

    def test_receipt_key_on_all_return_points(self, db):
        """回执第七键四返回点统一：新转化/谱系命中/同基因分流。"""
        hid = _hyp(db, "30m")
        first = translate_hypothesis(db, hypothesis_id=hid)
        assert first["execution_timeframe"] == "30m"  # 新转化
        again = translate_hypothesis(db, hypothesis_id=hid)
        assert again["duplicated"] is True  # 谱系命中（draft 已在）
        assert again["execution_timeframe"] == "30m"
        # 同基因分流：同 conditions 新假设（不同 data_ranges 避开登记
        # 幂等）→ 同签名命中既有 draft → same_dna_existing 分流
        hid2 = register_hypothesis(
            db, conditions=_CONDS, statement="s2",
            data_ranges=[{"symbol": "BTCUSDT", "timeframe": "30m",
                          "start": "2024-03-01T00:00:00Z",
                          "end": "2024-04-01T00:00:00Z"}])["hypothesis_id"]
        third = translate_hypothesis(db, hypothesis_id=hid2)
        assert third["execution_timeframe"] == "30m"  # 分流点同键

    def test_receipt_key_on_active_redirection_point(self, db):
        """回执第七键第四返回点：active 同基因分流（same_dna_existing
        =True——产物转正后谱系 draft 消失，同基因新假设命中 active
        分流点）。至此四返回点（谱系幂等/active 分流/draft 分流/新
        转化）全部触达。"""
        hid = _hyp(db, "30m")
        first = translate_hypothesis(db, hypothesis_id=hid)
        # 模拟转正：draft → active（谱系 draft 消失）
        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE strategy SET status='active' WHERE strategy_id=?",
                (first["strategy_id"],))
        hid3 = register_hypothesis(
            db, conditions=_CONDS, statement="s3",
            data_ranges=[{"symbol": "BTCUSDT", "timeframe": "30m",
                          "start": "2024-05-01T00:00:00Z",
                          "end": "2024-06-01T00:00:00Z"}])["hypothesis_id"]
        out = translate_hypothesis(db, hypothesis_id=hid3)
        assert out["same_dna_existing"] is True, "须命中 active 同基因分流"
        assert out["duplicated"] is False
        assert out["execution_timeframe"] == "30m", (
            "active 分流点第七键须在场且=产物行周期")

    def test_mcp_and_rest_default_semantics(self, db):
        """四入口默认一致：MCP 工具签名默认 None+REST schema 默认 None
        （两入口缺省均透传服务层继承语义——签名/schema 层锚）。"""
        import inspect

        ns = {}
        exec(compile(
            "import mcpserver.tools as _t", "<probe>", "exec"), ns)
        # MCP 工具闭包注册后原函数经 fn 属性可达（FastMCP 形制）——
        # 不依赖内部结构：源码签名静态断言（默认值契约面）
        import mcpserver.tools as tools_mod
        src = inspect.getsource(tools_mod)
        assert "strategy_timeframe: str | None = None" in src, (
            "MCP 工具默认值须为 None 继承哨兵")
        from api.routes.research.validation import TranslateRequest
        assert TranslateRequest.model_fields[
            "strategy_timeframe"].default is None

    def test_boundary_none_sentinel_and_empty_string(self, db):
        """边界：服务层 None=继承哨兵（不报错）；空串在 schema 层被
        接受（Optional[str]）但经 require_known_timeframe 值域拒绝
        （REST 层只对非 None 校验——空串非 None 必经校验面）。"""
        hid = _hyp(db, "1h")
        out = translate_hypothesis(
            db, hypothesis_id=hid, strategy_timeframe=None)
        assert out["execution_timeframe"] == "1h"
        from api.routes.research.validation import TranslateRequest
        req = TranslateRequest(strategy_symbol="BTCUSDT",
                               strategy_timeframe="")
        assert req.strategy_timeframe == ""  # schema 层收（拒绝在 require）
        from api.routes.research.common import require_known_timeframe
        with pytest.raises(Exception):
            require_known_timeframe(req.strategy_timeframe,
                                    "strategy_timeframe")
