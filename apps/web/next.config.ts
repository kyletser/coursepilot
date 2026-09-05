import path from "node:path";

import type { NextConfig } from "next";

// Keep local, CI, and container builds deterministic and free from host-level
// telemetry state (notably the global config store used by Next.js on Windows).
process.env.NEXT_TELEMETRY_DISABLED ??= "1";

const nextConfig: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: path.join(process.cwd(), "../.."),
  poweredByHeader: false,
  async headers() {
    // Baseline CSP for the Next.js app runtime: same-origin everything, inline
    // styles/scripts allowed because Next.js hydrates with inline bootstrap
    // chunks; connect-src 'self' covers both JSON API calls and SSE streams.
    const contentSecurityPolicy = [
      "default-src 'self'",
      "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: blob:",
      "font-src 'self' data:",
      "connect-src 'self'",
      "frame-ancestors 'none'",
      "base-uri 'self'",
      "form-action 'self'",
      "object-src 'none'",
    ].join("; ");
    return [
      {
        source: "/:path*",
        headers: [
          { key: "content-security-policy", value: contentSecurityPolicy },
          { key: "x-content-type-options", value: "nosniff" },
          { key: "referrer-policy", value: "strict-origin-when-cross-origin" },
          { key: "x-frame-options", value: "DENY" },
          { key: "permissions-policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
