import { expect, test } from "@playwright/test";

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

  const docsResponse = await page.request.get("/agent/docs", { maxRedirects: 0 });
  expect(docsResponse.status()).toBe(200);
  const docsRedirect = await page.request.get(`/agent/docs/${query}`, { maxRedirects: 0 });
  expect(docsRedirect.status()).toBe(307);
  const docsLocation = new URL(docsRedirect.headers()["location"], docsRedirect.url());
  expect(`${docsLocation.pathname}${docsLocation.search}`).toBe(`/agent/docs${query}`);

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

  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  await page.screenshot({ path: testInfo.outputPath("home.png"), fullPage: true });

  await Promise.all([
    page.waitForResponse((response) => response.url().endsWith("/core/health/") && response.status() === 200),
    page.waitForResponse((response) => response.url().endsWith("/agent/health/") && response.status() === 200),
    page.getByRole("button", { name: "Retry backend check" }).click(),
  ]);
  await expect(page.getByRole("article", { name: "Core health" }).getByText("Connected", { exact: true })).toBeVisible();
  await expect(page.getByRole("article", { name: "Agent health" }).getByText("Connected", { exact: true })).toBeVisible();

  await page.goto(`/console/${query}`);
  await expect(page).toHaveURL(new URL(`/console${query}`, page.url()).href);
  await expect(page.getByText("Claus Console")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Operations workspace" })).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  await page.screenshot({ path: testInfo.outputPath("console.png"), fullPage: true });
  expect({ consoleErrors, pageErrors, failedRequests, httpErrors }).toEqual({
    consoleErrors: [], pageErrors: [], failedRequests: [], httpErrors: [],
  });
});
