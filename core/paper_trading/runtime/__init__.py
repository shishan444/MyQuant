"""运行编排（第 2 层·rebuild-paper-trading 组 3）。

四件分工（design §5）：
- scheduler.py——任务调度器（五态状态机+多任务并发+心跳移交）
- feed.py——品种数据作业（per-(symbol,timeframe) 共享单例）
- executor.py——任务执行器（八步 bar 处理序+单 bar 事务落库）
- recovery.py——恢复器（启动扫描+恢复校验+可信续跑）
"""
