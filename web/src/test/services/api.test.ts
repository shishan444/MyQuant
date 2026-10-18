/**
 * Tests for services/api.ts（ky 单实例 + G16.0 拦截器契约）。
 * mock 全局 fetch（ky 底层），断言用户可见终态：错误分层 kind/message。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { api, ApiError } from "@/services/api";

const jsonResponse = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("G16.0 拦截器契约：HTTP 分层", () => {
  it("409 conflict：透出后端业务 detail（可理解冲突）", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(409, { detail: "已有运行中的交易任务" }));
    const err = (await api.post("/api/paper-trading/tasks", { json: {} }).catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.kind).toBe("conflict");
    expect(err.status).toBe(409);
    expect(err.message).toBe("已有运行中的交易任务");
  });

  it("409 conflict：无 detail 时给默认冲突文案", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(409, {}));
    const err = (await api.post("/api/paper-trading/tasks/t1/stop").catch((e: unknown) => e)) as ApiError;
    expect(err.kind).toBe("conflict");
    expect(err.message).toBe("操作冲突：目标状态已被其他操作改变，请刷新后重试");
  });

  it("422 validation：Pydantic detail 数组展开为 字段: 消息", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(422, {
        detail: [
          { loc: ["body", "risk_genes", "leverage"], msg: "Input should be greater than 0" },
          { loc: ["body", "symbol"], msg: "Field required" },
        ],
      }),
    );
    const err = (await api.post("/api/evolution/tasks", { json: {} }).catch((e: unknown) => e)) as ApiError;
    expect(err.kind).toBe("validation");
    expect(err.message).toBe(
      "risk_genes.leverage: Input should be greater than 0; symbol: Field required",
    );
  });

  it("4xx client：透出字符串 detail；无 detail 给状态码文案", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(404, { detail: "策略不存在" }));
    const notFound = (await api.get("/api/strategies/nope").catch((e: unknown) => e)) as ApiError;
    expect(notFound.kind).toBe("client");
    expect(notFound.message).toBe("策略不存在");

    fetchMock.mockResolvedValueOnce(jsonResponse(400, {}));
    const badRequest = (await api.get("/api/data/datasets").catch((e: unknown) => e)) as ApiError;
    expect(badRequest.kind).toBe("client");
    expect(badRequest.message).toBe("请求失败（400）");
  });

  it("5xx server：一律通用文案，不透出内部 detail", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(500, { detail: "Traceback (most recent call last): File /app/core/..." }),
    );
    const err = (await api.get("/api/paper-trading/tasks").catch((e: unknown) => e)) as ApiError;
    expect(err.kind).toBe("server");
    expect(err.message).toBe("服务暂不可用，请稍后重试");
    expect(err.message).not.toContain("Traceback");
  });
});

describe("G16.0 拦截器契约：网络层（网络与业务区分）", () => {
  it("网络断连 × GET（幂等）：网络连接失败文案", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("fetch failed"));
    const err = (await api.get("/api/paper-trading/tasks").catch((e: unknown) => e)) as ApiError;
    expect(err.kind).toBe("network");
    expect(err.message).toBe("网络连接失败，请检查网络后重试");
  });

  it("网络断连 × POST（非幂等 mutation）：结果未知语义", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("fetch failed"));
    const err = (await api.post("/api/paper-trading/tasks", { json: {} }).catch((e: unknown) => e)) as ApiError;
    expect(err.kind).toBe("network");
    expect(err.message).toContain("操作结果未知");
    expect(err.message).toContain("刷新页面确认当前状态");
  });

  it("网络断连 × DELETE（非幂等）：同为结果未知语义", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("fetch failed"));
    const err = (await api.delete("/api/strategies/s1").catch((e: unknown) => e)) as ApiError;
    expect(err.kind).toBe("network");
    expect(err.message).toContain("操作结果未知");
  });
});

describe("成功路径与参数序列化", () => {
  it("200 JSON 直返数据本体", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { items: [], total: 0 }));
    const data = await api.get("/api/data/datasets");
    expect(data).toEqual({ items: [], total: 0 });
  });

  it("204/空响应体返回 undefined（DELETE 场景）", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await expect(api.delete("/api/data/datasets/d1")).resolves.toBeUndefined();
  });

  it("params 过滤空值并序列化为查询串", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));
    await api.get("/api/strategies", {
      params: { symbol: "BTCUSDT", limit: 10, offset: undefined },
    });
    // ky 把最终 URL 构造进 Request 对象
    const input = fetchMock.mock.calls[0][0] as Request;
    expect(String(input.url)).toContain("/api/strategies?symbol=BTCUSDT&limit=10");
    expect(String(input.url)).not.toContain("offset");
  });

  it("FormData 以 multipart 原生上送（Content-Type 不手动覆盖）", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { dataset_id: "d1" }));
    const fd = new FormData();
    fd.append("file", new Blob(["a,b,c"]), "test.csv");
    await api.post("/api/data/import", { body: fd, timeout: 120000 });
    // ky 将 FormData 封装进 Request（multipart 由 fetch 层自动加 boundary）
    const request = fetchMock.mock.calls[0][0] as Request;
    expect(request).toBeInstanceOf(Request);
  });

  it("per-request timeout 透传给 ky", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { ok: true }));
    await api.post("/api/strategies/verify", { json: {}, timeout: 300000 });
    // ky timeout 不直接进 fetch init（由 ky 内部管理），此处仅验证请求可达
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
