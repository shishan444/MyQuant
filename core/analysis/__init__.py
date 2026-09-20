"""研究编排域（core/analysis）：分析模板、假设→策略转化器、插件注册流程。

定位对标 core/services（跨切编排）；扇入 features/discovery/validation/strategy，
依赖 core/signals 求值内核（D3）。
护栏：任何 core 模块不得反向依赖 core/analysis。
"""
