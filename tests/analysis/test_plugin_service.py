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


class TestModelColumnReserved:
    """CARD-23：model 注册撞既有插件列 422（原同名列互写覆盖）。

    model signal_column 强制 model_ 前缀（撞不了 BASE/编码形），只查
    注册表 taken——拦不同 name 组合的同名列（name="x" v2 与
    name="x_v2" v1 都产生 model_x_v2）。训练经假 executor 吸收
    （锚定注册校验，不依赖异步训练完成）。
    """

    MODEL_KW = dict(
        features=["close"], template="rolling_logistic",
        params={"window": 0, "step": 20},
        target={"type": "return_sign", "horizon": 4},
        symbol="BTCUSDT", timeframe="4h",
        data_start="2024-01-01", data_end="2024-03-01")

    @pytest.fixture(autouse=True)
    def _fake_executor(self, monkeypatch):
        class _Handle(dict):
            pass

        class _Exec:
            def submit(self, name, fn, **kw):
                return _Handle(task_id="task_fake", fast_path=False)

        import core.analysis.executor as exec_mod
        monkeypatch.setattr(exec_mod, "get_executor", lambda: _Exec())

    @staticmethod
    def _occupy(db: Path, name: str, version: int, column: str) -> None:
        """直插 valid 模型行 + 注册表占位（训练成功路径的等效落库态）。"""
        from sqlalchemy import insert
        from core.persistence.orm.models import ModelPlugin
        from core.persistence.repository.dataset_meta import _engine_for
        with _engine_for(db).begin() as conn:
            conn.execute(insert(ModelPlugin).values(
                plugin_id=f"mpl_{name}_v{version}", name=name,
                version=version, config="{}", signal_column=column,
                status="valid", strategy_visible=1,
                created_at="2026-09-19T00:00:00"))
        rr.register_existing_plugin_signal(
            db, source="model", plugin_id=f"mpl_{name}_v{version}",
            name=name, version=version, column_name=column)

    def test_占位列后升版撞名_422(self, db: Path, tmp_path: Path) -> None:
        from core.analysis.plugin_service import register_model_plugin
        self._occupy(db, "x_v2", 1, "model_x_v2")

        # v1 正常：model_x 不撞
        out1 = register_model_plugin(db, tmp_path, name="x", **self.MODEL_KW)
        assert out1["status"] == "pending"
        assert out1["signal_column"] == "model_x"

        # v2 撞占位列（修复核心）：model_x_v2 ∈ taken → 422
        with pytest.raises(ResearchError) as ei:
            register_model_plugin(db, tmp_path, name="x",
                                  **dict(self.MODEL_KW,
                                         features=["volume"]))
        assert ei.value.code == "RESEARCH_FORMULA_INVALID"
        assert ei.value.status == 422
        assert ei.value.detail["conflict"] == "既有插件列"
        assert ei.value.detail["column"] == "model_x_v2"
        # 零副作用：name=x 仍只有 v1 一行
        from sqlalchemy import select
        from core.persistence.orm.models import ModelPlugin
        from core.persistence.repository.dataset_meta import _engine_for
        with _engine_for(db).connect() as conn:
            rows = conn.execute(select(ModelPlugin).where(
                ModelPlugin.name == "x")).mappings().all()
        assert [r["version"] for r in rows] == [1]

    def test_formula撞model占位列_422(self, db: Path) -> None:
        """formula/model 共享撞名面（helper 化后双向不变）。"""
        self._occupy(db, "x_v2", 1, "model_x_v2")
        with pytest.raises(ResearchError) as ei:
            register_formula_plugin(db, name="beta", expression=EXPR,
                                    output_column="model_x_v2")
        assert ei.value.detail["conflict"] == "既有插件列"

    def test_model撞formula占位列_422(self, db: Path, tmp_path: Path) -> None:
        """双向另一半：formula 先占 model_x，model name=x v1 撞 422。"""
        from core.analysis.plugin_service import register_model_plugin
        register_formula_plugin(db, name="alpha", expression=EXPR,
                                output_column="model_x")
        with pytest.raises(ResearchError) as ei:
            register_model_plugin(db, tmp_path, name="x", **self.MODEL_KW)
        assert ei.value.detail["conflict"] == "既有插件列"

    def test_升版不撞_两版均正常pending(self, db: Path, tmp_path: Path) -> None:
        from core.analysis.plugin_service import register_model_plugin
        out1 = register_model_plugin(db, tmp_path, name="y", **self.MODEL_KW)
        out2 = register_model_plugin(db, tmp_path, name="y",
                                     **dict(self.MODEL_KW,
                                            features=["volume"]))
        assert out1["signal_column"] == "model_y"
        assert out2["signal_column"] == "model_y_v2"
        assert out1["status"] == out2["status"] == "pending"


class TestNoParamIndicatorColumnReserved:
    """CARD-24：output_column 撞无参指标输出列 422（第四分面）。

    危害链：指标计算幂等（col 不在 df 才算），插件值先写即顶替指标
    现算值。派生面 = INDICATOR_REGISTRY 中 params 为空指标的全部
    output_fields（动态派生不硬编码）；有参指标真列名带参数段
    （macd_histogram_12_26_9 形），由编码形分面覆盖。
    """

    def test_派生集与注册表独立推导一致(self) -> None:
        """单一事实源锚：派生集 == 无参指标 output_fields 拍平。"""
        from core.analysis.plugin_service import _no_param_indicator_columns
        from core.features.registry import INDICATOR_REGISTRY
        expected = frozenset(
            f for d in INDICATOR_REGISTRY.values() if not d.params
            for f in d.output_fields)
        assert _no_param_indicator_columns() == expected
        # 代表成员在场（防派生意外缩空）
        for must in ("obv", "vwap", "ad", "cvd", "pattern_morning_star"):
            assert must in _no_param_indicator_columns()

    @pytest.mark.parametrize("reserved", [
        "obv", "vwap", "ad", "cvd",
        "pattern_morning_star", "pattern_3blackcrows",
    ], ids=lambda v: v)
    def test_撞无参指标列_拒绝(self, db: Path, reserved: str) -> None:
        with pytest.raises(ResearchError) as ei:
            register_formula_plugin(db, name="np_hit", expression=EXPR,
                                    output_column=reserved)
        assert ei.value.code == "RESEARCH_FORMULA_INVALID"
        assert ei.value.status == 422
        assert ei.value.detail["conflict"] == "无参指标输出列"
        assert ei.value.detail["output_column"] == reserved
        assert rr.list_signal_registry_entries(db) == []

    def test_升版显式无参指标列_仍拒(self, db: Path) -> None:
        """第四分面在 out_col 检查位——语义关全版本。"""
        register_formula_plugin(db, name="alpha", expression=EXPR)
        with pytest.raises(ResearchError) as ei:
            register_formula_plugin(db, name="alpha",
                                    expression="zscore(close, 40)",
                                    output_column="obv")
        assert ei.value.detail["conflict"] == "无参指标输出列"

    def test_fx前缀无参指标名_放行(self, db: Path) -> None:
        """fx_obv 形似无参指标列但缺省前缀天然避开（不误杀）。"""
        out = register_formula_plugin(db, name="obv", expression=EXPR)
        assert out["column"] == "fx_obv"

    def test_有参指标真列名_编码形分面仍拦(self, db: Path) -> None:
        """有参指标实际列名带参数段——由编码形分面覆盖（分面互补、
        不重复拦截的边界锚）。"""
        with pytest.raises(ResearchError) as ei:
            register_formula_plugin(db, name="enc_hit", expression=EXPR,
                                    output_column="macd_histogram_12_26_9")
        assert ei.value.detail["conflict"] == "参数化指标编码形列名"


class TestLookbackUpperBound:
    """CARD-21：公式回看窗口静态上限（防 warmup 换算溢出）。

    窗口参数无上限时 bar 数 × 周期秒数换算 pd.Timedelta 溢出
    （OutOfBoundsTimedelta，消费路径 500）。注册处静态拦截：
    formula_lookback（全树窗口字面量取 max）> 10_000 → 422。
    上限依据（lead 打回方案 a，security 实测）：Timedelta 上限
    ~9.2e9s（106751 天）；换算值域 TF_MINUTES 最大 3d，1w 等高周期
    走 high_tf_warmup_span 不涉插件 lookback；10_000 对最苛刻 1w
    防御边界（约 15_250 bar）留 34% 裕度。
    """

    def test_上限常量冻结(self) -> None:
        from core.analysis.plugin_service import MAX_PLUGIN_LOOKBACK_BARS
        # 溢出核算锚：Timedelta.max 秒数 / 1w 周期秒数 ≈ 15_250 bar
        # 防御边界，10_000 留 34% 裕度（3d/1d 边界更宽）
        assert MAX_PLUGIN_LOOKBACK_BARS == 10_000

    def test_窗口恰上限_放行(self, db: Path) -> None:
        out = register_formula_plugin(
            db, name="edge_ok", expression="zscore(close, 10000)")
        assert out["column"] == "fx_edge_ok"

    @pytest.mark.parametrize(
        ("expression", "value_repr"),
        [
            ("zscore(close, 10001)", "10001"),
            ("zscore(close, 100000)", "100000"),
            ("zscore(close, 1000000000)", "1e+09"),
            ("ema(close, 500000)", "500000"),
            ("rolling_mean(rolling_std(close, 30), 999999)", "999999"),
        ],
        ids=["zscore超一", "zscore原边界值", "zscore十亿",
             "ema位", "嵌套取max"])
    def test_超上限_拒绝_静态关(self, db: Path, expression: str,
                                value_repr: str) -> None:
        """CARD-18：有窗口位的超上限公式拒绝点前移到静态关
        （validate_formula），detail 为 errors 列表形制。"""
        with pytest.raises(ResearchError) as ei:
            register_formula_plugin(db, name="lb_hit", expression=expression)
        assert ei.value.code == "RESEARCH_FORMULA_INVALID"
        assert ei.value.status == 422
        errors = ei.value.detail["errors"]
        assert any("窗口超上限" in e and value_repr in e for e in errors), errors
        assert any("10000" in e for e in errors)  # 上限值随文案透出
        # 零副作用
        assert rr.list_signal_registry_entries(db) == []

    def test_超上限_拒绝_注册关_shift位(self, db: Path) -> None:
        """shift 无窗口位（静态放行）→ 仍走注册关 formula_lookback
        原路径（detail 为 lookback/max 形制）。"""
        with pytest.raises(ResearchError) as ei:
            register_formula_plugin(
                db, name="lb_shift", expression="shift(close, 200000)")
        assert ei.value.code == "RESEARCH_FORMULA_INVALID"
        assert ei.value.status == 422
        assert ei.value.detail["lookback"] == 200000
        assert ei.value.detail["max"] == 10_000
        # 零副作用
        assert rr.list_signal_registry_entries(db) == []

    def test_无窗口项_正常注册(self, db: Path) -> None:
        out = register_formula_plugin(
            db, name="nowin", expression="gt(close, 60000)")
        assert out["duplicated"] is False

    def test_幂等重放_不受上限校验影响(self, db: Path) -> None:
        """校验落点在幂等之后（CARD-02/23 先例同形）——合法注册的
        重放仍命中幂等。"""
        first = register_formula_plugin(
            db, name="idem", expression="zscore(close, 10000)")
        replay = register_formula_plugin(
            db, name="idem", expression="zscore(close, 10000)")
        assert replay["duplicated"] is True
        assert replay["plugin_id"] == first["plugin_id"]


class TestGapClosureInternal:
    """阶段四盲区消解：plugin_service 内部分支直测（此前经公开入口
    不可达或未触达的容错/装载路径）。"""

    def test_name与expression必填_422(self, db: Path) -> None:
        with pytest.raises(ResearchError, match="必填"):
            register_formula_plugin(db, name="", expression=EXPR)
        with pytest.raises(ResearchError, match="必填"):
            register_formula_plugin(db, name="x", expression="")

    def test_模型配置非法_422(self, db: Path, tmp_path: Path) -> None:
        from core.analysis.plugin_service import register_model_plugin
        with pytest.raises(ResearchError, match="模型配置非法"):
            register_model_plugin(
                db, tmp_path, name="bad", features=["close"],
                template="rolling_unknown",
                params={"window": 0, "step": 20},
                target={"type": "return_sign", "horizon": 4},
                symbol="BTCUSDT", timeframe="4h",
                data_start="2024-01-01", data_end="2024-03-01")

    def test_公式表达式查询_非公式列与无valid版本返回None(
            self, db: Path) -> None:
        from core.analysis.plugin_service import (
            _formula_expression_of, _formula_registry_entries)
        entries = _formula_registry_entries(db)
        # 空注册表：entry None
        assert _formula_expression_of(db, entries, "close") is None
        register_formula_plugin(db, name="alpha", expression=EXPR)
        entries = _formula_registry_entries(db)
        # formula 列正常返回表达式
        assert _formula_expression_of(db, entries, "fx_alpha") == EXPR
        # 非 formula 源列（model 注册列）→ None
        from sqlalchemy import insert
        from core.persistence.orm.models import ModelPlugin
        from core.persistence.repository.dataset_meta import _engine_for
        with _engine_for(db).begin() as conn:
            conn.execute(insert(ModelPlugin).values(
                plugin_id="mpl_m1", name="m", version=1, config="{}",
                signal_column="model_m", status="valid",
                strategy_visible=1, created_at="2026-09-19T00:00:00"))
        from core.persistence.repository import research as rr2
        rr2.register_existing_plugin_signal(
            db, source="model", plugin_id="mpl_m1", name="m", version=1,
            column_name="model_m")
        entries = _formula_registry_entries(db)
        assert _formula_expression_of(db, entries, "model_m") is None
        # 无 valid 版本（rejected 行）→ None
        out = register_formula_plugin(db, name="beta", expression=EXPR)
        from sqlalchemy import update
        from core.persistence.orm.models import FormulaPlugin
        with _engine_for(db).begin() as conn:
            conn.execute(update(FormulaPlugin).where(
                FormulaPlugin.plugin_id == out["plugin_id"]).values(
                status="rejected"))
        assert _formula_expression_of(db, entries, out["column"]) is None

    def test_表达式语法错误_引用集空集(self, db: Path) -> None:
        from core.analysis.plugin_service import _referenced_formula_columns
        assert _referenced_formula_columns("not a (formula", {}) == set()

    def test_依赖闭包_未知列与无效列跳过(self, db: Path) -> None:
        from core.analysis.plugin_service import _formula_dependency_closure
        # 未知列（不在注册表）→ continue 跳过
        assert _formula_dependency_closure(db, {"nope_col"}) == {}

    def test_求值容错_运行时缺列跳过与环中断(self, db: Path) -> None:
        """enrich 求值循环两容错：①引用列注册时合法（BASE 列）但 df
        缺失 → FormulaError continue（列不产出不中断）；②依赖不满足
        收敛 → break（保持缺失，消费端显式拒绝兜底）。"""
        from core.analysis.plugin_service import enrich_with_plugin_columns
        # 注册静态合法（open_interest ∈ BASE），求值时 df 无该列
        register_formula_plugin(db, name="bad_ref",
                                expression="zscore(open_interest, 20)")
        import pandas as pd
        idx = pd.date_range("2024-01-01", periods=50, freq="1h", tz="UTC",
                            name="timestamp")
        df = pd.DataFrame({"close": range(50)}, index=idx)
        out = enrich_with_plugin_columns(db, df, {"fx_bad_ref"})
        assert "fx_bad_ref" not in out.columns  # 失败列不产出

    def test_特征装载_混合形三路(self, db: Path) -> None:
        """_enrich_feature_columns：plugin 列走 enrich、indicator 列
        现算注入、基础列已在 df（model 训练特征装载三路）。"""
        from core.analysis.plugin_service import _enrich_feature_columns
        from core.analysis.column_ref import resolve_column_ref
        register_formula_plugin(db, name="feat_fx",
                                expression="zscore(close, 20)")
        import pandas as pd
        idx = pd.date_range("2024-01-01", periods=60, freq="1h", tz="UTC",
                            name="timestamp")
        df = pd.DataFrame({
            "open": range(60), "close": [i + 0.5 for i in range(60)],
            "high": range(60), "low": range(60),
            "volume": [10.0] * 60, "trades": 5}, index=idx)
        defs = [
            resolve_column_ref("close", db),
            resolve_column_ref("fx_feat_fx", db),
            resolve_column_ref({"indicator": "RSI",
                                "params": {"period": 14}}, db),
        ]
        out = _enrich_feature_columns(db, df, defs)
        assert "fx_feat_fx" in out.columns   # plugin 路经 enrich
        assert "rsi_14" in out.columns       # indicator 现算注入
        assert "close" in out.columns        # 基础列原位

    def test_幂等查询_坏config行容错(self, db: Path) -> None:
        """register_model_plugin 幂等扫描遇坏 JSON config 行跳过。"""
        from sqlalchemy import insert
        from core.analysis.plugin_service import register_model_plugin
        from core.persistence.orm.models import ModelPlugin
        from core.persistence.repository.dataset_meta import _engine_for
        with _engine_for(db).begin() as conn:
            conn.execute(insert(ModelPlugin).values(
                plugin_id="mpl_bad", name="gamma", version=1,
                config="{not json", signal_column="model_g",
                status="valid", strategy_visible=1,
                created_at="2026-09-19T00:00:00"))
        # 坏行跳过后不幂等命中（would-be 同名但 config 不可比对），
        # 走正常注册路径（假 executor 吸收训练）
        class _Exec:
            def submit(self, name, fn, **kw):
                return {"task_id": "t", "fast_path": False}

        import core.analysis.executor as exec_mod
        orig = exec_mod.get_executor
        exec_mod.get_executor = lambda: _Exec()
        try:
            out = register_model_plugin(
                db, db.parent, name="gamma", features=["close"],
                template="rolling_logistic",
                params={"window": 0, "step": 20},
                target={"type": "return_sign", "horizon": 4},
                symbol="BTCUSDT", timeframe="4h",
                data_start="2024-01-01", data_end="2024-03-01")
        finally:
            exec_mod.get_executor = orig
        assert out["status"] == "pending"


class TestGapClosurePlugin3:
    """第三批：闭包查询与特征装载剩余分支。"""

    def test_注册表在而插件行缺_表达式None(self, db: Path) -> None:
        from sqlalchemy import delete
        from core.analysis.plugin_service import (
            _formula_expression_of, _formula_registry_entries)
        from core.persistence.orm.models import FormulaPlugin
        from core.persistence.repository.dataset_meta import _engine_for
        out = register_formula_plugin(db, name="alpha", expression=EXPR)
        with _engine_for(db).begin() as conn:
            conn.execute(delete(FormulaPlugin).where(
                FormulaPlugin.plugin_id == out["plugin_id"]))
        entries = _formula_registry_entries(db)
        assert _formula_expression_of(db, entries, out["column"]) is None

    def test_特征装载_纯indicator无plugin直返(self, db: Path) -> None:
        from core.analysis.plugin_service import _enrich_feature_columns
        from core.analysis.column_ref import resolve_column_ref
        import pandas as pd
        idx = pd.date_range("2024-01-01", periods=60, freq="1h", tz="UTC",
                            name="timestamp")
        df = pd.DataFrame({
            "open": range(60), "close": [i + 0.5 for i in range(60)],
            "high": range(60), "low": range(60),
            "volume": [10.0] * 60, "trades": 5}, index=idx)
        defs = [resolve_column_ref("close", db),
                resolve_column_ref({"indicator": "RSI",
                                    "params": {"period": 14}}, db)]
        out = _enrich_feature_columns(db, df, defs)
        assert "rsi_14" in out.columns
        assert "fx_" not in "".join(out.columns)

    def test_特征装载_已存在指标列跳过现算(self, db: Path) -> None:
        from core.analysis.plugin_service import _enrich_feature_columns
        from core.analysis.column_ref import resolve_column_ref
        import pandas as pd
        idx = pd.date_range("2024-01-01", periods=60, freq="1h", tz="UTC",
                            name="timestamp")
        df = pd.DataFrame({
            "open": range(60), "close": [i + 0.5 for i in range(60)],
            "high": range(60), "low": range(60),
            "volume": [10.0] * 60, "trades": 5,
            "rsi_14": [50.0] * 60}, index=idx)  # 已含指标列
        defs = [resolve_column_ref({"indicator": "RSI",
                                    "params": {"period": 14}}, db)]
        out = _enrich_feature_columns(db, df, defs)
        assert (out["rsi_14"] == 50.0).all()  # 未被现算覆盖


class TestReferencedColumnsDeepNesting:
    """CARD-19：超长链/深嵌套解析超限的结构化降级（原裸穿 RecursionError）。

    可达性实证：链长 10000 的长二元 "+" 链此前在 ast.parse 递归超限
    裸穿；现按无公式引用降级 set()（formula.py 校验关同款收口形制）。
    """

    def test_超长链_解析超限降级空集(self) -> None:
        from core.analysis.plugin_service import _referenced_formula_columns
        expr = "+".join(["close"] * 10000)
        assert _referenced_formula_columns(expr, {}) == set()

    def test_深嵌套_解析超限降级空集(self) -> None:
        from core.analysis.plugin_service import _referenced_formula_columns
        expr = "close" + "*(close" * 3000 + ")" * 3000
        assert _referenced_formula_columns(expr, {}) == set()

    def test_合法表达式_提取不受影响(self) -> None:
        from core.analysis.plugin_service import _referenced_formula_columns
        entries = {"fx_a": {"source": "formula"}}
        assert _referenced_formula_columns("fx_a + close", entries) == {"fx_a"}
        assert _referenced_formula_columns("close + open", entries) == set()
