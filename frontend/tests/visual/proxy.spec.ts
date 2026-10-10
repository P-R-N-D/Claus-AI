import { expect, test } from "@playwright/test";
import { NextRequest } from "next/server";
import { unstable_doesMiddlewareMatch } from "next/experimental/testing/server";
import { config, proxy } from "../../src/proxy";

// Unit-level checks that run without a browser. Next normalizes repeated slashes before the Proxy runs,
// so only a direct call exercises the Proxy's own guard.
test("proxy builds same-origin, non-scheme-relative UI redirects", () => {
  const cases: Array<[string, string]> = [
    ["http://127.0.0.1:3000//evil.example/", "/evil.example"],
    ["http://127.0.0.1:3000///evil.example/x/", "/evil.example/x"],
    ["http://127.0.0.1:3000/console/?view=tasks&filter=a%2Fb", "/console?view=tasks&filter=a%2Fb"],
  ];
  for (const [input, expected] of cases) {
    const request = new NextRequest(input);
    const response = proxy(request);
    expect(response.status, input).toBe(308);
    // NextRequest may canonicalize the loopback host name; the redirect must keep the request's own origin.
    const location = new URL(response.headers.get("location") ?? "", "http://invalid.example");
    expect(location.origin, input).toBe(new URL(request.url).origin);
    expect(`${location.pathname}${location.search}`, input).toBe(expected);
  }
  for (const input of ["http://127.0.0.1:3000/", "http://127.0.0.1:3000/console", "http://127.0.0.1:3000/core/health/"]) {
    expect(proxy(new NextRequest(input)).headers.get("location"), input).toBeNull();
  }
});

// unstable_doesMiddlewareMatch is a version-pinned Next helper; re-check it when Next is upgraded.
test("proxy matcher skips Next internals and backend prefixes only", () => {
  const cases: Array<[string, boolean]> = [
    ["/core/health/", false],
    ["/agent/docs/", false],
    ["/_next/static/chunk.js", false],
    ["/core-ui/", true],
    ["/agent-ui/", true],
    ["/console/", true],
    ["/", true],
  ];
  for (const [url, expected] of cases) {
    expect(unstable_doesMiddlewareMatch({ config, url, nextConfig: {} }), url).toBe(expected);
  }
});
