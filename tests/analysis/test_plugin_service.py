"""公式插件注册保留字校验特征测试（CARD-02 修复后正确行为锚）。

output_column 撞名危害链：插件求值直写 DataFrame 同名列（enrich 的
work[col]），撞基础列覆盖价格数据、撞既有插件列产生同名列冲突、撞
编码形冒充指标编码列。注册前一律 422 拒绝；版本化升版路径不误杀。

分面契约（防误杀合法升版的实施裁决）：
- out_col 裸名拦基础列/编码形——语义关，全版本生效；
- 既有插件列只拦真正写 df 的 column_name（version>1 带 _v{N} 后缀，
  裸名与 v1 同名是同名升版正常形态）。
"""

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.executor import ensure_research_tables
from core.analysis.hypothesis_service import ResearchError
from core.analysis.plugin_service import register_formula_plugin
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import research as rr

EXPR = "zscore(close, 20)"


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    ensure_research_tables(d)
    return d


class TestOutputColumnReserved:
    """三类撞名拒绝（均 422 RESEARCH_FORMULA_INVALID，零副作用）。"""

    @pytest.mark.parametrize("reserved", [
        "close", "open", "high", "low", "volume", "trades",
        "open_interest", "funding_rate",
    ], ids=lambda v: v)
    def test_撞基础数据列_拒绝(self, db: Path, reserved: str) -> None:
        with pytest.raises(ResearchError) as ei:
            register_formula_plugin(db, name="base_hit",
                                    expression=EXPR,
                                    output_column=reserved)
        assert ei.value.code == "RESEARCH_FORMULA_INVALID"
        assert ei.value.status == 422
        assert ei.value.detail["conflict"] == "基础数据列"
        assert ei.value.detail["output_column"] == reserved
        # 零副作用：拒后注册表无新行
        assert rr.list_signal_registry_entries(db) == []

    @pytest.mark.parametrize("encoded", ["rsi_14", "adx_14", "stoch_k_14_3"])
    def test_撞参数化编码形_拒绝(self, db: Path, encoded: str) -> None:
        with pytest.raises(ResearchError) as ei:
            register_formula_plugin(db, name="enc_hit", expression=EXPR,
                                    output_column=encoded)
        assert ei.value.detail["conflict"] == "参数化指标编码形列名"

    def test_撞既有插件列_拒绝(self, db: Path) -> None:
        register_formula_plugin(db, name="alpha", expression=EXPR)
        with pytest.raises(ResearchError) as ei:
            register_formula_plugin(db, name="beta", expression=EXPR,
                                    output_column="fx_alpha")
        assert ei.value.detail["conflict"] == "既有插件列"
        # 注册表仍只有 alpha v1 一行
        assert [e["column_name"] for e in rr.list_signal_registry_entries(db)] \
            == ["fx_alpha"]

    def test_后缀形态撞注册表列_拒绝(self, db: Path) -> None:
        # alpha 升 v2 后注册表含 fx_alpha_v2；新插件显式撞该后缀列名
        register_formula_plugin(db, name="alpha", expression=EXPR)
        register_formula_plugin(db, name="alpha",
                                expression="zscore(close, 40)")
        with pytest.raises(ResearchError) as ei:
            register_formula_plugin(db, name="gamma", expression=EXPR,
                                    output_column="fx_alpha_v2")
        assert ei.value.detail["conflict"] == "既有插件列"

    def test_升版显式保留名_仍拒(self, db: Path) -> None:
        """out_col 语义关全版本：同名升 v2 时显式传 close 一律拒。"""
        register_formula_plugin(db, name="alpha", expression=EXPR)
        with pytest.raises(ResearchError) as ei:
            register_formula_plugin(db, name="alpha",
                                    expression="zscore(close, 40)",
                                    output_column="close")
        assert ei.value.detail["conflict"] == "基础数据列"


class TestOutputColumnFree:
    """正常路径不误杀。"""

    def test_缺省fx前缀_注册成功(self, db: Path) -> None:
        out = register_formula_plugin(db, name="norm1", expression=EXPR)
        assert out["duplicated"] is False
        assert out["version"] == 1
        assert out["column"] == "fx_norm1"

    def test_fx前缀编码样式名_放行(self, db: Path) -> None:
        """fx_rsi_14 形似编码形但 fx_ 前缀天然避开（缺省前缀的设计
        意图——校验不误判）。"""
        out = register_formula_plugin(db, name="rsi_14", expression=EXPR)
        assert out["column"] == "fx_rsi_14"

    def test_自定义非保留名_注册成功(self, db: Path) -> None:
        out = register_formula_plugin(db, name="custom", expression=EXPR,
                                      output_column="my_signal_col")
        assert out["column"] == "my_signal_col"

    def test_同名升版_v2不误杀(self, db: Path) -> None:
        """版本化路径（设计卡支持）：同名不同表达式升 v2——裸名与 v1
        同名是正常形态，注册列名带 _v2 后缀。"""
        first = register_formula_plugin(db, name="alpha", expression=EXPR)
        second = register_formula_plugin(db, name="alpha",
                                         expression="zscore(close, 40)")
        assert second["duplicated"] is False
        assert second["version"] == 2
        # v1 裸名列 + v2 后缀列并存
        assert sorted(e["column_name"]
                      for e in rr.list_signal_registry_entries(db)) \
            == ["fx_alpha", "fx_alpha_v2"]
        assert first["column"] == "fx_alpha"
        assert second["column"] == "fx_alpha_v2"

    def test_幂等重放_不受校验影响(self, db: Path) -> None:
        """同名同表达式重放命中幂等分支（先于校验）——既有插件重复
        注册是幂等语义，不是撞名。"""
        first = register_formula_plugin(db, name="alpha", expression=EXPR)
        replay = register_formula_plugin(db, name="alpha", expression=EXPR)
        assert replay["duplicated"] is True
        assert replay["plugin_id"] == first["plugin_id"]


class TestIdempotentColumnReturn:
    """CARD-22：幂等分支 column 取注册表列名（单一事实源）。

    修复前返回 row["output_columns"]（JSON 数组串留档、不带版本
    后缀）——v1 回传 '["fx_a"]' 形制不一致，v2 回传错值致 AI 引用
    断链。修复后幂等与首注册/升版返回形制一致（column 同为裸列名）。
    """

    def test_v1幂等_返回裸列名(self, db: Path) -> None:
        first = register_formula_plugin(db, name="alpha", expression=EXPR)
        replay = register_formula_plugin(db, name="alpha", expression=EXPR)
        assert replay["column"] == "fx_alpha"
        # 形制一致锚：与首注册返回同列名同形制（裸 str，非 JSON 串）
        assert replay["column"] == first["column"]
        assert isinstance(replay["column"], str)
        assert not replay["column"].startswith("[")

    def test_v2幂等_返回带后缀列名(self, db: Path) -> None:
        """修复核心：version≥2 幂等命中回传 fx_alpha_v2（原错值
        '["fx_alpha"]' 指向不存在的裸列名）。"""
        register_formula_plugin(db, name="alpha", expression=EXPR)
        second = register_formula_plugin(
            db, name="alpha", expression="zscore(close, 40)")
        replay = register_formula_plugin(
            db, name="alpha", expression="zscore(close, 40)")
        assert replay["version"] == 2
        assert replay["column"] == "fx_alpha_v2"
        assert replay["column"] == second["column"]

    def test_多版本并存_各表达式重放返回各自列名(self, db: Path) -> None:
        expr_v1, expr_v2 = EXPR, "zscore(close, 40)"
        register_formula_plugin(db, name="alpha", expression=expr_v1)
        register_formula_plugin(db, name="alpha", expression=expr_v2)

        assert register_formula_plugin(
            db, name="alpha", expression=expr_v1)["column"] == "fx_alpha"
        assert register_formula_plugin(
            db, name="alpha", expression=expr_v2)["column"] == "fx_alpha_v2"

    def test_自定义列名幂等_返回自定义名(self, db: Path) -> None:
        kwargs = dict(name="gamma", expression=EXPR,
                      output_column="my_gamma")
        first = register_formula_plugin(db, **kwargs)
        replay = register_formula_plugin(db, **kwargs)
        assert replay["duplicated"] is True
        assert replay["column"] == "my_gamma"
        assert replay["column"] == first["column"]


class TestRegistryMissingIntegrity:
    """CARD-22 防御分支：valid 插件的注册表条目缺失 → 显式 500。

    双写原子性被破坏属服务端数据完整性异常（客户端无法经请求修正）；
    静默回传坏引用会重新制造 AI 引用断链且更难排查。
    """

    def test_注册条目缺失_500显式失败(self, db: Path) -> None:
        from sqlalchemy import delete
        from core.persistence.orm.models import SignalRegistryEntry
        from core.persistence.repository.dataset_meta import _engine_for

        out = register_formula_plugin(db, name="alpha", expression=EXPR)
        with _engine_for(db).begin() as conn:
            conn.execute(delete(SignalRegistryEntry).where(
                SignalRegistryEntry.plugin_id == out["plugin_id"]))

        with pytest.raises(ResearchError) as ei:
            register_formula_plugin(db, name="alpha", expression=EXPR)
        assert ei.value.code == "RESEARCH_FORMULA_REGISTRY_MISSING"
        assert ei.value.status == 500
