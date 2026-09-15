# 验证报告 — G6 Settings 页迁移（表单密集页）

_时间：2026-09-15 | 分支：fix/myquant-truthfulness-batch1 | tag：FE-G6_

## N.6 冻结与实施

组件映射：ui/Button→Button、ui/input→Input/InputNumber（数值字段）、ui/label→Form.Item 语义、ui/switch→Switch、ui/separator→Divider、ui/skeleton→Skeleton、ui/select→Select、自研 tab bar→antd Tabs（items API）

表单 AntD Form 化（本页 3 区块独立 Form 实例，rules 唯一持有草稿——ui-state spec）：
- GeneralSettings：语言/时区/双通知 Switch（valuePropName="checked"）
- BinanceApiSettings：凭证 Input.Password
- TradingSettings：InputNumber+rules（init_cash>0、费率 0-100、max_positions≥1 整数）——校验失败保留草稿+错误就近展示（有测试断言）

类型：AppConfig ← /api/config（响应 dict 无 schema，G1 无 schema 清单新增项），手写注明来源

顺带核对与记录：
- 全量订阅 3 处之一（IndicatorSettings 的 useChartSettings()）——订阅形态记录在案，selector 化归 G17 终核
- S1 凭证回显风险（GET /api/config 明文回显 binance key）——代码注释+本报告双重记录，移交后端容器
- 可访问性：全部开关/数值输入/颜色选择补 aria-label；连接状态=指示点+文字（非纯颜色）

## 测试（新建 5 用例，userEvent）

四 tab 呈现/通用保存 PUT 链路/模拟交易负值校验（错误就近+不发 PUT+草稿保留）/指标 EMA 增删/Binance 空凭证测试提示

## 四件套

tsc 0 / build ✓ / vitest 29 文件 270 全绿（+5 新建）/ lint 77 errors（基线 78 内）/ knip 无新增（基线仅 G5 遗留收缩）
