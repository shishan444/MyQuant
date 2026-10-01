"""插件注册用例（add-ai-research-mcp 5.2/5.4/5.5）：公式与模型。

生命周期（附.3）：提交 → 静态校验（公式三关 / 模型配置边界）→
valid 落库+signal_registry 注册（rejected 只留记录）→ 版本递增 →
可被分析与策略引用。

幂等与版本（设计卡 4/5）：
- 公式：(name, expression_hash) 相同 → 返回既有版本；同名不同表达式
  → (name,version) 唯一约束下取下一版本号；
- 模型：(name, config_hash) 相同 → 返回既有任务；训练异步（202+句柄，
  线程池执行器），失败产生 rejected 记录（不静默丢失，卡5）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional

from core.analysis.column_ref import BASE_SUBJECT_COLUMNS, _looks_like_encoded
from core.analysis.hypothesis_service import (
    ResearchError,
    allowed_subject_columns,
)
from core.features.registry import INDICATOR_REGISTRY
from core.persistence.repository import research as rr

# 模型训练任务超时上限（测试缝：测试经 monkeypatch 注入短超时验证链路；
# 生产默认 900s）
MODEL_TRAIN_TIMEOUT_S = 900.0


def _hash_str(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _plugin_allowed_columns(db_path: Path) -> set:
    """公式列引用集：基础列 ∪ 既有插件列（可叠层引用）。

    0.2.13：内置指标前缀名移除——DSL 求值上下文是原始 df（无指标列），
    前缀名放行即求值失败（R6 死代码退役）；指标在表达式内用白名单函数
    自算（0.2.12 正道）。"""
    cols, _builtin_prefixes = allowed_subject_columns(db_path)
    return cols


def _next_version(db_path: Path, table: str, name: str) -> int:
    """取 (name,version) 下一可用版本号。"""
    from sqlalchemy import func, select
    from core.persistence.orm.models import FormulaPlugin, ModelPlugin
    from core.persistence.repository.dataset_meta import _engine_for

    model = FormulaPlugin if table == "formula" else ModelPlugin
    with _engine_for(db_path).connect() as conn:
        current = conn.execute(
            select(func.max(model.version)).where(model.name == name)
        ).scalar()
    return (current or 0) + 1


# ---------------------------------------------------------------- 公式

def _assert_column_not_taken(db_path: Path, column_name: str) -> None:
    """插件列注册表全量撞名校验（formula/model 共享底座）：撞名 422。

    面取全量（与 _formula_registry_entries 求值闭包同面，非仅
    visible——隐藏列撞名同样污染叠层解析与引用判定）。
    """
    taken = {e["column_name"] for e in rr.list_signal_registry_entries(db_path)}
    if column_name in taken:
        raise ResearchError(
            "RESEARCH_FORMULA_INVALID",
            f"列名不可用: {column_name}（与既有插件列冲突——同名插件列"
            "求值互写覆盖，引用解析无法区分来源）",
            detail={"column": column_name, "conflict": "既有插件列",
                    "hint": "改用其他名称注册"})


def _no_param_indicator_columns() -> frozenset:
    """无参指标输出列全集（INDICATOR_REGISTRY 动态派生，不硬编码）。

    危害面：插件 output_column 冒充无参指标现算列——指标计算幂等
    （col 不在 df 才算），插件值先写即顶替指标值。有参指标列名带
    参数段（如 macd_histogram_12_26_9）已被编码形分面覆盖；无参列
    （obv/vwap/pattern_* 族）列名无参数段，需显式集合拦截。
    """
    return frozenset(
        f for defn in INDICATOR_REGISTRY.values() if not defn.params
        for f in defn.output_fields)


def _assert_output_column_free(
    db_path: Path, out_col: str, column_name: str,
) -> None:
    """公式插件输出列保留字校验（安全关）：撞名即 422 拒绝注册。

    危害链：插件求值把输出列直写 DataFrame（enrich_with_plugin_columns
    的 work[col]，写的是注册表列名 column_name）——撞基础列（OHLCV +
    open_interest/funding_rate 衍生合并列，即 BASE_SUBJECT_COLUMNS
    全集）覆盖价格数据；撞既有插件列产生同名列冲突；撞无参指标输出列
    顶替指标现算值（计算幂等跳过）；编码形（如 rsi_14）冒充指标编码
    列混淆字符串引用判定。

    分面（版本化语义不误杀）：out_col 裸名拦基础列/无参指标列/编码形
    （语义关，全版本——显式指定 close/obv/rsi_14 形一律拒）；既有
    插件列只拦真正写 df 的 column_name（委托 _assert_column_not_taken）
    ——version>1 时注册列名带 _v{N} 后缀，裸名与 v1 同名是同名升版
    的正常形态。
    """
    fixed_cols = _no_param_indicator_columns()
    for col in (out_col, column_name):
        if col in BASE_SUBJECT_COLUMNS:
            conflict = "基础数据列"
        elif col in fixed_cols:
            conflict = "无参指标输出列"
        elif _looks_like_encoded(col):
            conflict = "参数化指标编码形列名"
        else:
            continue
        raise ResearchError(
            "RESEARCH_FORMULA_INVALID",
            f"output_column 不可用: {col}（与{conflict}冲突——插件求值"
            "直写同名 DataFrame 列，将覆盖既有数据）",
            detail={"output_column": col, "conflict": conflict,
                    "hint": "改用其他列名；缺省 fx_ 前缀天然避开保留字"})
    _assert_column_not_taken(db_path, column_name)


def register_formula_plugin(
    db_path: Path,
    *,
    name: str,
    expression: str,
    output_column: Optional[str] = None,
) -> Dict[str, Any]:
    """公式插件注册：静态校验三关 → 幂等 → 版本化 → 注册（原子）。"""
    from core.signals.formula import validate_formula

    if not name or not expression:
        raise ResearchError("RESEARCH_FORMULA_INVALID", "name 与 expression 必填")

    # 列引用集（含既有插件列——插件可引用插件）
    allowed = _plugin_allowed_columns(db_path)

    errors = validate_formula(expression, allowed)
    if errors:
        raise ResearchError(
            "RESEARCH_FORMULA_INVALID",
            f"公式静态校验未通过（{len(errors)} 项）",
            detail={"errors": errors})

    # 幂等：同名同表达式且 valid → 返回既有（column 取 signal_registry
    # 注册列名——单一事实源：output_columns 是 JSON 留档且不带版本后缀，
    # version≥2 时回传它会把 AI 引用指向不存在的裸列名）
    from sqlalchemy import select
    from core.persistence.orm.models import FormulaPlugin
    from core.persistence.repository.dataset_meta import _engine_for
    with _engine_for(db_path).connect() as conn:
        rows = conn.execute(
            select(FormulaPlugin).where(FormulaPlugin.name == name)
        ).mappings().all()
    registry_column = {
        e["plugin_id"]: e["column_name"]
        for e in rr.list_signal_registry_entries(db_path)}
    for row in rows:
        if row["expression"] == expression and row["status"] == "valid":
            plugin_id = row["plugin_id"]
            column = registry_column.get(plugin_id)
            if column is None:
                # valid 插件与注册条目原子双写，缺失即数据完整性异常——
                # 显式失败优于回传坏引用（AI 拿 null/错名都会引用断链）
                raise ResearchError(
                    "RESEARCH_FORMULA_REGISTRY_MISSING",
                    f"插件注册表条目缺失: {plugin_id}",
                    status=500)
            return {"plugin_id": plugin_id, "name": name,
                    "version": row["version"], "column": column,
                    "duplicated": True}

    # 版本递增 + 原子注册（输出列保留字校验先行——撞名 422 拒绝）
    out_col = output_column or f"fx_{name}"
    version = _next_version(db_path, "formula", name)
    column_name = out_col if version == 1 else f"{out_col}_v{version}"
    _assert_output_column_free(db_path, out_col, column_name)
    plugin_id = f"fpl_{hashlib.sha256(f'{name}{version}'.encode()).hexdigest()[:10]}"
    import uuid
    entry_id = f"ent_{uuid.uuid4().hex[:12]}"
    rr.save_plugin_with_registry(
        db_path, source="formula", plugin_id=plugin_id, name=name,
        version=version, expression=expression,
        output_columns=json.dumps([out_col]),
        entry_id=entry_id, column_name=column_name,
    )
    return {"plugin_id": plugin_id, "name": name, "version": version,
            "column": column_name,
            "duplicated": False}


def rejected_formula_record(
    db_path: Path, *, name: str, expression: str, reason: str,
) -> str:
    """校验失败的 rejected 记录（只留档不注册；调用方在拒绝路径使用）。"""
    plugin_id = f"fpl_{hashlib.sha256(f'{name}{reason}'.encode()).hexdigest()[:10]}"
    rr.save_plugin_with_registry(
        db_path, source="formula", plugin_id=plugin_id, name=name,
        version=1, expression=expression, output_columns="[]",
        status="rejected", reject_reason=reason)
    return plugin_id


def _formula_registry_entries(db_path: Path) -> Dict[str, Any]:
    """注册表条目按列名索引（插件列解析共享底座——求值闭包与
    lookback 闭包同源，防两套解析漂移）。"""
    return {e["column_name"]: e for e in rr.list_signal_registry_entries(db_path)}


def _formula_expression_of(
    db_path: Path, entries: Dict[str, Any], col: str,
) -> Optional[str]:
    """列 → valid 公式插件的 expression（非公式列 / 无 valid 版本 → None）。"""
    entry = entries.get(col)
    if entry is None or entry["source"] != "formula":
        return None
    from sqlalchemy import select
    from core.persistence.orm.models import FormulaPlugin
    from core.persistence.repository.dataset_meta import _engine_for
    with _engine_for(db_path).connect() as conn:
        row = conn.execute(
            select(FormulaPlugin)
            .where(FormulaPlugin.plugin_id == entry["plugin_id"])
        ).mappings().first()
    if row is None or row["status"] != "valid":
        return None
    return row["expression"]


def _referenced_formula_columns(expr: str, entries: Dict[str, Any]) -> set:
    """表达式 AST 中引用的公式插件列（叠层引用解析）。"""
    import ast as _ast
    try:
        tree = _ast.parse(expr, mode="eval")
    except SyntaxError:
        return set()
    names = {n.id for n in _ast.walk(tree) if isinstance(n, _ast.Name)}
    return {n for n in names if n in entries
            and entries[n]["source"] == "formula"}


def _formula_dependency_closure(
    db_path: Path, needed_columns: set,
) -> Dict[str, str]:
    """依赖闭包：col → expression（叠层递归展开；被引用列不在
    needed 时也展开——否则叠层引用静默缺失）。"""
    entries = _formula_registry_entries(db_path)
    queue = list(needed_columns)
    pending = set()
    exprs: Dict[str, str] = {}
    while queue:
        col = queue.pop(0)
        if col in pending or col not in entries:
            continue
        expr = _formula_expression_of(db_path, entries, col)
        if expr is None:
            continue
        pending.add(col)
        exprs[col] = expr
        queue.extend(_referenced_formula_columns(expr, entries) - pending)
    return exprs


def plugin_columns_lookback(db_path: Path, columns: set) -> int:
    """插件列（含依赖闭包）回看窗口静态推导最大值——验证回测 warmup
    扩展输入（与内置 derive_warmup 取大、floor 200 兜底在调用处）。

    依赖闭包递归与 enrich_with_plugin_columns 共享解析（warmup 覆盖面
    = 求值面）；列不存在 / 非公式 / 已失效 → 跳过（求值端显式拒绝
    兜底，此处只算窗口）。无窗口项表达式贡献 0。
    """
    from core.signals.formula import formula_lookback

    lookback = 0
    for expr in _formula_dependency_closure(db_path, columns).values():
        lookback = max(lookback, formula_lookback(expr))
    return lookback


def enrich_with_plugin_columns(
    db_path: Path, df, needed_columns: set,
) -> Any:
    """把公式插件列求值附加到 df（按需：仅被引用的列）。

    支持插件引用插件（叠层）：先从表达式解析被引用的插件列并
    递归加入求值集（依赖列不在 needed 时也要求值——否则叠层引用
    静默缺失；此前两轮迭代假设依赖同在 needed，实测不成立）。
    """
    from core.signals.formula import FormulaError, evaluate_formula

    if not needed_columns:
        return df

    # 依赖闭包（叠层：fx_b 引用 fx_a → fx_a 一并求值）——与
    # plugin_columns_lookback 共享解析（warmup 覆盖面 = 求值面）
    exprs = _formula_dependency_closure(db_path, needed_columns)
    entries = _formula_registry_entries(db_path)

    env: Dict[str, Any] = {}
    work = df.copy()
    # 按依赖序（被依赖者先）：拓扑序迭代至收敛（无环由注册约束保证）
    remaining = dict(exprs)
    while remaining:
        progressed = False
        for col, expr in list(remaining.items()):
            deps = _referenced_formula_columns(expr, entries) & set(remaining)
            if deps:
                continue  # 依赖未求值，下一轮
            try:
                series = evaluate_formula(expr, df, extra_columns=env)
            except FormulaError:
                continue
            env[col] = series
            work[col] = series
            del remaining[col]
            progressed = True
        if not progressed:
            break  # 环或不满足——保持缺失（消费端显式拒绝兜底）
    return work


def _enrich_feature_columns(db_path: Path, df, feature_defs):
    """训练特征统一装载（校验即解析的运行半边——两域同源）。

    插件列走既有 enrich_with_plugin_columns（叠层求值）；指标列按
    params 现算注入；基础列已在 df。ColumnDef 惰性 encoded_name 同源。
    """
    plugin_cols = {d.column_name for d in feature_defs if d.kind == "plugin"}
    if plugin_cols:
        df = enrich_with_plugin_columns(db_path, df, plugin_cols)
    for d in feature_defs:
        if d.kind != "indicator":
            continue
        col = d.encoded_name()
        if col in df.columns:
            continue
        from core.features.indicators import _compute_indicator
        for cname, series in _compute_indicator(
                df, d.indicator, dict(d.params)).items():
            df[cname] = series
    return df


def _copy_config_with_columns(config, feature_defs):
    """config 副本：features 替换为解析出的精确列名（管线按列名取数）。"""
    import copy as _copy
    run_cfg = _copy.copy(config)
    run_cfg.features = [d.encoded_name() for d in feature_defs]
    return run_cfg


# ---------------------------------------------------------------- 模型

def register_model_plugin(
    db_path: Path,
    data_dir: Path,
    *,
    name: str,
    features: list,
    template: str,
    params: Dict[str, Any],
    target: Dict[str, Any],
    symbol: str,
    timeframe: str,
    data_start: str,
    data_end: str,
) -> Dict[str, Any]:
    """模型插件配置：同步校验 + 异步训练（线程池；202+句柄语义）。

    Returns:
        {"task_id", "status": "pending"} 或既有 valid 插件（幂等命中）。
    """
    from core.signals.model import ModelPluginConfig, ModelPipelineError

    config = ModelPluginConfig(
        name=name, features=features, template=template,
        params=params, target=target)
    try:
        config.validate()
    except ModelPipelineError as exc:
        raise ResearchError("RESEARCH_FORMULA_INVALID", f"模型配置非法: {exc}")

    # 特征引用校验即解析（0.2.13：混合形制——参数化指标结构化对象 +
    # 基础列/插件列字符串；前缀名语义退役——校验放行即运行可取，两域同源）
    from core.analysis.column_ref import (ColumnDef, ColumnRefError,
                                          resolve_column_ref)
    feature_defs: list = []
    bad: list = []
    for f in features:
        try:
            feature_defs.append(resolve_column_ref(f, db_path))
        except ColumnRefError as exc:
            bad.append(f"{f!r}: {exc.message}")
    if bad:
        raise ResearchError(
            "RESEARCH_CONDITION_INVALID",
            f"特征引用不可用: {bad}",
            detail={"hint": "基础列/插件列用字符串精确名，参数化指标用"
                            "{indicator, params, field}；可用列经 "
                            "GET /api/research/signals 查询"})
    features = [d.ref_dict() for d in feature_defs]   # canonical 化落 config

    # 幂等：(name, config_hash) 相同且 valid → 返回既有
    config_hash = _hash_json({
        "name": name, "features": features, "template": template,
        "params": params, "target": target,
        "symbol": symbol, "timeframe": timeframe,
        "data": [data_start, data_end],
    })
    from sqlalchemy import select
    from core.persistence.orm.models import ModelPlugin
    from core.persistence.repository.dataset_meta import _engine_for
    with _engine_for(db_path).connect() as conn:
        rows = conn.execute(
            select(ModelPlugin).where(ModelPlugin.name == name)
        ).mappings().all()
    for row in rows:
        try:
            row_cfg = json.loads(row["config"])
        except (json.JSONDecodeError, TypeError):
            continue
        if row_cfg.get("_config_hash") == config_hash and row["status"] == "valid":
            return {"plugin_id": row["plugin_id"], "name": name,
                    "version": row["version"], "column": row["signal_column"],
                    "duplicated": True}

    # 异步训练（线程池执行器，卡5 状态机 pending→valid|rejected）
    from core.analysis.executor import get_executor

    version = _next_version(db_path, "model", name)
    plugin_id = f"mpl_{hashlib.sha256(f'{name}{version}'.encode()).hexdigest()[:10]}"
    signal_column = f"model_{name}" if version == 1 else f"model_{name}_v{version}"
    # 撞既有插件列 422（model_ 前缀强制撞不了基础列/编码形，只查 taken
    # ——不同 name 组合可产生同名列，如 name="x" v2 与 name="x_v2" v1
    # 都产生 model_x_v2）
    _assert_column_not_taken(db_path, signal_column)
    cfg_json = json.dumps({**config.__dict__, "_config_hash": config_hash},
                          ensure_ascii=False, default=str)

    from core.persistence.repository.dataset_meta import _engine_for as _eng
    from sqlalchemy import insert as _insert
    now = None
    from core.persistence.timestamps import utc_now_iso
    with _eng(db_path).begin() as conn:
        conn.execute(_insert(ModelPlugin).values(
            plugin_id=plugin_id, name=name, version=version,
            config=cfg_json, signal_column=signal_column,
            status="pending", created_at=utc_now_iso()))

    def _train(stop_check):
        from core.data.mtf_loader import load_and_prepare_df
        from core.signals.model import fit_predict_walk_forward
        try:
            df = load_and_prepare_df(
                data_dir, symbol, timeframe,
                data_start=data_start, data_end=data_end,
                compute_indicators=False)
            if df is None:
                raise RuntimeError(f"区间数据不足: {symbol} {timeframe}")
            # 特征装载（0.2.13 统一入口）：插件列求值附加 + 指标对象按
            # params 现算 + 基础列直取；训练管线按解析出的精确列名取数
            # （config.features 替换为列名列表——model 管线零改动）
            df = _enrich_feature_columns(db_path, df, feature_defs)
            run_cfg = _copy_config_with_columns(config, feature_defs)
            res = fit_predict_walk_forward(df, run_cfg)
        except Exception as exc:  # noqa: BLE001 —— 训练失败自落 rejected（不静默丢失）
            _fail(str(exc))
            raise
        # valid 落库（插件行已存在，只更新状态）+ 注册信号列
        from sqlalchemy import update as _update
        with _eng(db_path).begin() as conn:
            conn.execute(_update(ModelPlugin).where(
                ModelPlugin.plugin_id == plugin_id).values(
                status="valid",
                oos_summary=json.dumps(res.oos_summary)))
        rr.register_existing_plugin_signal(
            db_path, source="model", plugin_id=plugin_id, name=name,
            version=version, column_name=signal_column,
            extra={"oos": res.oos_summary})
        return json.dumps({"plugin_id": plugin_id, "signal_column": signal_column,
                           "oos_summary": res.oos_summary,
                           "n_refits": res.n_refits}, ensure_ascii=False)

    def _fail(reason: str):
        from sqlalchemy import update as _update
        with _eng(db_path).begin() as conn:
            conn.execute(_update(ModelPlugin).where(
                ModelPlugin.plugin_id == plugin_id).values(
                status="rejected", reject_reason=reason[:200]))

    handle = get_executor().submit(
        f"model_train:{name}", _train, timeout_s=MODEL_TRAIN_TIMEOUT_S,
        params_json=cfg_json, estimated_cost_s=10.0)
    task_id = handle.get("task_id")
    if handle["fast_path"]:
        # 不应发生（estimated_cost 强制重组路径）；防御处理
        if handle["status"] == "failed":
            _fail(handle.get("reason", "fast_path_failed"))
        return {"plugin_id": plugin_id, "status": handle["status"],
                "task_id": task_id, "signal_column": signal_column,
                "duplicated": False}

    return {"plugin_id": plugin_id, "status": "pending", "task_id": task_id,
            "signal_column": signal_column, "duplicated": False,
            "poll": f"/api/research/analysis/tasks/{task_id}"}


def _hash_json(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
