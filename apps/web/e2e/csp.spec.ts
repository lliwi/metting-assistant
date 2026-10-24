import { expect, test } from "@playwright/test";

import { STATE_FILE } from "./helpers";

test.use({ storageState: STATE_FILE });

test("la CSP no bloquea nada de la aplicación", async ({ page }) => {
  const violations: string[] = [];
  page.on("console", (msg) => {
    if (/Content Security Policy|Refused to/i.test(msg.text())) {
      violations.push(msg.text());
    }
  });
  const res = await page.goto("/es/recordings");
  expect(res?.headers()["content-security-policy"]).toContain(
    "default-src 'self'",
  );
  for (const path of [
    "/es/record",
    "/es/import",
    "/es/organizations",
    "/es/account",
  ]) {
    await page.goto(path);
    await expect(page.locator("main")).toBeVisible();
  }
  await page.goto("/es/recordings");
  await page.locator("main a[href*='/es/recordings/']").first().click();
  await expect(page.locator("audio")).toBeVisible();
  await page
    .locator("audio")
    .evaluate((a: HTMLAudioElement) => a.play().catch(() => {}));
  await page.waitForTimeout(1_500);
  expect(violations).toEqual([]);
});
