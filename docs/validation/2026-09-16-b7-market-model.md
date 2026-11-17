# B7 回测域固化+市场模型层 — evidence

容器：`refactor-myquant-backend-b1-b11` · 组 B7 · tag BE7 · 2026-09-16
验证：后端全量 pytest **2168 passed**；MODIFIED backtest-liquidation-detection delta spec 落档（openspec validate 通过）。

## 10.0 口径卡与 10.1 市场模型层

**架构约束发现（B7.0 冻结后实施中修订）**：engine 为「无杠杆 vbt 跑 + 事后逐交易块放大」——订单层权益是无杠杆曲线，与杠杆名义不匹配，「基准改仓位名义」无法直接落地（订单层判不出杠杆级亏损）。**修订路径**：维持 batch1 等效换算形态 `init_cash×(1−(1−mmr)/L)`，固定 0.9 近似替换为币安 USDT-M 分层档位精确值——单一权威源架构与逐 bar/事后一致性保持。

- `core/market_model/`：BinanceMmrTiers 档位表（0.4%–10%，唯一字面量源）+ maintenance_margin_rate（档位选择）+ maintenance_threshold（等效清算线）+ tier_arrays（njit 内联友好平铺）
- engine 两消费点接源：逐 bar（order_func_nb 内联选档，order_args 注入数组）+ 事后检测（`_mm_threshold` 直接引用）；旧 MAINTENANCE_MARGIN_FRACTION/maintenance_threshold 删除（单一定义纪律）

## 影响面量化（新旧阈值网格，init_cash=100k）

| L | 等效名义 | mmr | 旧线(0.9/L) | 新线 | Δ | 价差旧→新 |
|---|---|---|---|---|---|---|
| 2 | 200k | 0.5% | 55,000 | 50,250 | −4,750 | 45.0%→49.8% |
| 3 | 300k | 1.0% | 70,000 | 67,000 | −3,000 | 30.0%→33.0% |
| 5 | 500k | 1.0% | 82,000 | 80,200 | −1,800 | 18.0%→19.8% |
| 10 | 1M | 1.0% | 91,000 | 90,100 | −900 | 9.0%→9.9% |
| 20 | 2M | 2.5% | 95,500 | 95,125 | −375 | 4.5%→4.9% |

方向：**一致更宽松**（清算更晚触发，价差扩大 (1−mmr)−0.9 个百分点/L）——进化评分连续性影响=清算惩罚略降，无迁移补偿（口径修正）。

## 守护测试重锚定（不变量保持）

- test_liquidation_threshold.py：档位边界/公式/单一定义/两消费点同源/njit-Python 同构（5 用例，数值按新口径）
- test_invariant_backtest.py 权益非负性容差：−5000→−13000（穿深=线差×杠杆机制吻合：L=10 实测 −12044 vs 旧 −3443，差 ≈900×10）——清算触发后曲线平坦，负值属 bar 内穿透容差（不变量语义「不为大额负值」保持）
- test_liquidation_edge_cases 全绿（含清算后续投场景）

## position_size/funding/费用滑口（B7.0 裁决文档化不动）

position_size=「vbt size_pct=当前权益百分比，杠杆 PnL 由事后放大承载」语义自洽；funding（0.1%/8h、borrowed_ratio、持仓期 position_values 基准）已合理——两者经口径卡显式文档化，不改数值。
