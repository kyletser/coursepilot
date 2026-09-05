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

function isSecureRequest(request: NextRequest, requestUrl: URL) {
  if (requestUrl.protocol === "https:") return true;
  // Behind a TLS-terminating reverse proxy the forwarded hop is http; trust
  // the first x-forwarded-proto value to keep the Secure flag correct.
  const forwardedProto = request.headers.get("x-forwarded-proto");
  if (!forwardedProto) return false;
  return forwardedProto.split(",")[0].trim().toLowerCase() === "https";
}

function cookieOptions(request: NextRequest, requestUrl: URL, maxAge: number) {
  return {
    httpOnly: true,
    maxAge,
    path: "/",
    sameSite: "lax" as const,
    secure: isSecureRequest(request, requestUrl),
  };
}

function isSameSiteRequest(request: NextRequest, incomingUrl: URL) {
  // Mutations must prove they originate from this site. Prefer Origin, fall
  // back to Sec-Fetch-Site (modern browsers) and Referer; a request carrying
  // none of these signals fails closed instead of passing by default.
  const origin = request.headers.get("origin");
  if (origin) {
    try {
      return new URL(origin).origin === incomingUrl.origin;
    } catch {
      return false;
    }
  }
  const secFetchSite = request.headers.get("sec-fetch-site");
  if (secFetchSite) {
    return secFetchSite.toLowerCase() === "same-origin";
  }
  const referer = request.headers.get("referer");
  if (referer) {
    try {
      return new URL(referer).origin === incomingUrl.origin;
    } catch {
      return false;
    }
  }
  return false;
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
    if (isMutation && !isSameSiteRequest(request, incomingUrl)) {
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
    // Keep the original client-facing protocol if we sit behind a TLS proxy,
    // otherwise report this hop's protocol to the upstream API.
    const existingProto = request.headers.get("x-forwarded-proto");
    headers.set(
      "x-forwarded-proto",
      existingProto?.split(",")[0].trim() || incomingUrl.protocol.slice(0, -1),
    );
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
          cookieOptions(request, incomingUrl, payload.data.expires_in ?? 900),
        );
        response.cookies.set(
          REFRESH_COOKIE,
          payload.data.refresh_token,
          cookieOptions(request, incomingUrl, REFRESH_MAX_AGE),
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
