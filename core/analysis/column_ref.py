"""列引用单一事实源（0.2.13 unify-column-references B1-B3）。

混合形制（Q2 拍板）：
- 参数化指标：结构化 ``{indicator, params, field}``——indicator 查注册表、
  params 按参数空间校验、多输出指标 field 必填（单输出缺省注入唯一列）。
- 基础列（OHLCV/衍生品）与插件列（fx_*/model_*）：字符串精确名。

三向映射与 canonical：
- ``resolve_column_ref``：混合形制 → ColumnDef（五域消费的统一校验入口）。
- ``encode_column_ref``：委托 ``registry.resolve_indicator_column``（encode
  权威已存在，不建第二实现——分端轮 B1 裁决）。
- ``decode_column_ref``：编码列名 → 结构化引用（12 naming 模式逆文法，
  注册表驱动；迁移脚本消费，往返一致锁在调用方）。
- ``canonical_ref``：引用的规范化编码（指纹与 conditions_hash 幂等连续
  的同源基础——与登记/迁移共用，无第二实现）。

字符串编码的参数化指标引用（前缀+参数数字形）迁移后对新提交关闭
（RESEARCH_CONDITION_INVALID 族由消费方转换；本模块抛 ColumnRefError）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.features.registry import INDICATOR_REGISTRY, resolve_indicator_column


class ColumnRefError(ValueError):
    """列引用解析失败（消费方转 RESEARCH_CONDITION_INVALID 族）。

    code: NOT_FOUND / ENCODED_STRING_REJECTED / PARAM_INVALID /
    FIELD_REQUIRED / FIELD_UNKNOWN / AMBIGUOUS_DECODE。
    """

    def __init__(self, code: str, message: str, detail: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


#: 基础列清单（自 hypothesis_service 移入——单一事实源；假设引擎
#: _resolve_subject 的列语义不变）。add-session-structure-expression
#: 任务 7 起 dict 形（值=域元数据；八列空元数据=零变化）——第九列
#: hour_utc 带 intraday_only 域标记（仅日内周期可引用，日线及以上
#: 拒绝）。`in` 判定 dict 与 frozenset 同语义，七处消费方零适配。
BASE_SUBJECT_COLUMNS = {
    "open": {}, "high": {}, "low": {}, "close": {}, "volume": {},
    "trades": {}, "open_interest": {}, "funding_rate": {},
    "hour_utc": {"intraday_only": True},
}


@dataclass
class ColumnDef:
    """列引用解析结果（column_name 惰性由 encode 得出）。"""
    kind: str                 # indicator | base | plugin
    indicator: Optional[str] = None
    params: Dict[str, Any] = dc_field(default_factory=dict)
    field: Optional[str] = None
    column_name: Optional[str] = None   # indicator 类惰性填充；base/plugin 即引用名

    def ref_dict(self) -> Any:
        """canonical 形引用（登记入口归一化与迁移产出的同源基础）。

        指标类：params 数值归一（整数值 float→int——14.0 与 14 同 hash）、
        field 恒注入；base/plugin：字符串原样。
        """
        if self.kind != "indicator":
            return self.column_name
        params = {k: (int(v) if isinstance(v, float) and v.is_integer() else v)
                  for k, v in self.params.items()}
        return {"indicator": self.indicator, "params": params,
                "field": self.field}

    def encoded_name(self) -> str:
        if self.kind != "indicator":
            return str(self.column_name)
        if self.column_name is None:
            self.column_name = encode_column_ref(
                self.indicator, self.params, self.field)
        return self.column_name


# ---------------------------------------------------------------- encode

def encode_column_ref(indicator: str, params: Dict[str, Any],
                      field_name: Optional[str] = None) -> str:
    """编码列名（委托 registry.resolve_indicator_column——生产权威）。"""
    return resolve_indicator_column(indicator, dict(params),
                                    field_name or "")


# ---------------------------------------------------------------- resolve

def _visible_plugin_columns(db_path: Path) -> set:
    from core.persistence.repository import research as rr
    return {e["column_name"]
            for e in rr.list_signal_registry_entries(db_path, only_visible=True)}


def _looks_like_encoded(name: str) -> bool:
    """字符串是否为「参数化指标编码形」（前缀+数字，含多段）——入口关闭
    的判定面（供拒绝详情提示结构化形制）。"""
    try:
        return decode_column_ref(name) is not None
    except ColumnRefError:
        return True


# 日内周期上界（hour_utc 域校验）：周期分钟超过该值的禁止引用
# intraday_only 列（日线 1d=1440 分钟及以上）
_INTRADAY_MINUTES_MAX = 720  # 12h 及更细粒度为日内；1d=1440 拒


def _check_base_column_domain(column: str, base_tf) -> None:
    """带域元数据基础列的周期域校验（任务 7 单点）。

    现唯一域标记 intraday_only（hour_utc）：base_tf 周期分钟 >
    _INTRADAY_MINUTES_MAX 时拒绝（日线及以上引用小时列无意义——
    每日一 bar 值恒定）；base_tf None/未知周期跳过（兼容面——
    供给方三面各自传执行周期）。
    """
    meta = BASE_SUBJECT_COLUMNS.get(column)
    if not meta or not meta.get("intraday_only") or base_tf is None:
        return
    from core.data.timeframes import TF_SECONDS
    minutes = TF_SECONDS.get(base_tf)
    if minutes is None:
        return
    if minutes / 60.0 > _INTRADAY_MINUTES_MAX:
        raise ColumnRefError(
            "PARAM_INVALID",
            f"{column} 为日内列（intraday_only），基准周期 {base_tf} "
            "（日线及以上）不可引用——每日一 bar 下该列值恒定无意义",
            detail={"column": column, "base_tf": base_tf,
                    "domain": "intraday_only",
                    "hint": "换 12h 及以下周期，或改用其他基础列"})


def resolve_column_ref(ref: Any, db_path: Optional[Path] = None,
                       base_tf: Optional[str] = None) -> ColumnDef:
    """混合形制校验（五域统一入口）。

    Args:
        ref: 结构化 dict（参数化指标）或 str（基础列/插件列精确名）。
        db_path: 插件列注册表所在库（插件列判定需要；None 时跳过插件判定）。

    Returns:
        ColumnDef。

    Raises:
        ColumnRefError: 结构化校验失败 / 字符串引用不存在 / 字符串编码形
        被拒（ENCODED_STRING_REJECTED——迁移后入口关闭语义）。
    """
    if isinstance(ref, dict):
        return _resolve_indicator_ref(ref)

    if isinstance(ref, str):
        if ref in BASE_SUBJECT_COLUMNS:
            _check_base_column_domain(ref, base_tf)
            return ColumnDef(kind="base", column_name=ref)
        if db_path is not None and ref in _visible_plugin_columns(db_path):
            return ColumnDef(kind="plugin", column_name=ref)
        if _looks_like_encoded(ref):
            raise ColumnRefError(
                "ENCODED_STRING_REJECTED",
                f"参数化指标引用不接受字符串编码形（{ref}）——"
                "统一为结构化写法 {indicator, params, field}",
                detail={"subject": ref,
                        "hint": "可用指标与参数空间经 GET /api/research/signals 查询"})
        raise ColumnRefError(
            "NOT_FOUND",
            f"引用不存在的信号列: {ref}",
            detail={"subject": ref,
                    "hint": "可用列经 GET /api/research/signals 查询"})

    raise ColumnRefError(
        "PARAM_INVALID",
        f"列引用须为结构化对象或字符串精确名，实得 {type(ref).__name__}",
        detail={"subject": ref})


def _resolve_indicator_ref(ref: Dict[str, Any]) -> ColumnDef:
    indicator = ref.get("indicator")
    if not isinstance(indicator, str) or indicator not in INDICATOR_REGISTRY:
        raise ColumnRefError(
            "PARAM_INVALID",
            f"indicator 未知或缺失: {indicator!r}",
            detail={"indicator": indicator,
                    "hint": "可用指标经 GET /api/research/indicators 查询"})
    defn = INDICATOR_REGISTRY[indicator]

    params = ref.get("params")
    if not isinstance(params, dict):
        raise ColumnRefError(
            "PARAM_INVALID",
            f"{indicator} 的 params 须为对象（无参指标传 {{}}）",
            detail={"indicator": indicator})

    missing = [k for k in defn.params if k not in params]
    extra = [k for k in params if k not in defn.params]
    if missing or extra:
        raise ColumnRefError(
            "PARAM_INVALID",
            f"{indicator} 参数键不匹配（缺 {missing} 多余 {extra}；"
            f"合法键 {sorted(defn.params)}）",
            detail={"indicator": indicator, "missing": missing, "extra": extra})

    for key, pdef in defn.params.items():
        val = params[key]
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise ColumnRefError(
                "PARAM_INVALID",
                f"{indicator}.{key} 须为数值，实得 {type(val).__name__}",
                detail={"indicator": indicator, "key": key})
        if not (pdef.min <= val <= pdef.max):
            raise ColumnRefError(
                "PARAM_INVALID",
                f"{indicator}.{key}={val} 越界（合法 [{pdef.min}, {pdef.max}]）",
                detail={"indicator": indicator, "key": key, "value": val})

    # 跨参数约束钩子（add-session-structure-expression 任务 1）：
    # 逐参数域校验通过后的联合校验（如时段起止序）——无钩子指标
    # （cross_params=None）零行为变化
    hook = getattr(defn, "cross_params", None)
    if hook is not None:
        violation = hook(params)
        if violation is not None:
            raise ColumnRefError(
                "PARAM_INVALID",
                f"{indicator} 跨参数约束不满足: {violation}",
                detail={"indicator": indicator, "params": dict(params),
                        "violation": violation})

    outputs = list(defn.output_fields)
    field_name = ref.get("field")
    if len(outputs) > 1:
        if not field_name:
            raise ColumnRefError(
                "FIELD_REQUIRED",
                f"{indicator} 为多输出指标（{outputs}），field 必填",
                detail={"indicator": indicator, "output_fields": outputs})
    else:
        field_name = field_name or outputs[0]
    if field_name not in outputs:
        raise ColumnRefError(
            "FIELD_UNKNOWN",
            f"{indicator}.field={field_name!r} 不在输出列 {outputs}",
            detail={"indicator": indicator, "field": field_name,
                    "output_fields": outputs})

    return ColumnDef(kind="indicator", indicator=indicator,
                     params=dict(params), field=field_name)


# ---------------------------------------------------------------- decode

_DECODERS: Optional[List[Tuple[re.Pattern, str, Dict[str, Any]]]] = None
# 每条：(compiled, indicator, 提取规则)；提取规则含 field_group（组名→
# 输出列）与 param_groups（组名→参数键）——由注册表 naming 生成。


def _build_decoders() -> List[Tuple[re.Pattern, str, Dict[str, Any]]]:
    """注册表驱动的逆文法表（12 naming 模式——与 resolve 各分支对偶）。"""
    from core.features.registry import _PATTERN_COLUMNS

    rules: List[Tuple[re.Pattern, str, Dict[str, Any]]] = []
    pat_num = r"(\d+)"
    pat_float = r"(\d+(?:\.\d+)?)"

    for name, defn in INDICATOR_REGISTRY.items():
        naming = defn.naming
        if naming == "default":
            # 单输出前缀_数字（0 参指标列名无后缀——不在此产生可解码形，
            # 结构化引用走 resolve；多参 default 不存在，实测确认）
            if defn.params and "period" in defn.params:
                prefix = re.escape(defn.output_fields[0])
                rules.append((re.compile(rf"^{prefix}_{pat_num}$"), name,
                              {"field": defn.output_fields[0],
                               "param_groups": {"1": "period"},
                               "float_groups": set()}))
        elif naming == "bb":
            fields_alt = "|".join(re.escape(f) for f in defn.output_fields)
            rules.append((re.compile(
                rf"^bb_({fields_alt})_{pat_num}_{pat_float}$"), name,
                {"field_group": "1", "param_groups": {"2": "period", "3": "std"},
                 "float_groups": {"std"}}))
        elif naming == "macd":
            # field 语义名：无后缀=macd；signal_/histogram_ 显式
            for sema, pref in (("macd", "macd_"), ("signal", "macd_signal_"),
                               ("histogram", "macd_histogram_")):
                rules.append((re.compile(
                    rf"^{re.escape(pref)}{pat_num}_{pat_num}_{pat_num}$"), name,
                    {"field": sema if sema != "macd" else "macd",
                     "param_groups": {"1": "fast", "2": "slow", "3": "signal"},
                     "float_groups": set()}))
        elif naming == "stoch":
            for sema in ("k", "d"):
                rules.append((re.compile(
                    rf"^stoch_{sema}_{pat_num}_{pat_num}$"), name,
                    {"field": sema,
                     "param_groups": {"1": "k_period", "2": "d_period"},
                     "float_groups": set()}))
        elif naming == "session":
            fields_alt = "|".join(re.escape(f) for f in defn.output_fields)
            rules.append((re.compile(
                rf"^({fields_alt})_{pat_num}_{pat_num}$"), name,
                {"field_group": "1",
                 "param_groups": {"2": "start_minute", "3": "end_minute"},
                 "float_groups": set()}))
        elif naming == "pivot":
            fields_alt = "|".join(re.escape(f) for f in defn.output_fields)
            rules.append((re.compile(
                rf"^({fields_alt})_{pat_num}_{pat_num}$"), name,
                {"field_group": "1",
                 "param_groups": {"2": "left", "3": "right"},
                 "float_groups": set()}))
        elif naming == "supertrend":
            # 两 pattern 固定 field（dir 列与主线列；field 不进组——
            # 列名前缀即字段语义）
            rules.append((re.compile(
                rf"^supertrend_dir_{pat_num}_{pat_float}$"), name,
                {"field": "supertrend_dir",
                 "param_groups": {"1": "atr_period", "2": "multiplier"},
                 "float_groups": {"multiplier"}}))
            rules.append((re.compile(
                rf"^supertrend_{pat_num}_{pat_float}$"), name,
                {"field": "supertrend",
                 "param_groups": {"1": "atr_period", "2": "multiplier"},
                 "float_groups": {"multiplier"}}))
        elif naming == "kc":
            fields_alt = "|".join(re.escape(f) for f in defn.output_fields)
            rules.append((re.compile(
                rf"^kc_{pat_num}_{pat_num}_{pat_float}_({fields_alt})$"), name,
                {"field_group": "4", "param_groups":
                 {"1": "ema_period", "2": "atr_period", "3": "multiplier"},
                 "float_groups": {"multiplier"}}))
        elif naming == "dc":
            fields_alt = "|".join(re.escape(f) for f in defn.output_fields)
            rules.append((re.compile(
                rf"^dc_({fields_alt})_{pat_num}$"), name,
                {"field_group": "1", "param_groups": {"2": "period"},
                 "float_groups": set()}))
        elif naming == "vp":
            fields_alt = "|".join(re.escape(f) for f in defn.output_fields)
            rules.append((re.compile(
                rf"^({fields_alt})_{pat_num}_{pat_num}$"), name,
                {"field_group": "1", "param_groups": {"2": "bins", "3": "lookback"},
                 "float_groups": set()}))
        elif naming == "aroon":
            fields_alt = "|".join(re.escape(f) for f in defn.output_fields)
            rules.append((re.compile(
                rf"^({fields_alt})_{pat_num}$"), name,
                {"field_group": "1", "param_groups": {"2": "period"},
                 "float_groups": set()}))
        elif naming == "mfe":
            rules.append((re.compile(
                rf"^{re.escape(defn.output_fields[0])}_{pat_num}_{pat_num}$"),
                name, {"field": defn.output_fields[0],
                       "param_groups": {"1": "bins", "2": "lookback"},
                       "float_groups": set()}))
        elif naming == "psar":
            # 固定列名（参数不编码——decode 补注册表 default）
            rules.append((re.compile(rf"^{re.escape(defn.output_fields[0])}$"),
                name, {"field": defn.output_fields[0],
                       "param_groups": {}, "float_groups": set()}))
        elif naming == "pattern":
            for out, col in _PATTERN_COLUMNS.items():
                if INDICATOR_REGISTRY.get(out) is defn and defn.output_fields:
                    rules.append((re.compile(rf"^{re.escape(col)}$"), name,
                                  {"field": defn.output_fields[0],
                                   "param_groups": {}, "float_groups": set()}))
    return rules


def _decoders() -> List[Tuple[re.Pattern, str, Dict[str, Any]]]:
    global _DECODERS
    if _DECODERS is None:
        _DECODERS = _build_decoders()
    return _DECODERS


def decode_column_ref(col_name: str) -> Optional[Dict[str, Any]]:
    """编码列名 → 结构化引用（迁移脚本消费）；无匹配返回 None。

    未编码进列名的参数（psar 的 step/max_step）补注册表 default——
    列名未携带即生成时的默认值，语义正确且往返一致
    （encode(decode(x)) == x 由调用方锁）。

    Raises:
        ColumnRefError: 同一列名命中多个 (indicator, params, field)——
        歧义显式拒绝（进迁移阻塞清单，不猜测）。
    """
    hits: List[Dict[str, Any]] = []
    for pattern, indicator, spec in _decoders():
        m = pattern.match(col_name)
        if not m:
            continue
        defn = INDICATOR_REGISTRY[indicator]
        params: Dict[str, Any] = {}
        for gname, pkey in spec["param_groups"].items():
            raw = m.group(int(gname))
            params[pkey] = float(raw) if pkey in spec["float_groups"] else int(raw)
        for key, pdef in defn.params.items():      # 未编码参数补 default
            params.setdefault(key, pdef.default)
        field_name = (m.group(int(spec["field_group"]))
                      if "field_group" in spec else spec["field"])
        hits.append({"indicator": indicator, "params": params,
                     "field": field_name})

    if not hits:
        return None
    uniq = {str(sorted(h.items(), key=str)) for h in hits}
    if len(hits) > 1 and len(uniq) > 1:
        raise ColumnRefError(
            "AMBIGUOUS_DECODE",
            f"编码列名歧义（命中 {len(hits)} 个解析）: {col_name}",
            detail={"column": col_name, "hits": hits})
    return hits[0]


def try_decode_column_ref(col_name: str) -> Optional[Dict[str, Any]]:
    """编码列名宽容解码：无匹配或歧义均返回 None。

    「可解析才并入/现算」分派语义的统一入口（_mask_higher 的
    subject/target 高周期现算、case_inspection 的 target 放行）——
    歧义与无匹配同归不可解析（歧义列名属迁移阻塞清单事务，不猜测）。
    """
    try:
        return decode_column_ref(col_name)
    except ColumnRefError:
        return None


# ---------------------------------------------------------------- canonical

def canonicalize_ref(ref: Any, db_path: Optional[Path] = None) -> Any:
    """引用 canonical 化（resolve + ref_dict 的便捷组合）——登记与迁移
    共用（幂等连续：同函数无第二实现）。"""
    return resolve_column_ref(ref, db_path).ref_dict()


def _norm_number(val: Any) -> str:
    """数值归一（与 condition_identity._normalize_target 同语义：
    int/float 同值同形，整数值去浮点尾巴）。"""
    f = float(val)
    if f.is_integer():
        return f"n:{int(f)}"
    return f"n:{f!r}"


def canonical_ref(ref: Any) -> str:
    """引用的规范化编码——指纹（canonical_condition_hash subject 段）与
    迁移重算 conditions_hash 的同源基础。

    - 结构化指标：i:{indicator}|p:{k=v,...（参数键排序，数值归一）}|f:{field}
    - 字符串（基础列/插件列）：strip 后裸名直通——与现状指纹（str().strip()）
      逐字节连续（存量连续锚；结构化恒以 i: 开头无撞形）。
    """
    if isinstance(ref, dict):
        indicator = str(ref.get("indicator", ""))
        params = ref.get("params") or {}
        keys = sorted(params)
        param_part = ",".join(f"{k}={_norm_number(params[k])}" for k in keys)
        # field 缺省归一（与 resolve 同语义——单输出注入唯一列，保证
        # 「带/不带显式 field 的同语义引用」同 canonical，幂等不断裂）
        field_name = ref.get("field")
        if not field_name:
            defn = INDICATOR_REGISTRY.get(indicator)
            if defn is not None and len(defn.output_fields) == 1:
                field_name = defn.output_fields[0]
        return f"i:{indicator}|p:{param_part}|f:{field_name or ''}"
    return str(ref).strip()
