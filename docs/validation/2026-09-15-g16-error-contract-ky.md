# G16 错误统一 + ky 迁移 Evidence

日期：2026-09-15 | 分支：fix/myquant-truthfulness-batch1 | tag：FE-G16

## 16.0 拦截器契约冻结（design.md G16 节已回写）

ApiError `{ kind, message, status? }` 五层判定（网络×幂等二分 / 422 / 409 / 4xx / 5xx），
全中文文案，5xx 不透 detail（内部细节红线），非幂等 mutation 附「结果未知」语义。
批量失败清单=业务响应体由页面渲染，拦截器只管 HTTP 层（边界冻结）。
**契约文本为后端容器 B6 对齐目标**（后端 detail 保持 string | [{loc,msg}] 即前端最优展示）。

## 16.1 实施

### services/api.ts 重写（axios → ky 2.1）

- `ky.create` 单实例：timeout 30s + afterResponse hook（非 2xx 读 body 统一转 ApiError）
- 泛型方法集 `api.get/post/put/delete<T>` 直返数据本体；RequestOptions（params→searchParams 空值过滤 / json / FormData body / per-request timeout）
- 204/空体显式判空（`.json()` 对空体会抛 SyntaxError）
- 相对路径统一解析为绝对 URL（jsdom 的 Request 构造限制；等价原 axios baseURL 语义）
- 网络层错误识别三形态：fetch TypeError / ky NetworkError / ky TimeoutError

### services 全量迁移（62 调用点）

7 文件（datasets/evolution/scene/strategies/trading/validation + Settings.tsx）：
`const { data } = await api.get(url, {params})` → `return api.get(url, {params})`；
post 裸 body → `{ json: body }`；FormData 三参 → `{ body: formData, timeout }`；
4 处 per-request timeout 保留（import 120s/300s、backtest 60s、verify 300s）。

**批量脚本两次事故与修复**（正则跨函数误吞 return、半截替换坏语法）——均已 git diff 核对修复；
教训已吸收：跨行正则替换后必须 diff 全文件核对。

### 测试

- 新建 `test/services/api.test.ts`（13 用例，mock 全局 fetch 测真实分层）：
  - 409 可理解：detail 透出（「已有运行中的交易任务」）/ 无 detail 默认文案
  - 网络与业务区分：GET 幂等文案 vs POST/DELETE「操作结果未知」文案
  - 500 通用：message 固定「服务暂不可用」且不含 Traceback
  - 422 数组展开（`字段路径: 消息` 分号拼接）/ 4xx detail 透出与状态码回退
  - 成功路径：200 直返 / 204 判空 / searchParams 序列化 / FormData multipart
- 既有 4 个 services 测试改写：mock 返回值 `{ data: X }` → `X`（axios response 形态退役）、
  5 处参数断言更新（`{ json: payload }` 形态）；既有用例无错误场景（全成功路径），错误场景为新建

## 验证

- axios 依赖移除（package.json/lock，src 零残留）
- 四件套：tsc 0 / vitest **34 文件 315 全绿**（302→315，+13 错误契约）/ lint 86 基线持平 / build ✓
- knip 无新增
- 浏览器真实冒烟（前后端真实服务）：Strategies 列表 200 行经 ky fetch 链路正常加载；
  后端 404 detail（"Strategy not found"）与 4xx client 层透出契约匹配；应用 console 零错误
  （探测请求自身的 4xx 资源记录除外）
