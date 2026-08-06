/**
 * HTTP 래퍼 (F-06) — `_get` / `_post` / `_put` / `_delete` + 공통 에러 처리.
 *
 * ## 봉투 벗기기
 * 백엔드는 모든 응답을 `{ result, data, message, code }` 로 감싼다.
 * 호출부가 매번 `res.data.data` 를 쓰게 하지 않으려고 여기서 한 겹 벗긴다.
 * 실패는 `ApiError` 예외로 바꿔 던진다 — 호출부가 `if (res.result === "FAIL")` 를
 * 빼먹어도 조용히 성공으로 흘러가지 않도록.
 *
 * ## 토스트
 * 에러 알림은 이 모듈이 직접 store 를 import 하지 않고, 앱 부팅 시 등록된
 * 콜백(`setErrorNotifier`)으로 넘긴다. 순환 의존(store → http → store)을 끊고
 * 테스트에서 토스트 없이 단독 검증할 수 있게 하기 위함이다.
 */

export interface Envelope<T> {
  result: "SUCCESS" | "FAIL";
  data: T;
  message?: string;
  code?: string;
  /** 목록 응답에만 존재 */
  count?: number;
  page?: number;
  size?: number;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly data: unknown = null,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** 네트워크 자체가 안 되는 경우 (서버 다운, 오프라인) */
  get isNetworkError(): boolean {
    return this.status === 0;
  }
}

export interface RequestOptions {
  /** 이 요청의 에러는 토스트로 띄우지 않는다 (폴링·백그라운드 조회용). */
  silent?: boolean;
  signal?: AbortSignal;
  headers?: Record<string, string>;
  /** 기본 15초. 관제 명령이 무한정 매달려 있으면 안 된다. */
  timeoutMs?: number;
}

type ErrorNotifier = (error: ApiError) => void;
type RoleProvider = () => { role: string; operator: string };

let notifyError: ErrorNotifier | null = null;
let getRole: RoleProvider | null = null;

/** 앱 부팅 시 1회 등록 (main.tsx / AppProviders). */
export function setErrorNotifier(notifier: ErrorNotifier | null): void {
  notifyError = notifier;
}

/** 요청마다 X-Role / X-Operator 헤더를 붙이기 위한 공급자. */
export function setRoleProvider(provider: RoleProvider | null): void {
  getRole = provider;
}

const DEFAULT_TIMEOUT_MS = 15_000;

export const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? "http://localhost:8000";

function buildUrl(path: string, params?: Record<string, unknown>): string {
  const url = new URL(path.startsWith("http") ? path : `${API_BASE}${path}`);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value === undefined || value === null || value === "") continue;
      if (Array.isArray(value)) {
        value.forEach((v) => url.searchParams.append(key, String(v)));
      } else {
        url.searchParams.set(key, String(value));
      }
    }
  }
  return url.toString();
}

async function request<T>(
  method: "GET" | "POST" | "PUT" | "DELETE",
  path: string,
  { body, params, ...options }: RequestOptions & { body?: unknown; params?: Record<string, unknown> } = {},
): Promise<T> {
  const controller = new AbortController();
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  // 외부 signal 과 타임아웃 signal 을 함께 존중한다.
  options.signal?.addEventListener("abort", () => controller.abort(), { once: true });

  const identity = getRole?.();
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    ...(identity ? { "X-Role": identity.role, "X-Operator": identity.operator } : {}),
    ...options.headers,
  };

  let response: Response;
  try {
    response = await fetch(buildUrl(path, params), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
  } catch (cause) {
    clearTimeout(timeoutId);
    const aborted = (cause as Error)?.name === "AbortError";
    const error = new ApiError(
      0,
      aborted ? "TIMEOUT" : "NETWORK_ERROR",
      aborted
        ? `요청 시간이 초과되었습니다 (${timeoutMs / 1000}초): ${method} ${path}`
        : `서버에 연결할 수 없습니다: ${method} ${path}`,
    );
    if (!options.silent) notifyError?.(error);
    throw error;
  }
  clearTimeout(timeoutId);

  // CSV 다운로드처럼 JSON 이 아닌 응답
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    if (!response.ok) {
      const error = new ApiError(response.status, "HTTP_ERROR", `${response.status} ${response.statusText}`);
      if (!options.silent) notifyError?.(error);
      throw error;
    }
    return (await response.blob()) as unknown as T;
  }

  let envelope: Envelope<T>;
  try {
    envelope = (await response.json()) as Envelope<T>;
  } catch {
    const error = new ApiError(response.status, "BAD_RESPONSE", "서버 응답을 해석할 수 없습니다");
    if (!options.silent) notifyError?.(error);
    throw error;
  }

  if (!response.ok || envelope.result === "FAIL") {
    const error = new ApiError(
      response.status,
      envelope.code ?? "UNKNOWN",
      envelope.message ?? `요청이 실패했습니다 (${response.status})`,
      envelope.data ?? null,
    );
    if (!options.silent) notifyError?.(error);
    throw error;
  }

  return envelope.data;
}

/** 목록 응답 — data 와 함께 count/page/size 가 필요할 때. */
export async function _getPaged<T>(
  path: string,
  params?: Record<string, unknown>,
  options?: RequestOptions,
): Promise<{ items: T[]; count: number; page: number; size: number }> {
  const url = buildUrl(path, params);
  const identity = getRole?.();
  const response = await fetch(url, {
    headers: {
      Accept: "application/json",
      ...(identity ? { "X-Role": identity.role, "X-Operator": identity.operator } : {}),
    },
    signal: options?.signal,
  }).catch(() => null);

  if (response === null) {
    const error = new ApiError(0, "NETWORK_ERROR", `서버에 연결할 수 없습니다: GET ${path}`);
    if (!options?.silent) notifyError?.(error);
    throw error;
  }

  const envelope = (await response.json()) as Envelope<T[]>;
  if (!response.ok || envelope.result === "FAIL") {
    const error = new ApiError(
      response.status,
      envelope.code ?? "UNKNOWN",
      envelope.message ?? "요청이 실패했습니다",
    );
    if (!options?.silent) notifyError?.(error);
    throw error;
  }
  return {
    items: envelope.data ?? [],
    count: envelope.count ?? envelope.data?.length ?? 0,
    page: envelope.page ?? 1,
    size: envelope.size ?? envelope.data?.length ?? 0,
  };
}

export function _get<T>(
  path: string,
  params?: Record<string, unknown>,
  options?: RequestOptions,
): Promise<T> {
  return request<T>("GET", path, { params, ...options });
}

export function _post<T>(path: string, body?: unknown, options?: RequestOptions): Promise<T> {
  return request<T>("POST", path, { body, ...options });
}

export function _put<T>(path: string, body?: unknown, options?: RequestOptions): Promise<T> {
  return request<T>("PUT", path, { body, ...options });
}

export function _delete<T>(path: string, options?: RequestOptions): Promise<T> {
  return request<T>("DELETE", path, options);
}

/** 헬스체크 — 봉투 규격이 살짝 달라 별도 처리. */
export async function checkHealth(): Promise<boolean> {
  try {
    const response = await fetch(`${API_BASE}/health`);
    return response.ok;
  } catch {
    return false;
  }
}
