"""RR-013 诊断复跑：OI 拥挤单条件做空（OI_ZScore(20)>2.0 @4h, 48bar）。

问题：RR-013 档案记载 param_sweep 判 unsupported 并归因「做多语义判据
（GAP-01）」。代码考古（包四 GAP-01 审查重定义）证明效应锚方向自适应
（center_dir 由数据定，邻域同向即 effective）。本脚本用当前代码复跑
同条件，取 points 明细归因。
"""
import sys, json
sys.path.insert(0, "/Users/ss/works/git/aicode/everycode/MyQuant")
import pandas as pd
from pathlib import Path
from core.analysis.warmup_loader import load_with_warmup
from core.analysis.templates.param_sweep import param_sweep
from core.features.indicators import _DEFAULT_PARAMS, _compute_indicator

DATA = Path("/Users/ss/works/git/aicode/everycode/MyQuant/data/market")
# 默认参数矩阵输出列名（OI_ZScore period=20）
probe = _compute_indicator(pd.DataFrame(
    {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0,
     "open_interest": 1.0},
    index=pd.DatetimeIndex([pd.Timestamp("2024-01-01", tz="UTC")] * 3)),
    "OI_ZScore", dict(_DEFAULT_PARAMS["OI_ZScore"][0]))
cols = list(probe.keys())
print("OI_ZScore 输出列:", cols)
subject = cols[0]

# 统计段：RR-013 档案「统计段后半 2025Q4~2026Q1 为大熊」→ 复跑取
# 2025-01-01~2026-06-30（覆盖档案口径）
df, req_start = load_with_warmup(
    DATA, "BTCUSDT", "4h", "2025-01-01", "2026-06-30",
    ["OI_ZScore"])
res = param_sweep(
    df, indicator="OI_ZScore",
    condition={"subject": subject, "action": "gt", "target": 2.0},
    sweep={"axis": "target", "values": "auto"},
    forward_bars=48, timeframe="4h", request_start=req_start,
    data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                  "start": "2025-01-01", "end": "2026-06-30"}])

payload = res.payload if hasattr(res, "payload") else res
d = payload if isinstance(payload, dict) else json.loads(json.dumps(payload))
print("verdict:", d["verdict"])
print("baseline_mean:", d["points"][0]["baseline_mean"])
for p in d["points"]:
    print(f"  v={p['value']:.3f} n={p['samples']:4d} mean={p['forward_return_mean']} "
          f"p={p['p_value']} q={p['q_value']} dir={p['effect_direction']} "
          f"state={p['state']}")
center = next(p for p in d["points"] if abs(p["value"] - 2.0) < 1e-9)
print("中心点(2.0):", {k: center[k] for k in
                       ("samples", "forward_return_mean", "q_value",
                        "effect_direction", "state")})
