/**
 * T5 前端侧对账测试（fix-replay-gap-alignment——endpoint-parity-audit
 * 能力锚的前端镜像，与后端 tests/api/test_endpoint_parity.py 互为独立
 * 证据；npm test 全量绿隐含「前端调用面 ⊆ 路由面」）。
 *
 * - T5-a：src 全部 API 调用点（api.* 形态）
 *   ⊆ web/openapi.json 路由面（再生成权威基准源）；模板参数段归一
 *   （含 ${...} 与 encodeURIComponent(...) 形制）。
 * - T5-WS：前端 WS channel 字面量集 ⊆ 后端 socket.io @sio.on 注册面
 *   （api/realtime.py 直读——注册不走 FastAPI 路由表，路由面天然
 *   不含，独立对账档）。
 *
 * 挂载时点（F0）预期红：调用面差集=25 对象死调用（26−1：union-
 * balance 经后端薄壳恢复变活调用）+evolution channel（两端皆死）。
 * U1-U10 清理完成后差集清零转绿（F6）；sse.ts 随 U8 批次删除后
 * streamSse 特判提取规则已同步移除（零死规则残留）。
 */
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const SRC_ROOT = join(__dirname, "../..");
const OPENAPI_PATH = join(__dirname, "../../../openapi.json");
const REALTIME_SERVER_PATH = join(
  __dirname,
  "../../../../api/realtime.py",
);

/** 路径模板段归一：${expr} 与 {name} 两形制 → {} */
function normPath(p: string): string {
  return p.replace(/\$\{[^}]*\}/g, "{}").replace(/\{[^}]*\}/g, "{}");
}

interface OpenapiSpec {
  paths: Record<string, Record<string, unknown>>;
}

function routeSurface(): Set<string> {
  const spec: OpenapiSpec = JSON.parse(readFileSync(OPENAPI_PATH, "utf-8"));
  const out = new Set<string>();
  for (const [p, methods] of Object.entries(spec.paths)) {
    for (const m of ["get", "post", "put", "patch", "delete"]) {
      if (m in methods) out.add(`${m.toUpperCase()} ${normPath(p)}`);
    }
  }
  return out;
}

/** 递归收集 src 下 ts/tsx 文件（排除 test 域/generated/fixtures） */
function collectSourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    const rel = relative(SRC_ROOT, full);
    if (
      rel.startsWith("test/") ||
      rel.includes("/test/") ||
      rel.includes("__fixtures__") ||
      rel.startsWith("types/generated") ||
      rel.includes("/generated/")
    ) {
      continue;
    }
    if (statSync(full).isDirectory()) {
      out.push(...collectSourceFiles(full));
      continue;
    }
    if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) {
      out.push(full);
    }
  }
  return out;
}

const CALL_RE =
  /(?:api|apiFetch)\.(get|post|put|patch|delete)(?:<[^<>]*>)?\(\s*([`"'])(\/api\/[^`"']*)\2/g;

function frontendSurface(): Set<string> {
  const out = new Set<string>();
  for (const f of collectSourceFiles(SRC_ROOT)) {
    const text = readFileSync(f, "utf-8");
    for (const match of text.matchAll(CALL_RE)) {
      out.add(`${match[1].toUpperCase()} ${normPath(match[3])}`);
    }
  }
  return out;
}

describe("T5-a 前端调用面 ⊆ 路由面", () => {
  it("api.* 的每条调用都有后端路由在", () => {
    const routes = routeSurface();
    const front = frontendSurface();
    expect(front.size).toBeGreaterThan(0);
    const dead = [...front].filter((k) => !routes.has(k)).sort();
    expect(
      dead,
      `前端死调用 ${dead.length} 条（U1-U10 清理面未尽或新引入）`,
    ).toEqual([]);
  });
});

describe("T5-WS 前端 channel 字面量集 ⊆ 后端注册面", () => {
  it("createRealtimeConnection 的 channel 实参都对应 @sio.on 注册", () => {
    const server = readFileSync(REALTIME_SERVER_PATH, "utf-8");
    const registered = new Set<string>();
    for (const m of server.matchAll(/@sio\.on\([^)]*namespace="\/([^"]+)"/g)) {
      registered.add(m[1]);
    }
    // 前端 channel 消费点：channel: "xxx" 实参（生产代码域）
    const consumed = new Set<string>();
    for (const f of collectSourceFiles(SRC_ROOT)) {
      const text = readFileSync(f, "utf-8");
      for (const m of text.matchAll(
        /createRealtimeConnection\(\s*\{\s*channel:\s*"([^"]+)"/g,
      )) {
        consumed.add(m[1]);
      }
    }
    expect(consumed.size).toBeGreaterThan(0);
    const unregistered = [...consumed].filter((c) => !registered.has(c));
    expect(
      unregistered,
      `前端 WS channel 无后端注册（两端皆死通道）: ${unregistered}`,
    ).toEqual([]);
  });
});
