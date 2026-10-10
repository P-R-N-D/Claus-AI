import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Preserve backend slashes; src/proxy.ts restores canonical UI redirects.
  skipTrailingSlashRedirect: true,
  // Keep AI-facing instructions under repository control (see docs/CONTEXT.md).
  agentRules: false,
  experimental: {
    // Next 16 serves an unauthenticated dev-only MCP endpoint at /_next/mcp by default.
    mcpServer: false,
  },
  async rewrites() {
    return [
      {
        source: "/core/:path(.*)",
        destination: "http://127.0.0.1:8000/core/:path",
      },
      {
        source: "/agent/:path(.*)",
        destination: "http://127.0.0.1:8000/agent/:path",
      },
    ];
  },
};

export default nextConfig;
