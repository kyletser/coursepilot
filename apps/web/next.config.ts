import path from "node:path";

import type { NextConfig } from "next";

// Keep local, CI, and container builds deterministic and free from host-level
// telemetry state (notably the global config store used by Next.js on Windows).
process.env.NEXT_TELEMETRY_DISABLED ??= "1";

const nextConfig: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: path.join(process.cwd(), "../.."),
  poweredByHeader: false,
};

export default nextConfig;
