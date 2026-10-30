旧域 data/market 清理清单（2026-10-29T14:07:21.977086）
（迁移四文件已先经 cp -p 校验迁入 data/market_fapi——迁移校验记录见文末；以下为删除面）
BTCUSDT_15m.parquet: rows=95255
BTCUSDT_1d.parquet: rows=993
BTCUSDT_1h.parquet: rows=23814
BTCUSDT_1m.parquet: rows=1429920
BTCUSDT_4h.parquet: rows=5954
BTCUSDT_5m.parquet: rows=285984
BTCUSDT_funding.parquet: rows=3099
BTCUSDT_oi_15m.parquet: rows=98935
BTCUSDT_oi_1d.parquet: rows=1031
BTCUSDT_oi_1h.parquet: rows=24734
BTCUSDT_oi_30m.parquet: rows=49468
BTCUSDT_oi_3d.parquet: rows=344
BTCUSDT_oi_4h.parquet: rows=6184
BTCUSDT_oi_5m.parquet: rows=296798
ETHUSDT_15m.parquet: rows=105216
ETHUSDT_1d.parquet: rows=1096
ETHUSDT_1h.parquet: rows=26304
ETHUSDT_1m.parquet: rows=1429920
ETHUSDT_4h.parquet: rows=6576
ETHUSDT_5m.parquet: rows=285984
ETHUSDT_funding.parquet: rows=3099
ETHUSDT_oi_15m.parquet: rows=98935
ETHUSDT_oi_1d.parquet: rows=1031
ETHUSDT_oi_1h.parquet: rows=24734
ETHUSDT_oi_30m.parquet: rows=49468
ETHUSDT_oi_3d.parquet: rows=344
ETHUSDT_oi_4h.parquet: rows=6184
ETHUSDT_oi_5m.parquet: rows=296798
非 parquet 项: ['__init__.py']

## 迁移校验记录（VS-3.9 复验收尾，tester3 2026-10-29 补档）

四文件迁移终态独立复验（migrate_frozen_timeframes.py 执行时刻的校验输出
未落档——本段为事后对 data/market_fapi 终态的复验收尾记录；行数与设计卡
快照〔47628/319/52608/365〕逐件一致，mtime 均保持迁移前原值〔2026-09-21〕
——cp -p 冻结不刷新语义生效；源已删故首尾戳与 mtime 以迁入件为终态锚）：

- BTCUSDT_30m.parquet: rows=47628 head=2024-01-01 00:00:00+00:00 tail=2026-09-19 05:30:00+00:00 mtime=2026-09-21T01:32:26
- BTCUSDT_3d.parquet: rows=319 head=2024-01-01 00:00:00+00:00 tail=2026-09-17 00:00:00+00:00 mtime=2026-09-21T01:32:38
- ETHUSDT_30m.parquet: rows=52608 head=2023-09-19 06:00:00+00:00 tail=2026-09-19 05:30:00+00:00 mtime=2026-09-21T01:33:27
- ETHUSDT_3d.parquet: rows=365 head=2023-09-21 00:00:00+00:00 tail=2026-09-17 00:00:00+00:00 mtime=2026-09-21T01:34:08

中止路径（任一步校验不过源文件不动）由脚本内置 AssertionError 回滚逻辑
承载（works/历史脚本/migrate_frozen_timeframes.py:73-77），代码面已核。