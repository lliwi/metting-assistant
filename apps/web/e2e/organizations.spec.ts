import { expect, test } from "@playwright/test";

import { STATE_FILE, unique } from "./helpers";

test.use({ storageState: STATE_FILE });

test("crear y eliminar una organización", async ({ page }) => {
  const name = unique("E2E org");
  await page.goto("/es/organizations");
  await page.getByLabel("Nombre de la organización").fill(name);
  await page.getByRole("button", { name: "Crear organización" }).click();
  await page.getByRole("link", { name }).click();
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Consumo de IA" }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Eliminar organización" }).click();
  const submit = page.getByRole("button", { name: "Eliminar definitivamente" });
  await expect(submit).toBeDisabled();
  await page.getByLabel(`Escribe «${name}» para confirmar`).fill(name);
  await submit.click();
  await expect(page).toHaveURL(/\/es\/organizations$/);
  await expect(page.getByRole("link", { name })).toHaveCount(0);
});
