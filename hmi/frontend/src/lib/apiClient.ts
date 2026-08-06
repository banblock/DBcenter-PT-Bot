export const MOCK = import.meta.env.VITE_MOCK !== "false";
export const WS_URL = import.meta.env.VITE_WS_URL ?? "ws://localhost:8001/ws/amr/status";

class ApiClient {
  constructor(private readonly baseUrl = import.meta.env.VITE_API_BASE ?? "") {}

  async post(endpoint: string, body?: unknown): Promise<Response | null> {
    if (MOCK) return null;
    try {
      return await fetch(`${this.baseUrl}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      return null;
    }
  }
}

export const apiClient = new ApiClient();
