# quant.db VACUUM 运维规程

## 背景

quant.db 的删除类策略（历史清理/退役分拣）持续产生空闲页（freelist），
且 WAL 模式下 checkpoint 不及时会使 -wal 文件堆积。本规程用
`scripts/vacuum_quant_db.py`（常驻运维件，幂等可重跑）在停服窗口
物理回收空间。

## 触发条件

- 大批删减后（策略退役/污染清理/归档分拣）
- 健康面发现 `PRAGMA freelist_count` 显著非零或主库体积远超真实
  数据量级
- 例行（季度级）

## 规程（五步）

1. **停服**：`scripts/stop.sh`（API/MCP/Web 三进程全停——脚本前置
   自检会以 BEGIN IMMEDIATE 探测占用，未停服执行将退出码 2 拒绝）。
2. **快照临时件**（执行前 cp、核验后删）：停服库正常关闭后 wal
   已随连接关闭合并，`cp data/quant.db /tmp/quant.db.vacuum-snap`
   后执行第 3 步；第 4 步核验通过即删除临时件，不留常驻副本
   （Q-2 裁决后 data/ 不再保留备份库实体，且 quant.db 在
   .gitignore——git 从不跟踪它，本临时快照是执行窗口内唯一的
   恢复通道；VACUUM 是原地重建，虽然 SQLite 保证原子性，重大
   操作前保留恢复通道）。
3. **执行**：`./venv/bin/python scripts/vacuum_quant_db.py`（缺省
   data/quant.db；可 `--db PATH` 指定）。输出前后体积与
   page_count/freelist_count 对比。
4. **体积核验**：对比输出中主库/wal 前后体积（`du data/quant.db*`
   复核）——freelist 应显著归零、wal 合并消失。
5. **起服**：`scripts/start.sh`，抽查 runner-status 正常。

## 时点约束

深夜低峰窗口执行（数据板块整改裁决 D）——停服期间实时面（WS 流/
守护器/纸盘调度）全部暂停，K 线缺口由重启后的建库对账+守护器自然
补齐（分钟级收敛）。

## 后续触发

删除策略持续产生空闲页属长期运维——每次大批删减后按本规程复跑
（脚本幂等）。
