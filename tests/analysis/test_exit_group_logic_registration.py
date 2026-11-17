"""出场组内 OR 通道——登记校验四条与归一化（add-exit-group-or 任务 1.1/1.2）。

覆盖 tasks 完成标准：
- 值域：{"and","or"} 小写封闭——大写 "AND"/"OR" 与 "xor" 同拒
  （conditions 层小写→LogicGenes 层大写，防实现混淆）；两键同规则
- 组存在性：partial_exit_logic 传值而 exit_partial_conditions 组缺失
  → 422 附「逻辑键无作用组」；组在场放行
- 条目同构：逻辑键在场时组内坏条目仍走既有同构校验链（第 1 步
  组键校验零改动——field 定位形制不变）
- 标量无嵌套：dict 子结构携带 → 422（值域分支天然覆盖）
- 归一化：显式 "and" 与无键两形 conditions_hash 相等（幂等命中
  同一假设）；「or」才写键进指纹
- 双身份（1.2）：同条件集一个含 exit_logic="or" 一个无键 → 两个
  独立假设身份
"""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.executor import ensure_research_tables
from core.analysis.errors import ResearchError
from core.analysis.hypothesis_service import register_hypothesis
from core.persistence.db_ext import init_db_ext


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    ensure_research_tables(d)
    return d


def _cond() -> dict:
    return {
        "when_conditions": [
            {"subject": {"indicator": "RSI", "params": {"period": 14},
                         "field": "rsi"}, "action": "lt", "target": 30}],
        "then_conditions": [
            {"subject": {"indicator": "RSI", "params": {"period": 14},
                         "field": "rsi"}, "action": "gt", "target": 70}],
    }


def _ranges() -> list:
    return [{"symbol": "BTCUSDT", "timeframe": "4h",
             "start": "2024-01-01T00:00:00Z", "end": "2024-06-01T00:00:00Z"}]


def _register(db: Path, *, conditions=None, exit_logic=None,
              partial_exit_logic=None):
    """服务层直调登记（直调面 conditions 不含逻辑键——键经参数通道，
    与 direction 同族；API/MCP 两正式入口同汇本签名）。"""
    return register_hypothesis(
        db, conditions=conditions or _cond(), statement="组内 OR 登记测试",
        data_ranges=_ranges(), strategy_params={"stop_loss": 0.05},
        exit_logic=exit_logic, partial_exit_logic=partial_exit_logic)


class TestValueDomain:
    """校验条①：值域 {"and","or"} 小写封闭（两键同规则）。"""

    @pytest.mark.parametrize("bad", ["AND", "OR", "And", "xor", "", 1, True])
    def test_exit_logic_bad_value_rejected(self, db, bad):
        """exit_logic 非法值 422：大写/混合大小写/异或/空串/数值/布尔
        全拒——值域小写封闭，大小写映射断言防实现混淆。"""
        with pytest.raises(ResearchError) as ei:
            _register(db, exit_logic=bad)
        assert ei.value.code == "RESEARCH_CONDITION_INVALID"
        assert ei.value.detail["legal_values"] == ["and", "or"]

    @pytest.mark.parametrize("bad", ["AND", "xor"])
    def test_partial_exit_logic_bad_value_rejected(self, db, bad):
        """partial_exit_logic 同规则（值域判定先于组存在性——非法值
        且组缺失时值域优先报）。"""
        with pytest.raises(ResearchError) as ei:
            _register(db, partial_exit_logic=bad)
        assert ei.value.code == "RESEARCH_CONDITION_INVALID"
        assert ei.value.detail["legal_values"] == ["and", "or"]

    def test_or_value_allowed(self, db):
        """正例：exit_logic="or" 登记成功。"""
        out = _register(db, exit_logic="or")
        assert out["duplicated"] is False
        assert out["hypothesis_id"].startswith("hyp_")


class TestHostGroupPresence:
    """校验条②：组存在性——逻辑键无作用组拒绝。"""

    def test_partial_exit_logic_without_group_rejected(self, db):
        """partial_exit_logic="or" 而 exit_partial_conditions 缺失 →
        422 附「逻辑键无作用组」。"""
        with pytest.raises(ResearchError) as ei:
            _register(db, partial_exit_logic="or")
        assert ei.value.code == "RESEARCH_CONDITION_INVALID"
        assert "逻辑键无作用组" in ei.value.message
        assert ei.value.detail["host_group"] == "exit_partial_conditions"

    def test_partial_exit_logic_with_group_allowed(self, db):
        """正例：组在场放行（组键经 conditions 顶层，登记归一形
        「or」写键进指纹——此处只验证校验面放行）。"""
        cond = _cond()
        cond["exit_partial_conditions"] = [
            {"subject": {"indicator": "RSI", "params": {"period": 14},
                         "field": "rsi"}, "action": "gt", "target": 75}]
        out = _register(db, conditions=cond, partial_exit_logic="or")
        assert out["duplicated"] is False

    def test_exit_logic_no_group_check(self, db):
        """exit_logic 无组存在性检查（then_conditions 本必填非空，
        恒有作用组）——纯 then 假设带 exit_logic 照常登记。"""
        out = _register(db, exit_logic="or")
        assert out["duplicated"] is False


class TestEntryIsomorphismShared:
    """校验条③：组内条目形制与既有同构校验链共用（零改动锚）。"""

    def test_bad_group_entry_still_rejected_with_logic_key(self, db):
        """逻辑键在场时组内坏条目（缺 target）仍走既有第 1 步组键
        校验 422——field 定位组条目形制不变（校验链未被逻辑键改写）。"""
        cond = _cond()
        cond["exit_partial_conditions"] = [
            {"subject": {"indicator": "RSI", "params": {"period": 14},
                         "field": "rsi"}, "action": "gt"}]  # 缺 target
        with pytest.raises(ResearchError) as ei:
            _register(db, conditions=cond, partial_exit_logic="or")
        assert ei.value.code == "RESEARCH_CONDITION_INVALID"
        # 既有校验链的 field 定位形制（exit_partial_conditions[0]）
        assert ei.value.detail["field"] == "exit_partial_conditions[0]"


class TestScalarNoNesting:
    """校验条④：标量键无嵌套。"""

    def test_dict_payload_rejected(self, db):
        """携带子结构（{logic: "or"} 形组对象案残留）→ 422——落入
        值域分支天然拒（not in 集合对 dict 成立）。"""
        with pytest.raises(ResearchError) as ei:
            _register(db, exit_logic={"logic": "or"})
        assert ei.value.code == "RESEARCH_CONDITION_INVALID"
        assert ei.value.detail["legal_values"] == ["and", "or"]

    def test_list_payload_rejected(self, db):
        with pytest.raises(ResearchError) as ei:
            _register(db, partial_exit_logic=["or"])
        assert ei.value.code == "RESEARCH_CONDITION_INVALID"


class TestNormalizationAndIdentity:
    """归一化（显式 "and" 归一删除——两形 hash 相等）＋双身份（1.2）。"""

    def test_explicit_and_same_identity_as_missing(self, db):
        """显式 "and" 与无键两形 conditions_hash 相等：先无键登记、
        再显式 "and" 登记命中同一假设（duplicated=True 同 id）——
        归一形「or」才写键的行为级证据。"""
        first = _register(db)
        second = _register(db, exit_logic="and", partial_exit_logic=None)
        assert second["duplicated"] is True
        assert second["hypothesis_id"] == first["hypothesis_id"]

    def test_or_key_creates_independent_identity(self, db):
        """双身份断言（1.2）：同条件集一个含 exit_logic="or" 一个
        无键 → 两个独立假设身份（逻辑键入身份指纹）。"""
        plain = _register(db)
        with_or = _register(db, exit_logic="or")
        assert with_or["duplicated"] is False
        assert with_or["hypothesis_id"] != plain["hypothesis_id"]

    def test_partial_or_key_creates_independent_identity(self, db):
        """partial 通道同理：带组+or 与带组无键 → 两独立身份。"""
        cond = _cond()
        cond["exit_partial_conditions"] = [
            {"subject": {"indicator": "RSI", "params": {"period": 14},
                         "field": "rsi"}, "action": "gt", "target": 75}]
        plain = _register(db, conditions=cond)
        with_or = _register(db, conditions=cond, partial_exit_logic="or")
        assert with_or["duplicated"] is False
        assert with_or["hypothesis_id"] != plain["hypothesis_id"]

    def test_or_conditions_persisted(self, db):
        """归一形落库核验：exit_logic="or" 登记 → conditions_json
        顶层含 exit_logic="or"（翻译层读键的落库前提）。"""
        from core.persistence.repository import research as rr
        out = _register(db, exit_logic="or")
        row = rr.get_hypothesis(db, out["hypothesis_id"])
        import json
        assert json.loads(row["conditions"])["exit_logic"] == "or"

    def test_explicit_and_not_persisted(self, db):
        """显式 "and" 不写键：conditions_json 顶层无 exit_logic 键
        （归一删除——与缺省同字面）。"""
        from core.persistence.repository import research as rr
        out = _register(db, exit_logic="and")
        row = rr.get_hypothesis(db, out["hypothesis_id"])
        import json
        assert "exit_logic" not in json.loads(row["conditions"])


class TestPartialChannelNormalization:
    """partial 通道登记级归一三锚（scaled delta scenario「部分离场
    组内 OR 登记」THEN 第三分句——「显式 "and" 与缺省两形指纹相等」
    的 partial 通道直接断言；此前仅 exit 通道有，partial 通道若漏
    归一〔显式 and 写键〕时：hash 分叉建双实体〔本类①能抓〕、产物
    逐位一致〔translation 级测试抓不到——翻译层只认 "or"〕）。"""

    @staticmethod
    def _group_cond() -> dict:
        cond = _cond()
        cond["exit_partial_conditions"] = [
            {"subject": {"indicator": "RSI", "params": {"period": 14},
                         "field": "rsi"}, "action": "gt", "target": 75}]
        return cond

    def test_partial_explicit_and_same_identity_as_missing(self, db):
        """带组假设：先无键登记、再显式 partial_exit_logic="and"
        登记 → duplicated=True 同 id（两形条件指纹相等——归一形
        「or」才写键）。"""
        cond = self._group_cond()
        first = _register(db, conditions=cond)
        second = _register(db, conditions=cond, partial_exit_logic="and")
        assert second["duplicated"] is True
        assert second["hypothesis_id"] == first["hypothesis_id"]

    def test_partial_or_conditions_persisted(self, db):
        """带组+or 登记 → conditions_json 顶层 partial_exit_logic
        =="or"（翻译层 reduce 通道读键的落库直接锚）。"""
        from core.persistence.repository import research as rr
        import json

        out = _register(db, conditions=self._group_cond(),
                        partial_exit_logic="or")
        row = rr.get_hypothesis(db, out["hypothesis_id"])
        assert json.loads(row["conditions"])["partial_exit_logic"] == "or"

    def test_partial_explicit_and_not_persisted(self, db):
        """带组+显式 "and" 登记 → conditions_json 顶层无
        partial_exit_logic 键（归一删除——与缺省同字面）。"""
        from core.persistence.repository import research as rr
        import json

        out = _register(db, conditions=self._group_cond(),
                        partial_exit_logic="and")
        row = rr.get_hypothesis(db, out["hypothesis_id"])
        assert "partial_exit_logic" not in json.loads(row["conditions"])
