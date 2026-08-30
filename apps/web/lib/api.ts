import type {
  ApiEnvelope,
  AuthTokens,
  ChatStreamEvent,
  User,
} from "@/lib/types";

const API_PREFIX = "/api/v1";
const SESSION_KEY = "coursepilot.auth.v1";

export type StoredSession = {
  tokens: AuthTokens;
  user?: User;
};

export class ApiError extends Error {
  code: string;
  details: Record<string, unknown>;
  requestId?: string;
  status?: number;

  constructor(
    message: string,
    options: {
      code?: string;
      details?: Record<string, unknown>;
      requestId?: string;
      status?: number;
    } = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.code = options.code ?? "REQUEST_FAILED";
    this.details = options.details ?? {};
    this.requestId = options.requestId;
    this.status = options.status;
  }
}

function canUseStorage() {
  return typeof window !== "undefined";
}

export function getStoredSession(): StoredSession | null {
  if (!canUseStorage()) return null;
  try {
    const raw = window.localStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredSession;
    if (!parsed.tokens?.access_token || !parsed.tokens?.refresh_token) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export function setStoredSession(session: StoredSession | null) {
  if (!canUseStorage()) return;
  if (session) {
    window.localStorage.setItem(SESSION_KEY, JSON.stringify(session));
    window.dispatchEvent(new Event("coursepilot:auth-change"));
  } else {
    window.localStorage.removeItem(SESSION_KEY);
    window.dispatchEvent(new Event("coursepilot:auth-change"));
  }
}

async function readEnvelope<T>(
  response: Response,
): Promise<ApiEnvelope<T> & { data: T; error: null }> {
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    const message = response.ok
      ? "服务端返回了无法识别的响应"
      : `请求失败（HTTP ${response.status}）`;
    throw new ApiError(message, {
      code: "INVALID_API_RESPONSE",
      status: response.status,
    });
  }

  let envelope: ApiEnvelope<T>;
  try {
    envelope = (await response.json()) as ApiEnvelope<T>;
  } catch {
    throw new ApiError("服务端返回了无效的 JSON", {
      code: "INVALID_API_RESPONSE",
      status: response.status,
    });
  }

  const data = envelope.data;
  if (!response.ok || envelope.error || data === null) {
    throw new ApiError(
      envelope.error?.message ?? `请求失败（HTTP ${response.status}）`,
      {
        code: envelope.error?.code,
        details: envelope.error?.details,
        requestId: envelope.request_id,
        status: response.status,
      },
    );
  }
  return { ...envelope, data, error: null };
}

function buildHeaders(init?: RequestInit, accessToken?: string) {
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  if (init?.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  return headers;
}

function toApiError(error: unknown) {
  if (error instanceof ApiError) return error;
  if (error instanceof Error) {
    return new ApiError(error.message, { code: "NETWORK_ERROR" });
  }
  return new ApiError("无法连接到 CoursePilot API", {
    code: "NETWORK_ERROR",
  });
}

let refreshInFlight: Promise<AuthTokens> | null = null;

async function refreshTokens(): Promise<AuthTokens> {
  const current = getStoredSession();
  if (!current?.tokens.refresh_token) {
    throw new ApiError("登录状态已失效，请重新登录", {
      code: "AUTH_REQUIRED",
      status: 401,
    });
  }

  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      const response = await fetch(`${API_PREFIX}/auth/refresh`, {
        method: "POST",
        headers: buildHeaders({ body: "{}" }),
        body: JSON.stringify({ refresh_token: current.tokens.refresh_token }),
      });
      const { data } = await readEnvelope<AuthTokens>(response);
      setStoredSession({ tokens: data, user: current.user });
      return data;
    })().finally(() => {
      refreshInFlight = null;
    });
  }

  try {
    return await refreshInFlight;
  } catch (error) {
    setStoredSession(null);
    throw error;
  }
}

async function fetchWithAuth(
  path: string,
  init: RequestInit = {},
  retryAfterRefresh = true,
) {
  const current = getStoredSession();
  const response = await fetch(`${API_PREFIX}${path}`, {
    ...init,
    headers: buildHeaders(init, current?.tokens.access_token),
  });

  if (response.status === 401 && retryAfterRefresh && current) {
    const tokens = await refreshTokens();
    return fetch(`${API_PREFIX}${path}`, {
      ...init,
      headers: buildHeaders(init, tokens.access_token),
    });
  }
  return response;
}

export async function apiRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  try {
    const response = await fetchWithAuth(path, init);
    const envelope = await readEnvelope<T>(response);
    return envelope.data;
  } catch (error) {
    throw toApiError(error);
  }
}

export async function publicApiRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  try {
    const response = await fetch(`${API_PREFIX}${path}`, {
      ...init,
      headers: buildHeaders(init),
    });
    const envelope = await readEnvelope<T>(response);
    return envelope.data;
  } catch (error) {
    throw toApiError(error);
  }
}

export function jsonBody(value: unknown): RequestInit {
  return { body: JSON.stringify(value) };
}

function parseSseBlock(block: string): ChatStreamEvent | null {
  let eventType = "message";
  const dataLines: string[] = [];
  for (const rawLine of block.split(/\r?\n/)) {
    if (rawLine.startsWith("event:")) {
      eventType = rawLine.slice(6).trim();
    } else if (rawLine.startsWith("data:")) {
      dataLines.push(rawLine.slice(5).trimStart());
    }
  }
  if (!dataLines.length || eventType === "message") return null;
  try {
    return {
      type: eventType,
      data: JSON.parse(dataLines.join("\n")) as Record<string, unknown>,
    } as ChatStreamEvent;
  } catch {
    throw new ApiError("问答流返回了无法解析的事件", {
      code: "INVALID_STREAM_EVENT",
    });
  }
}

export async function streamChatMessage(
  sessionId: string,
  payload: {
    content: string;
    requested_intent: string | null;
    target_concept_ids: string[];
  },
  onEvent: (event: ChatStreamEvent) => void,
) {
  let response: Response;
  try {
    response = await fetchWithAuth(`/chat/sessions/${sessionId}/messages`, {
      method: "POST",
      headers: { Accept: "text/event-stream" },
      body: JSON.stringify(payload),
    });
  } catch (error) {
    throw toApiError(error);
  }

  if (!response.ok || !response.body) {
    if ((response.headers.get("content-type") ?? "").includes("application/json")) {
      await readEnvelope<never>(response);
    }
    throw new ApiError(`问答请求失败（HTTP ${response.status}）`, {
      code: "CHAT_STREAM_FAILED",
      status: response.status,
    });
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      const event = parseSseBlock(block);
      if (event) onEvent(event);
    }
    if (done) break;
  }
  if (buffer.trim()) {
    const event = parseSseBlock(buffer);
    if (event) onEvent(event);
  }
}

export function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "请求失败，请稍后重试";
}
