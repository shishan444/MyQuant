/**
 * HTTP 客户端单实例（ky）+ 统一错误契约。
 *
 * 拦截器契约见 design.md G16.0：网络/422/409/4xx/5xx 分层判定，
 * 全部转换为 ApiError（中文化 message + kind），不透出内部细节。
 * SSE（realtime/sse）与 WS 不经本层。
 */
import ky from "ky";

export type ApiErrorKind =
  | "network"
  | "validation"
  | "conflict"
  | "client"
  | "server";

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status?: number;

  constructor(kind: ApiErrorKind, message: string, status?: number) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
  }
}

/** Pydantic 422 detail 数组展开：`字段路径: 消息` 分号拼接 */
function formatValidationDetail(detail: unknown): string {
  if (Array.isArray(detail)) {
    const parts = detail
      .map((e: { loc?: unknown[]; msg?: unknown }) => {
        const field = Array.isArray(e.loc) ? e.loc.slice(1).join(".") : "";
        const msg = typeof e.msg === "string" ? e.msg : "";
        return field ? `${field}: ${msg}` : msg;
      })
      .filter(Boolean);
    if (parts.length > 0) return parts.join("; ");
    return "请求参数有误";
  }
  if (typeof detail === "string" && detail) return detail;
  return "";
}

/** HTTP 状态错误 → ApiError（G16.0 分层契约） */
function toHttpApiError(status: number, body: unknown): ApiError {
  const detail = (body as { detail?: unknown } | null)?.detail;
  const detailText = formatValidationDetail(detail);

  if (status === 422) {
    return new ApiError("validation", detailText || "请求参数有误", status);
  }
  if (status === 409) {
    return new ApiError(
      "conflict",
      detailText || "操作冲突：目标状态已被其他操作改变，请刷新后重试",
      status,
    );
  }
  if (status >= 500) {
    // 5xx 不透出 detail（内部细节红线）
    return new ApiError("server", "服务暂不可用，请稍后重试", status);
  }
  return new ApiError("client", detailText || `请求失败（${status}）`, status);
}

/** 网络层错误 → ApiError；非幂等 mutation 附「结果未知」语义 */
function toNetworkApiError(method: string): ApiError {
  const nonIdempotent = method !== "GET";
  return new ApiError(
    "network",
    nonIdempotent
      ? "网络连接失败，操作结果未知，请刷新页面确认当前状态后再决定是否重试"
      : "网络连接失败，请检查网络后重试",
  );
}

const kyInstance = ky.create({
  timeout: 30000,
  headers: {
    // A2：服务端启用 API key 时随请求携带（vite env 注入；未设不带，兼容放行态）
    ...(import.meta.env.VITE_MYQUANT_API_KEY
      ? { Authorization: `Bearer ${import.meta.env.VITE_MYQUANT_API_KEY}` }
      : {}),
  },
  hooks: {
    afterResponse: [
      // 非 2xx：读 body 后统一转 ApiError（ky 默认 HTTPError 不带解析后的业务信息）
      async ({ response }: { response: Response }) => {
        if (response.ok) return response;
        let body: unknown = null;
        try {
          body = await response.json();
        } catch {
          body = null;
        }
        throw toHttpApiError(response.status, body);
      },
    ],
  },
});

/** 请求参数：查询串（params→searchParams）、JSON 体、FormData 体、单请求超时 */
export interface RequestOptions {
  params?: Record<string, string | number | boolean | undefined | null>;
  json?: unknown;
  body?: FormData | URLSearchParams | string;
  timeout?: number;
}

function buildOptions(opts: RequestOptions = {}): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (opts.params) {
    out.searchParams = Object.fromEntries(
      Object.entries(opts.params).filter(([, v]) => v !== undefined && v !== null),
    );
  }
  if (opts.json !== undefined) out.json = opts.json;
  if (opts.body !== undefined) out.body = opts.body;
  if (opts.timeout !== undefined) out.timeout = opts.timeout;
  return out;
}

async function request<T>(
  method: string,
  url: string,
  opts: RequestOptions,
): Promise<T> {
  // ky 需要 input 可构造为 Request：jsdom 等环境相对路径解析受限，统一解析为绝对 URL
  // （等价原 axios baseURL 语义：VITE_API_BASE_URL 优先，缺省同源）
  const base: string = import.meta.env.VITE_API_BASE_URL || globalThis.location?.origin || "";
  const absUrl = /^https?:/.test(url) ? url : `${base}${url}`;
  try {
    const response = await kyInstance(absUrl, {
      method,
      ...buildOptions(opts),
    });
    // 204/空体（DELETE 等）：json() 会抛 SyntaxError，显式判空
    if (response.status === 204) return undefined as T;
    const text = await response.text();
    return (text ? (JSON.parse(text) as T) : (undefined as T));
  } catch (e) {
    if (e instanceof ApiError) throw e;
    // 网络层（无 HTTP 状态）：fetch 原生 TypeError、ky NetworkError、ky TimeoutError
    if (
      e instanceof TypeError ||
      (e instanceof Error && (e.name === "NetworkError" || e.name === "TimeoutError"))
    ) {
      throw toNetworkApiError(method);
    }
    throw e;
  }
}

export const api = {
  get: <T>(url: string, opts?: RequestOptions) => request<T>("GET", url, opts ?? {}),
  post: <T>(url: string, opts?: RequestOptions) => request<T>("POST", url, opts ?? {}),
  put: <T>(url: string, opts?: RequestOptions) => request<T>("PUT", url, opts ?? {}),
  delete: <T = unknown>(url: string, opts?: RequestOptions) =>
    request<T>("DELETE", url, opts ?? {}),
};
