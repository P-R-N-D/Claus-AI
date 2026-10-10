import { expect, test, type Page } from "@playwright/test";

// clientWidth is the layout viewport the page was designed for. Under mobile emulation Chromium widens
// window.innerWidth to fit overflowing content, so an innerWidth-based check cannot fail at phone width.
const horizontalOverflow = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

test("user and console surfaces render with both backend services", async ({ page }, testInfo) => {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  const failedRequests: string[] = [];
  const httpErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(`${message.text()} (${message.location().url})`);
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("requestfailed", (request) => failedRequests.push(`${request.method()} ${request.url()}: ${request.failure()?.errorText}`));
  page.on("response", (response) => {
    if (response.status() >= 400) httpErrors.push(`${response.status()} ${response.url()}`);
  });

  for (const path of ["/core/health/", "/agent/health/", "/agent/openapi.json"]) {
    const response = await page.request.get(path, { maxRedirects: 0 });
    expect(response.status(), `${path} must not redirect`).toBe(200);
  }

  const query = "?view=tasks&filter=a%2Fb";
  for (const path of ["/console", "/core-ui", "/agent-ui"]) {
    const response = await page.request.get(`${path}/${query}`, { maxRedirects: 0 });
    expect(response.status(), `${path}/ must keep its canonical UI redirect`).toBe(308);
    const location = response.headers()["location"];
    expect(location).toBeDefined();
    expect(new URL(location, response.url()).href).toBe(new URL(`${path}${query}`, response.url()).href);
  }

  // Canary: Next's own repeated-slash 308 answers "//..." before the Proxy runs; the encoded form reaches the Proxy.
  const origin = new URL(test.info().project.use.baseURL ?? "http://127.0.0.1:3000").origin;
  for (const [path, target] of [["//evil.example/", "/evil.example/"], ["/%2F%2Fevil.example/", "/%2F%2Fevil.example"]]) {
    const response = await page.request.get(`${origin}${path}`, { maxRedirects: 0 });
    expect(response.status(), `${path} must redirect`).toBe(308);
    const location = response.headers()["location"];
    expect(location, `${path} must send a Location`).toBeDefined();
    expect(new URL(location, response.url()).href, `${path} must stay same-origin`).toBe(`${origin}${target}`);
  }

  const docsResponse = await page.request.get("/agent/docs", { maxRedirects: 0 });
  expect(docsResponse.status()).toBe(200);
  // The agent app does not redirect slash mismatches: an absolute FastAPI redirect would leave the frontend origin.
  for (const path of ["/agent/docs/", "/agent/health"]) {
    const response = await page.request.get(`${path}${query}`, { maxRedirects: 0 });
    expect(response.status(), `${path} must not redirect`).toBe(404);
    expect(response.headers()["location"]).toBeUndefined();
  }
  const coreRedirect = await page.request.get(`/core/health${query}`, { maxRedirects: 0 });
  expect(coreRedirect.status()).toBe(301);
  const coreLocation = new URL(coreRedirect.headers()["location"], coreRedirect.url());
  expect(coreLocation.href).toBe(new URL(`/core/health/${query}`, coreRedirect.url()).href);

  const mcp = await page.request.post("/_next/mcp", {
    data: { jsonrpc: "2.0", id: 1, method: "tools/list" },
    maxRedirects: 0,
  });
  expect(mcp.status(), "Next dev MCP endpoint must be disabled").toBe(404);

  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Claus" })).toBeVisible();
  await expect(page.getByRole("article", { name: "Core health" }).getByText("Connected", { exact: true })).toBeVisible();
  await expect(page.getByRole("article", { name: "Agent health" }).getByText("Connected", { exact: true })).toBeVisible();
  for (const name of ["Core", "Agent"]) {
    await expect(page.getByRole("article", { name: `${name} health` }).getByText('"status": "ok"')).toBeVisible();
  }
  await expect(page.getByText('"backend": "django"')).toBeVisible();
  await expect(page.getByText('"api": "drf"')).toBeVisible();
  await expect(page.getByText('"backend": "fastapi"')).toBeVisible();

  await expect.poll(() => horizontalOverflow(page)).toBeLessThanOrEqual(0);
  await page.screenshot({ path: testInfo.outputPath("home.png"), fullPage: true });

  const retry = page.getByRole("button", { name: "Retry backend check" });
  await expect(retry).toBeEnabled();
  // Hold the retried Core request so the in-flight state is observable.
  let releaseCore!: () => void;
  const coreHeld = new Promise<void>((resolve) => {
    releaseCore = resolve;
  });
  await page.route("**/core/health/", async (route) => {
    await coreHeld;
    await route.continue();
  });
  const retried = Promise.all([
    page.waitForResponse((response) => response.url().endsWith("/core/health/") && response.status() === 200),
    page.waitForResponse((response) => response.url().endsWith("/agent/health/") && response.status() === 200),
  ]);
  await retry.focus();
  await page.keyboard.press("Enter");
  await expect(retry).toBeDisabled();
  await expect(retry).toHaveAttribute("aria-busy", "true");
  await expect(retry).toBeFocused();
  // Viewport-only screenshot keeps the hold short (axios times out after 5 s).
  await page.screenshot({ path: testInfo.outputPath("home-retrying.png") });
  releaseCore();
  await retried;
  await page.unroute("**/core/health/");
  await expect(page.getByRole("article", { name: "Core health" }).getByText("Connected", { exact: true })).toBeVisible();
  await expect(page.getByRole("article", { name: "Agent health" }).getByText("Connected", { exact: true })).toBeVisible();
  await expect(retry).toBeEnabled();
  await expect(retry).toBeFocused();
  // Pointer activation must work too.
  await Promise.all([
    page.waitForResponse((response) => response.url().endsWith("/core/health/") && response.status() === 200),
    page.waitForResponse((response) => response.url().endsWith("/agent/health/") && response.status() === 200),
    retry.click(),
  ]);
  await expect(page.getByRole("article", { name: "Core health" }).getByText("Connected", { exact: true })).toBeVisible();
  await expect(retry).toBeEnabled();

  await page.goto(`/console/${query}`);
  await expect(page).toHaveURL(new URL(`/console${query}`, page.url()).href);
  await expect(page.getByText("Claus Console")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Operations workspace" })).toBeVisible();
  await expect.poll(() => horizontalOverflow(page)).toBeLessThanOrEqual(0);
  await page.screenshot({ path: testInfo.outputPath("console.png"), fullPage: true });

  // Prove the overflow check can fail in this project: twice the layout width stays below the 4x mobile widening cap.
  await page.evaluate(() => {
    const probe = document.createElement("div");
    probe.id = "overflow-self-check";
    probe.style.cssText = `width:${document.documentElement.clientWidth * 2}px;height:1px`;
    document.body.append(probe);
  });
  try {
    expect(await horizontalOverflow(page)).toBeGreaterThan(0);
  } finally {
    await page.evaluate(() => document.getElementById("overflow-self-check")?.remove());
  }
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
  expect({ consoleErrors, pageErrors, failedRequests, httpErrors }).toEqual({
    consoleErrors: [], pageErrors: [], failedRequests: [], httpErrors: [],
  });
});
