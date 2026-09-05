import type {
  ApiEnvelope,
  ChatStreamEvent,
} from "@/lib/types";

const API_PREFIX = "/api/v1";
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

function buildHeaders(init?: RequestInit) {
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  if (init?.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  return headers;
}

function toApiError(error: unknown) {
  if (error instanceof ApiError) return error;
  if (typeof DOMException !== "undefined" && error instanceof DOMException) {
    if (error.name === "AbortError" || error.name === "TimeoutError") {
      return new ApiError("请求超时，请稍后重试", { code: "REQUEST_TIMEOUT" });
    }
  }
  if (error instanceof Error) {
    return new ApiError(error.message, { code: "NETWORK_ERROR" });
  }
  return new ApiError("无法连接到 CoursePilot API", {
    code: "NETWORK_ERROR",
  });
}

const DEFAULT_REQUEST_TIMEOUT_MS = 30_000;

/**
 * Bound one fetch by both the caller's signal (cancellation) and a timeout.
 * Streaming callers pass timeoutMs = 0 so only their own signal applies —
 * a fixed timeout would also abort long-lived SSE body reads.
 */
function withTimeout(init: RequestInit, timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS): RequestInit {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  if (timeoutMs > 0) {
    timer = setTimeout(() => controller.abort(), timeoutMs);
  }
  const forwardAbort = () => controller.abort();
  if (init.signal) {
    if (init.signal.aborted) controller.abort();
    else init.signal.addEventListener("abort", forwardAbort, { once: true });
  }
  controller.signal.addEventListener(
    "abort",
    () => {
      if (timer !== undefined) clearTimeout(timer);
      init.signal?.removeEventListener("abort", forwardAbort);
    },
    { once: true },
  );
  return { ...init, signal: controller.signal };
}

let refreshInFlight: Promise<void> | null = null;

async function refreshTokens(): Promise<void> {
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      const response = await fetch(`${API_PREFIX}/auth/refresh`, {
        method: "POST",
        headers: buildHeaders({ body: "{}" }),
        body: "{}",
      });
      await readEnvelope<{ authenticated: boolean }>(response);
    })().finally(() => {
      refreshInFlight = null;
    });
  }

  return await refreshInFlight;
}

async function fetchWithAuth(
  path: string,
  init: RequestInit = {},
  retryAfterRefresh = true,
  timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
) {
  const response = await fetch(
    `${API_PREFIX}${path}`,
    withTimeout({ ...init, headers: buildHeaders(init) }, timeoutMs),
  );

  const canRefresh = !["/auth/login", "/auth/refresh", "/auth/logout"].includes(path);
  if (response.status === 401 && retryAfterRefresh && canRefresh) {
    await refreshTokens();
    return fetch(
      `${API_PREFIX}${path}`,
      withTimeout({ ...init, headers: buildHeaders(init) }, timeoutMs),
    );
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

const STREAM_CONNECT_TIMEOUT_MS = 30_000;

export async function streamChatMessage(
  sessionId: string,
  payload: {
    content: string;
    requested_intent: string | null;
    target_concept_ids: string[];
  },
  onEvent: (event: ChatStreamEvent) => void,
  options?: { signal?: AbortSignal },
) {
  const callerSignal = options?.signal;
  const streamController = new AbortController();
  const forwardAbort = () => streamController.abort();
  callerSignal?.addEventListener("abort", forwardAbort, { once: true });
  // Only the connect phase gets a timeout; the SSE body read itself may run
  // for the whole turn, so it stays bound to the caller's signal alone.
  const connectTimer = setTimeout(() => streamController.abort(), STREAM_CONNECT_TIMEOUT_MS);

  let response: Response;
  try {
    response = await fetchWithAuth(
      `/chat/sessions/${sessionId}/messages`,
      {
        method: "POST",
        headers: { Accept: "text/event-stream" },
        body: JSON.stringify(payload),
        signal: streamController.signal,
      },
      true,
      0,
    );
  } catch (error) {
    if (callerSignal?.aborted) {
      throw new ApiError("本次提问已取消", { code: "REQUEST_ABORTED" });
    }
    if (streamController.signal.aborted && !callerSignal?.aborted) {
      const apiError = toApiError(error);
      if (apiError.code === "REQUEST_TIMEOUT" || apiError.code === "NETWORK_ERROR") {
        throw new ApiError("无法建立问答连接，请检查网络后重试", {
          code: "CHAT_STREAM_CONNECT_FAILED",
        });
      }
      throw apiError;
    }
    throw toApiError(error);
  } finally {
    clearTimeout(connectTimer);
    callerSignal?.removeEventListener("abort", forwardAbort);
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

  // If the body ends without a terminal done/error event, the connection was
  // cut mid-stream (proxy timeout, worker restart). Surface it explicitly so
  // the UI can recover the persisted message history instead of hanging.
  let terminated = false;
  const handleEvent = (event: ChatStreamEvent) => {
    if (event.type === "done" || event.type === "error") terminated = true;
    onEvent(event);
  };

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() ?? "";
      for (const block of blocks) {
        const event = parseSseBlock(block);
        if (event) handleEvent(event);
      }
      if (done) break;
    }
    if (buffer.trim()) {
      const event = parseSseBlock(buffer);
      if (event) handleEvent(event);
    }
  } catch (error) {
    if (callerSignal?.aborted) {
      throw new ApiError("本次提问已取消", { code: "REQUEST_ABORTED" });
    }
    throw toApiError(error);
  }
  if (!terminated) {
    throw new ApiError("问答流被中断，回答可能未生成完整", {
      code: "CHAT_STREAM_INTERRUPTED",
    });
  }
}

export function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "请求失败，请稍后重试";
}
