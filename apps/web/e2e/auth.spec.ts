import { expect, test } from "@playwright/test";

import { E2E_EMAIL, e2ePassword } from "./helpers";

test.describe("acceso", () => {
  test("no hay registro público", async ({ page }) => {
    await page.goto("/es/login");
    await expect(
      page.getByText("Pide acceso a un administrador"),
    ).toBeVisible();
    await expect(page.getByRole("link", { name: /crear cuenta/i })).toHaveCount(
      0,
    );
    const res = await page.goto("/es/register");
    expect(res?.status()).toBe(404);
  });

  test("contraseña incorrecta", async ({ page }) => {
    await page.goto("/es/login");
    await page.getByLabel("Email").fill(E2E_EMAIL);
    await page.getByLabel("Contraseña").fill("no es la contraseña buena");
    await page.getByRole("button", { name: "Entrar" }).click();
    await expect(
      page.getByRole("alert").filter({ hasText: /incorrect/i }),
    ).toBeVisible();
  });

  test("entrar y salir", async ({ page }) => {
    await page.goto("/es/login");
    await page.getByLabel("Email").fill(E2E_EMAIL);
    await page.getByLabel("Contraseña").fill(e2ePassword());
    await page.getByRole("button", { name: "Entrar" }).click();
    await expect(page.getByText("Hola, Prueba E2E")).toBeVisible();
    // Next mantiene la página de login montada (oculta): la contraseña no debe quedar en ella.
    for (const input of await page.locator("input[type=password]").all()) {
      await expect(input).toHaveValue("");
    }

    await page.getByRole("button", { name: "Cerrar sesión" }).click();
    await expect(page).toHaveURL(/\/es\/login/);
    await page.goto("/es/recordings");
    await expect(page).toHaveURL(/\/es\/login/);
  });
});
