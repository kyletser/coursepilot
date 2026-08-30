import type { NextRequest } from "next/server";

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

    const headers = new Headers(request.headers);
    requestHopByHopHeaders.forEach((header) => headers.delete(header));
    headers.set("x-forwarded-host", incomingUrl.host);
    headers.set("x-forwarded-proto", incomingUrl.protocol.slice(0, -1));

    const hasBody = request.method !== "GET" && request.method !== "HEAD";
    const init: RequestInit & { duplex?: "half" } = {
      body: hasBody ? request.body : undefined,
      headers,
      method: request.method,
      redirect: "manual",
    };

    if (hasBody) {
      init.duplex = "half";
    }

    const upstreamResponse = await fetch(targetUrl, init);
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
