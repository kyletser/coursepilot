import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type ProxyContext = {
  params: Promise<{ path: string[] }>;
};

const requestHopByHopHeaders = [
  "connection",
  "content-length",
  "host",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
] as const;

const responseHopByHopHeaders = [
  "connection",
  "content-encoding",
  "content-length",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
] as const;

const ACCESS_COOKIE = "coursepilot_access";
const REFRESH_COOKIE = "coursepilot_refresh";
const REFRESH_MAX_AGE = 14 * 24 * 60 * 60;

function cookieOptions(requestUrl: URL, maxAge: number) {
  return {
    httpOnly: true,
    maxAge,
    path: "/",
    sameSite: "lax" as const,
    secure: requestUrl.protocol === "https:",
  };
}

function getApiOrigin() {
  const rawOrigin = process.env.API_INTERNAL_URL ?? "http://127.0.0.1:8000";
  const origin = new URL(rawOrigin);

  if (!['http:', 'https:'].includes(origin.protocol)) {
    throw new Error("API_INTERNAL_URL must use http or https");
  }

  return origin;
}

async function proxyRequest(request: NextRequest, context: ProxyContext) {
  try {
    const { path } = await context.params;
    const incomingUrl = new URL(request.url);
    const apiOrigin = getApiOrigin();
    const encodedPath = path.map(encodeURIComponent).join("/");
    const targetUrl = new URL(`/api/${encodedPath}`, apiOrigin);
    targetUrl.search = incomingUrl.search;

    const isMutation = !["GET", "HEAD", "OPTIONS"].includes(request.method);
    const origin = request.headers.get("origin");
    let originMatches = true;
    if (origin) {
      try {
        originMatches = new URL(origin).origin === incomingUrl.origin;
      } catch {
        originMatches = false;
      }
    }
    if (isMutation && !originMatches) {
      return Response.json(
        {
          data: null,
          error: {
            code: "CSRF_REJECTED",
            message: "Cross-origin request rejected",
            details: {},
          },
          request_id: crypto.randomUUID(),
        },
        { status: 403 },
      );
    }

    const routeKey = path.join("/");
    const isLogin = routeKey === "v1/auth/login";
    const isRefresh = routeKey === "v1/auth/refresh";
    const isLogout = routeKey === "v1/auth/logout";

    const headers = new Headers(request.headers);
    requestHopByHopHeaders.forEach((header) => headers.delete(header));
    headers.set("x-forwarded-host", incomingUrl.host);
    headers.set("x-forwarded-proto", incomingUrl.protocol.slice(0, -1));
    headers.delete("cookie");
    headers.delete("authorization");
    const accessToken = request.cookies.get(ACCESS_COOKIE)?.value;
    if (accessToken) headers.set("authorization", `Bearer ${accessToken}`);

    const hasBody = request.method !== "GET" && request.method !== "HEAD";
    let body: BodyInit | null | undefined = hasBody ? request.body : undefined;
    if (isRefresh || isLogout) {
      body = JSON.stringify({
        refresh_token: request.cookies.get(REFRESH_COOKIE)?.value ?? "",
      });
      headers.set("content-type", "application/json");
    }
    const init: RequestInit & { duplex?: "half" } = {
      body,
      headers,
      method: request.method,
      redirect: "manual",
    };

    if (hasBody) {
      init.duplex = "half";
    }

    const upstreamResponse = await fetch(targetUrl, init);

    if (isLogin || isRefresh) {
      const payload = (await upstreamResponse.json()) as {
        data?: { access_token?: string; refresh_token?: string; expires_in?: number };
        [key: string]: unknown;
      };
      const responsePayload =
        upstreamResponse.ok && payload.data?.access_token && payload.data.refresh_token
          ? {
              ...payload,
              data: {
                authenticated: true,
                expires_in: payload.data.expires_in ?? 900,
              },
            }
          : payload;
      const response = NextResponse.json(responsePayload, {
        status: upstreamResponse.status,
      });
      if (upstreamResponse.ok && payload.data?.access_token && payload.data.refresh_token) {
        response.cookies.set(
          ACCESS_COOKIE,
          payload.data.access_token,
          cookieOptions(incomingUrl, payload.data.expires_in ?? 900),
        );
        response.cookies.set(
          REFRESH_COOKIE,
          payload.data.refresh_token,
          cookieOptions(incomingUrl, REFRESH_MAX_AGE),
        );
      } else if (isRefresh) {
        response.cookies.delete(ACCESS_COOKIE);
        response.cookies.delete(REFRESH_COOKIE);
      }
      return response;
    }

    if (isLogout) {
      const response = new NextResponse(upstreamResponse.body, {
        headers: upstreamResponse.headers,
        status: upstreamResponse.status,
      });
      response.cookies.delete(ACCESS_COOKIE);
      response.cookies.delete(REFRESH_COOKIE);
      return response;
    }

    const responseHeaders = new Headers(upstreamResponse.headers);
    responseHopByHopHeaders.forEach((header) => responseHeaders.delete(header));

    return new Response(upstreamResponse.body, {
      headers: responseHeaders,
      status: upstreamResponse.status,
      statusText: upstreamResponse.statusText,
    });
  } catch {
    return Response.json(
      {
        data: null,
        error: {
          code: "API_UNAVAILABLE",
          message: "CoursePilot API is temporarily unavailable",
          details: {},
        },
        request_id: crypto.randomUUID(),
      },
      { status: 502 },
    );
  }
}

export const GET = proxyRequest;
export const HEAD = proxyRequest;
export const POST = proxyRequest;
export const PUT = proxyRequest;
export const PATCH = proxyRequest;
export const DELETE = proxyRequest;
export const OPTIONS = proxyRequest;
