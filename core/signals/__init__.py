"""信号求值内核（core/signals）：纯计算、无研究语义、无 HTTP 概念。

承载：受限公式语言求值器、模型 fit/predict 管道、信号注册表读取。
供 core/analysis 与 core/strategy/backtest 用例共同依赖（D3）；
护栏：本模块不得依赖 core/analysis（防循环）。
"""
