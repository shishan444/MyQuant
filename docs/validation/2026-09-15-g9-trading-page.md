# 验证报告 — G9 Trading 页迁移（高交互页）

_时间：2026-09-15 | 分支：fix/myquant-truthfulness-batch1 | tag：FE-G9_

## 实施内容

Trading.tsx（802 行）+ CreateTaskDialog（167 行）：

- Badge×3→Tag（状态/持仓/runner 徽标语义色映射 success/error/warning/default）
- AlertDialog×2（删除/重启确认）→ App.useApp().modal.confirm 命令式（确认交互单源；target state 随之删除——弹层由 openDeleteConfirm/openRestartConfirm 直接构造文案）
- 交易明细表：ui/table+TradeRow→antd Table 声明式 TRADE_COLUMNS（六列 render 逻辑原样移植，自管分页「查看全部」保留）
- CreateTaskDialog：AlertDialog+散 useState 表单→Modal+AntD Form（rules 校验初始资金≥100；杠杆/方向选项按钮 Form.Item 受控化；置信度开关 Switch）；外部默认值经 useEffect+setFieldsValue 同步
- 搜索原生 input→AntD Input（prefix+allowClear）；图标操作按钮补 aria-label
- 图表组件对接：TradingChart/EquityCurve 保持消费（G14 图表批统一处理，本批无先后依赖）

## 测试

既有 Trading 组件/hook 测试全绿（277）——断言语义保持实证（hook 层与数据链零变化）；本页新增覆盖留给 G17 L3 冒烟（高交互页 e2e 比单元测试更贴近行为等价线）

## 四件套

tsc 0 / build ✓ / vitest 31 文件 277 全绿 / lint 77 errors（基线内）/ knip 无新增
