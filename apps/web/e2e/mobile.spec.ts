import { expect, test } from "@playwright/test";

import { STATE_FILE } from "./helpers";

test.use({ storageState: STATE_FILE });

test("en el móvil no hay desplazamiento horizontal", async ({ page }) => {
  for (const path of [
    "/es/recordings",
    "/es/record",
    "/es/organizations",
    "/es/account",
  ]) {
    await page.goto(path);
    await expect(page.locator("main")).toBeVisible();
    const overflow = await page.evaluate(
      () =>
        document.documentElement.scrollWidth -
        document.documentElement.clientWidth,
    );
    expect(overflow, path).toBeLessThanOrEqual(0);
  }
});
