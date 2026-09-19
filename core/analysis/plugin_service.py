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

from core.analysis.hypothesis_service import (
    ResearchError,
    allowed_subject_columns,
)
from core.persistence.repository import research as rr


def _hash_str(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _plugin_allowed_columns(db_path: Path) -> set:
    """公式列引用集：基础列 ∪ 指标输出列前缀 ∪ 既有插件列（可叠层引用）。"""
    cols, builtin_prefixes = allowed_subject_columns(db_path)
    return cols | builtin_prefixes  # 前缀以精确名形式进入（指标参数化列形制）


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

    # 幂等：同名同表达式且 valid → 返回既有
    from sqlalchemy import select
    from core.persistence.orm.models import FormulaPlugin
    from core.persistence.repository.dataset_meta import _engine_for
    with _engine_for(db_path).connect() as conn:
        rows = conn.execute(
            select(FormulaPlugin).where(FormulaPlugin.name == name)
        ).mappings().all()
    for row in rows:
        if row["expression"] == expression and row["status"] == "valid":
            return {"plugin_id": row["plugin_id"], "name": name,
                    "version": row["version"], "column": row["output_columns"],
                    "duplicated": True}

    # 版本递增 + 原子注册
    out_col = output_column or f"fx_{name}"
    version = _next_version(db_path, "formula", name)
    plugin_id = f"fpl_{hashlib.sha256(f'{name}{version}'.encode()).hexdigest()[:10]}"
    import uuid
    entry_id = f"ent_{uuid.uuid4().hex[:12]}"
    rr.save_plugin_with_registry(
        db_path, source="formula", plugin_id=plugin_id, name=name,
        version=version, expression=expression,
        output_columns=json.dumps([out_col]),
        entry_id=entry_id, column_name=out_col if version == 1 else f"{out_col}_v{version}",
    )
    return {"plugin_id": plugin_id, "name": name, "version": version,
            "column": out_col if version == 1 else f"{out_col}_v{version}",
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


def enrich_with_plugin_columns(
    db_path: Path, df, needed_columns: set,
) -> Any:
    """把公式插件列求值附加到 df（按需：仅被引用的列）。

    支持插件引用插件（叠层）：按依赖序迭代求值（简化为两轮——
    注册时列集已含既有插件，公式不可引用未注册列，拓扑天然无环）。
    """
    from core.signals.formula import FormulaError, evaluate_formula

    if not needed_columns:
        return df
    entries = {e["column_name"]: e for e in rr.list_signal_registry_entries(db_path)}
    env: Dict[str, Any] = {}
    work = df.copy()
    pending = set(needed_columns)
    for _round in range(2):  # 两轮覆盖一层插件间引用
        still = set(pending)
        for col in still:
            entry = entries.get(col)
            if entry is None or entry["source"] != "formula":
                continue
            # 取该列当前版本的表达式（插件表最新 valid 行）
            from sqlalchemy import select
            from core.persistence.orm.models import FormulaPlugin
            from core.persistence.repository.dataset_meta import _engine_for
            with _engine_for(db_path).connect() as conn:
                row = conn.execute(
                    select(FormulaPlugin)
                    .where(FormulaPlugin.plugin_id == entry["plugin_id"])
                ).mappings().first()
            if row is None or row["status"] != "valid":
                continue
            try:
                series = evaluate_formula(
                    row["expression"], df, extra_columns=env)
            except FormulaError:
                continue
            env[col] = series
            work[col] = series
            pending.discard(col)
    return work


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

    # 特征列存在性
    allowed = _plugin_allowed_columns(db_path)
    missing = [f for f in features if f not in allowed]
    if missing:
        raise ResearchError(
            "RESEARCH_CONDITION_INVALID",
            f"特征列不可用: {missing}",
            detail={"hint": "GET /api/research/signals 查询可用列"})

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
            # 特征可能是插件列——先求值附加
            df = enrich_with_plugin_columns(db_path, df, set(features))
            res = fit_predict_walk_forward(df, config)
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
        f"model_train:{name}", _train, timeout_s=900.0,
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
