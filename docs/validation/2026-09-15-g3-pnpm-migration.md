# 验证报告 — G3 pnpm 迁移（refactor-myquant-frontend-f1-f7 阶段 A）

_时间：2026-09-15 | 分支：fix/myquant-truthfulness-batch1 | tag：FE-G3_

## 迁移过程

1. `pnpm import`（pnpm 11.18.0）从 package-lock.json 生成 pnpm-lock.yaml（import 自动删除旧 lock，基准从 git HEAD 取回对照）
2. `rm -rf node_modules && pnpm install`（26.4s 重装）

## 依赖零漂移对照（lock 全集 diff）

- 唯一包名集合：npm lock 519 = pnpm lock 519，**零缺失、零多余**
- 版本集差异仅 1 处：`brace-expansion` npm 树 2.1.7 vs pnpm 树 2.1.4（patch 级传递依赖，两包管理器对嵌套 range 的解析选择差异；minimatch/glob 用的正则展开工具库，功能等价）——判定可接受
- 转换注意：openapi-typescript 依赖的 `--legacy-peer-deps` 安装语义在 pnpm 下不再需要（peer 冲突默认 warning）

## 调用点更新（npm → pnpm，共 5 处）

- scripts/dev.sh：npm install→pnpm install、npx vite→pnpm exec vite
- scripts/start.sh：同上
- start.sh（根）：npx vite→pnpm exec vite
- docs/MAP.md：包管理表述
- docs/AI_CODING_GUIDE.md：lint/test/build 命令示例
- 残留检查：全仓 shell 脚本 npm/npx 调用零残留

## 四件套（pnpm 生态）

| 项 | 结果 |
|---|---|
| pnpm exec tsc -b | 0 |
| pnpm run build | ✓（343ms） |
| pnpm exec vitest run | 27 文件 258 全绿 |
| pnpm run lint | 78 errors（=F0 基线零新增） |
| pnpm exec knip | 与 G1 基线唯一名零差异 |
