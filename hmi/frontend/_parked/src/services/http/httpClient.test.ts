/** HTTP 래퍼 테스트 (F-06) — 봉투 벗기기 · 에러 → 토스트 · 역할 헤더. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  _delete,
  _get,
  _getPaged,
  _post,
  _put,
  setErrorNotifier,
  setRoleProvider,
} from "./httpClient";

const notified: ApiError[] = [];

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  notified.length = 0;
  setErrorNotifier((error) => notified.push(error));
  setRoleProvider(() => ({ role: "OPERATOR", operator: "tester" }));
});

afterEach(() => {
  setErrorNotifier(null);
  setRoleProvider(null);
  vi.unstubAllGlobals();
});

describe("성공 응답", () => {
  it("봉투를 벗기고 data 만 돌려준다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ result: "SUCCESS", data: { robot_id: "amr_1" } })),
    );
    await expect(_get("/api/robots/amr_1")).resolves.toEqual({ robot_id: "amr_1" });
    expect(notified).toHaveLength(0);
  });

  it("쿼리 파라미터를 URL 에 붙이고 빈 값은 뺀다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ result: "SUCCESS", data: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await _get("/api/events", { zone_id: "Z01", type: "", page: 2, missing: undefined });

    const url = new URL(fetchMock.mock.calls[0][0] as string);
    expect(url.searchParams.get("zone_id")).toBe("Z01");
    expect(url.searchParams.get("page")).toBe("2");
    expect(url.searchParams.has("type")).toBe(false);
    expect(url.searchParams.has("missing")).toBe(false);
  });

  it("배열 파라미터는 같은 키로 반복 추가한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ result: "SUCCESS", data: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await _get("/api/robots", { robot_ids: ["amr_1", "amr_2"] });
    const url = new URL(fetchMock.mock.calls[0][0] as string);
    expect(url.searchParams.getAll("robot_ids")).toEqual(["amr_1", "amr_2"]);
  });

  it("POST 는 JSON 바디와 Content-Type 을 함께 보낸다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ result: "SUCCESS", data: {} }));
    vi.stubGlobal("fetch", fetchMock);

    await _post("/api/patrol/start", { route_id: "R-01" });
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ route_id: "R-01" }));
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("PUT / DELETE 도 같은 규칙을 따른다", async () => {
    // Response 본문은 한 번만 읽을 수 있다. 호출마다 새 응답을 만들어야 한다.
    const fetchMock = vi.fn().mockImplementation(async () =>
      jsonResponse({ result: "SUCCESS", data: {} }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await _put("/api/nodes/N-001", { name: "변경" });
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("PUT");

    await _delete("/api/nodes/N-001");
    expect((fetchMock.mock.calls[1][1] as RequestInit).method).toBe("DELETE");
  });

  it("역할·조작자 헤더를 자동으로 붙인다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ result: "SUCCESS", data: {} }));
    vi.stubGlobal("fetch", fetchMock);

    await _get("/api/robots");
    const headers = (fetchMock.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(headers["X-Role"]).toBe("OPERATOR");
    expect(headers["X-Operator"]).toBe("tester");
  });

  it("페이지네이션 응답은 count/page/size 를 함께 돌려준다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ result: "SUCCESS", count: 128, page: 2, size: 50, data: [{ event_id: "EV-1" }] }),
      ),
    );
    const result = await _getPaged("/api/events", { page: 2 });
    expect(result).toMatchObject({ count: 128, page: 2, size: 50 });
    expect(result.items).toHaveLength(1);
  });
});

describe("실패 응답", () => {
  it("result=FAIL 이면 ApiError 로 던지고 토스트를 띄운다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          { result: "FAIL", code: "MISSION_ALREADY_RUNNING", message: "이미 실행 중인 미션이 있습니다" },
          409,
        ),
      ),
    );

    await expect(_post("/api/patrol/start", {})).rejects.toThrow(ApiError);
    expect(notified).toHaveLength(1);
    expect(notified[0].code).toBe("MISSION_ALREADY_RUNNING");
    expect(notified[0].status).toBe(409);
  });

  it("HTTP 200 이어도 result=FAIL 이면 실패로 처리한다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ result: "FAIL", code: "X", message: "실패" }, 200)),
    );
    await expect(_get("/api/robots")).rejects.toThrow(ApiError);
  });

  it("silent 옵션이면 토스트를 띄우지 않는다 (폴링용)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ result: "FAIL", code: "X", message: "실패" }, 500)),
    );
    await expect(_get("/api/stats/overview", undefined, { silent: true })).rejects.toThrow(ApiError);
    expect(notified).toHaveLength(0);
  });

  it("네트워크 실패는 status=0 · NETWORK_ERROR 로 정규화한다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    await expect(_get("/api/robots")).rejects.toMatchObject({ status: 0, code: "NETWORK_ERROR" });
    expect(notified[0].isNetworkError).toBe(true);
  });

  it("응답이 JSON 이 아니면 BAD_RESPONSE 로 알린다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("<html>500</html>", { status: 200, headers: { "content-type": "application/json" } }),
      ),
    );
    await expect(_get("/api/robots")).rejects.toMatchObject({ code: "BAD_RESPONSE" });
  });

  it("타임아웃은 TIMEOUT 코드로 구분된다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => {
        const error = new Error("aborted");
        error.name = "AbortError";
        return Promise.reject(error);
      }),
    );
    await expect(_get("/api/robots", undefined, { timeoutMs: 10 })).rejects.toMatchObject({
      code: "TIMEOUT",
    });
  });
});

describe("비 JSON 응답 (CSV 내보내기)", () => {
  it("Blob 을 그대로 돌려준다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("event_id,type\nEV-1,FIRE", {
          status: 200,
          headers: { "content-type": "text/csv; charset=utf-8" },
        }),
      ),
    );
    const blob = await _get<Blob>("/api/events/export", { format: "csv" });
    // jsdom 의 Blob 과 undici 의 Blob 은 서로 다른 클래스라 instanceof 로 못 잡는다.
    expect(typeof blob.text).toBe("function");
    await expect(blob.text()).resolves.toContain("EV-1");
  });

  it("비 JSON 이면서 실패면 HTTP_ERROR 로 알린다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("nope", { status: 500, headers: { "content-type": "text/plain" } })),
    );
    await expect(_get("/api/events/export")).rejects.toMatchObject({ code: "HTTP_ERROR" });
  });
});
