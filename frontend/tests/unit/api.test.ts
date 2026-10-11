import type { AxiosAdapter, InternalAxiosRequestConfig } from "axios";
import { describe, expect, it } from "vitest";
import { agentApi, coreApi } from "@/lib/api";

// Answers every request locally and records the config axios would have sent.
function recordingAdapter(seen: InternalAxiosRequestConfig[]): AxiosAdapter {
  return async (config) => {
    seen.push(config);
    return { data: { status: "ok" }, status: 200, statusText: "OK", headers: {}, config };
  };
}

describe("API clients", () => {
  it.each([
    ["Core", "/core/", coreApi],
    ["Agent", "/agent/", agentApi],
  ] as const)("%s client targets the same-origin %s prefix with a 5 second timeout", (_name, prefix, client) => {
    expect(client.defaults.baseURL).toBe(prefix);
    expect(client.defaults.timeout).toBe(5000);
  });

  it("are separate instances, so a change to one client's defaults cannot leak into the other", () => {
    expect(coreApi).not.toBe(agentApi);
    expect(coreApi.defaults).not.toBe(agentApi.defaults);
  });

  it.each([
    ["Core", "/core/health/", coreApi],
    ["Agent", "/agent/health/", agentApi],
  ] as const)("%s health request goes to %s, keeping the trailing slash the backend route requires", async (_name, url, client) => {
    const seen: InternalAxiosRequestConfig[] = [];
    const response = await client.get("health/", { adapter: recordingAdapter(seen) });

    expect(response.status).toBe(200);
    expect(seen).toHaveLength(1);
    expect(client.getUri(seen[0])).toBe(url);
    expect(seen[0].method).toBe("get");
    expect(seen[0].timeout).toBe(5000);
  });
});
