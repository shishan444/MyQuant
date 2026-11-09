"""浪清单盘点模板（add-evaluation-ruler）。

语义：对给定区间以收盘价 zigzag 规则产出已确认浪的底账清单——
回答「这段行情里 ≥threshold 幅度的波段有几段、各多长」。浪=相邻
两确认 pivot 的腿；pivot 确认依赖后向信息（反向运动达门槛才回认
上一转折，确认发生在未来 bar），与条件列「当根只看已发生数据」的
防前视前提数学上不相容——因此 zigzag 不进 INDICATOR_REGISTRY、
只落本盘点模板与报告对照层（design D1）。

zigzag 五规则（与 demo 参考实现 r9_d1_ruler.py 同构，数值形制逐位
对齐）：首段方向未定期极值候选双向更新（新高新低都推进候选）；
dir_=±1 时反向运动达门槛回认上一极值为 pivot（确认含等号）；状态
机转折确认用乘/除比较（无容差）；浪清单的腿幅过滤带浮点容差
threshold−1e-9（状态机已保证满足，防御浮点表示误差）；末端未确认
pivot 不入清单。

跨查询可比性披露：zigzag 有起点效应——不同 start 装载的清单不同
（起点前的走势决定首段方向与首 pivot），不同 start 的清单不可直接
混用拼接；同参数同数据重复调用逐位一致（幂等复算锚）。

回望性契约：浪清单是行情结构事实——不适用假设关联（路由分派层
统一拦 hypothesis_id）、不计入判定并集、不作策略条件或实盘信号
输入。
"""
from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from core.analysis.hypothesis_service import ResearchError
from core.analysis.result import AnalysisResult, build_result
from core.analysis.templates.param_guard import (
    _require_float_range, _require_int_range)
from core.analysis.warmup_loader import to_utc

# 浪清单默认门槛（demo 冻结值：4h 轴 5% 波段）
DEFAULT_THRESHOLD = 0.05
# 默认前扩 bar 数（起点效应缓冲——前扩段只用不算）
DEFAULT_WARMUP_BARS = 500
# 腿幅过滤浮点容差（与 demo 同值：状态机确认后的防御性再过滤）
_AMP_TOLERANCE = 1e-9


def validate_wave_inventory_params(
    threshold: float, warmup_bars: int
) -> None:
    """参数前置校验（路由装载前调用——warmup_bars 参与前扩起点计算，
    负值须在装载前拒绝而非装载后）。

    委托 param_guard 单源（bool 排除＋字符串数字拒——原 float() 静默
    转换会让 'abc' 裸 ValueError 落 500 与 '0.5' 静默放行并存）。"""
    _require_float_range(
        threshold, name="threshold", lo=0, hi=1,
        lo_excl=True, hi_excl=True)
    _require_int_range(warmup_bars, name="warmup_bars", lo=0)


def compute_zigzag_waves(
    close: pd.Series, threshold: float = DEFAULT_THRESHOLD
) -> List[Dict[str, Any]]:
    """zigzag 状态机 → 已确认浪清单（纯计算核心，模板与对照共用）。

    Args:
        close: 收盘价序列（DatetimeIndex；在全装载 df 上调用——
            zigzag 有起点效应，起点越早首段方向判定越稳定）。
        threshold: 转折确认门槛（相邻 pivot 反向运动幅度，小数形制）。

    Returns:
        浪字典列表（时间序），每条含 start/end（pd.Timestamp UTC）、
        start_price/end_price（pivot 价）、direction（long/short）、
        amp（幅度绝对值，小数形制）、dur（bar 数）。
    """
    c = close.values
    idx = close.index
    n = len(c)
    piv: List[tuple] = []  # 已确认 pivot (位置, 价格)——确认发生在未来 bar
    dir_ = 0  # 0=首段方向未定；1=上行态；-1=下行态
    ext_i, ext_p = 0, float(c[0])
    for i in range(1, n):
        p = float(c[i])
        if dir_ == 0:
            # 方向未定：达门槛即确认极值候选为 pivot 并定方向；
            # 未达门槛期间极值候选随新高新低双向更新
            if p >= ext_p * (1 + threshold):
                piv.append((ext_i, ext_p))
                dir_, ext_i, ext_p = 1, i, p
            elif p <= ext_p * (1 - threshold):
                piv.append((ext_i, ext_p))
                dir_, ext_i, ext_p = -1, i, p
            else:
                if p > ext_p:
                    ext_i, ext_p = i, p
                elif p < ext_p:
                    ext_i, ext_p = i, p
        elif dir_ == 1:
            if p > ext_p:
                ext_i, ext_p = i, p
            elif ext_p / p - 1 >= threshold:  # 回落达门槛：回认峰为 pivot
                piv.append((ext_i, ext_p))
                dir_, ext_i, ext_p = -1, i, p
        else:
            if p < ext_p:
                ext_i, ext_p = i, p
            elif p / ext_p - 1 >= threshold:  # 反弹达门槛：回认谷为 pivot
                piv.append((ext_i, ext_p))
                dir_, ext_i, ext_p = 1, i, p
    # 末端未确认 pivot（当前 ext 候选）不入清单——piv 仅含已确认转折
    waves: List[Dict[str, Any]] = []
    for (i0, p0), (i1, p1) in zip(piv[:-1], piv[1:]):
        amp = p1 / p0 - 1
        if abs(amp) >= threshold - _AMP_TOLERANCE:
            waves.append({
                "start": idx[i0], "end": idx[i1],
                "start_price": p0, "end_price": p1,
                "direction": "long" if amp > 0 else "short",
                "amp": abs(amp), "dur": int(i1 - i0),
            })
    return waves


def wave_inventory(
    df: pd.DataFrame,
    *,
    data_ranges: list[dict[str, str]],
    request_start: str | pd.Timestamp | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    warmup_bars: int = DEFAULT_WARMUP_BARS,
) -> AnalysisResult:
    """浪清单盘点（纯计算模板——装载由路由层完成）。

    Args:
        df: 含 close 列的 DataFrame（DatetimeIndex UTC；路由路径下为
            请求区间前扩 warmup_bars 的装载结果）。
        data_ranges: 请求区间（半开声明；end 必填——无开放右端形制）。
        request_start: 请求区间起点（UTC 归一）。None 时统计段=全 df
            （直调向后兼容）；非 None 时输出只含起点落在请求区间内的
            已确认浪，前扩窗内浪计入 excluded_boundary_waves。
        threshold: 转折确认门槛，默认 0.05。
        warmup_bars: 请求的前扩 bar 数（披露对照用；实际前扩由装载
            层执行），默认 500。

    Raises:
        ResearchError: threshold/warmup_bars 非法（RESEARCH_PARAM_
            INVALID）；data_range 缺 end（无开放右端）；有效 bar < 2；
            区间零浪（走 build_result min_samples=1 既有拒绝路径——
            提示扩区间，不产空清单伪报告）。
    """
    validate_wave_inventory_params(threshold, warmup_bars)
    for r in data_ranges:
        if not isinstance(r, dict) or not r.get("end"):
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                "data_range 缺 end——浪清单盘点无开放右端形制，"
                "右端必须为显式时间戳",
                detail={"param": "data_range.end",
                        "legal_form": "显式 UTC 时间戳（如 2026-11-07）"})
    if len(df) < 2:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"有效 bar 数 {len(df)} < 2——不足以判定任何转折",
            detail={"param": "data_range", "actual_bars": len(df),
                    "legal_range": ">=2"})

    req_ts = (to_utc(pd.Timestamp(request_start))
              if request_start is not None else None)

    waves = compute_zigzag_waves(df["close"], threshold=float(threshold))
    in_range = [w for w in waves
                if req_ts is None or w["start"] >= req_ts]
    excluded_boundary = len(waves) - len(in_range)
    actual_warmup_bars = (int((df.index < req_ts).sum())
                          if req_ts is not None else 0)
    boundary_effect = {
        "warmup_data_sufficient": actual_warmup_bars >= warmup_bars,
        "excluded_boundary_waves": int(excluded_boundary),
        "warmup_bars_requested": int(warmup_bars),
        "warmup_bars_actual": actual_warmup_bars,
    }

    amps = [w["amp"] for w in in_range]
    durs = [w["dur"] for w in in_range]
    payload = {
        "threshold": float(threshold),
        "waves": [{
            "start": w["start"].isoformat(),
            "end": w["end"].isoformat(),
            "direction": w["direction"],
            "amp": float(w["amp"]),
            "dur": w["dur"],
        } for w in in_range],
        "summary": {
            "n_waves": len(in_range),
            "n_long": sum(1 for w in in_range if w["direction"] == "long"),
            "n_short": sum(1 for w in in_range if w["direction"] == "short"),
            "amp_median": (float(pd.Series(amps).median())
                           if amps else None),
            "amp_mean": (float(sum(amps) / len(amps)) if amps else None),
            "amp_max": (float(max(amps)) if amps else None),
            "dur_median": (float(pd.Series(durs).median())
                           if durs else None),
        },
        "boundary_effect": boundary_effect,
        # 跨查询可比性披露（spec 披露义务）：起点效应——不同 start
        # 的清单不可直接混用拼接
        "comparability_note": (
            "zigzag 有起点效应：不同 start 装载的浪清单不同，"
            "跨查询清单不可直接混用拼接；同参数同数据幂等"),
    }

    return build_result(
        "wave_inventory",
        sample_size=len(in_range),
        confidence={
            "method": "zigzag_close_threshold",
            # 确定性规则算法无抽样误差区间（interval 键位保留 null 语义：
            # 三要素构造器的结构要求，非缺失）
            "interval": None,
            "threshold": float(threshold),
            "deterministic": True,
        },
        data_ranges=data_ranges,
        payload=payload,
        min_samples=1,
    )
