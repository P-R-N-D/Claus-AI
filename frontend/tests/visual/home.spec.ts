import { expect, test } from "@playwright/test";

test("user and console surfaces render with both backend services", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });

  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Claus" })).toBeVisible();
  await expect(page.getByRole("article", { name: "Core health" }).getByText("Connected", { exact: true })).toBeVisible();
  await expect(page.getByRole("article", { name: "Agent health" }).getByText("Connected", { exact: true })).toBeVisible();
  await expect(page.getByText('"status": "ok"')).toBeVisible();
  await expect(page.getByText('"backend": "django"')).toBeVisible();
  await expect(page.getByText('"api": "drf"')).toBeVisible();
  await expect(page.getByText('"backend": "fastapi"')).toBeVisible();

  const screenshot = await page.screenshot({ fullPage: true });
  expect(screenshot.length).toBeGreaterThan(0);

  await page.goto("/console");
  await expect(page.getByText("Claus Console")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Operations workspace" })).toBeVisible();
  expect((await page.screenshot({ fullPage: true })).length).toBeGreaterThan(0);
  expect(consoleErrors).toEqual([]);
});
