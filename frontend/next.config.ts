import type { NextConfig } from "next";

/**
 * Next.js configuration.
 *
 * `output: "standalone"` produces the self-contained server bundle consumed by
 * deploy/frontend.Dockerfile (ADR-0014). SSE relay rewrites and the pdf.js
 * viewer asset config arrive in later phases alongside their features.
 */
const nextConfig: NextConfig = {
  output: "standalone",
};

export default nextConfig;
