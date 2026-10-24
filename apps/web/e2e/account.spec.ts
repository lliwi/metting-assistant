import { expect, test } from "@playwright/test";

import { e2ePassword, setE2ePassword, STATE_FILE } from "./helpers";

test.use({ storageState: STATE_FILE });

test("cambiar la contraseña desde Mi cuenta", async ({ page }) => {
  const current = e2ePassword();
  const next = `${current} nueva`;
  await page.goto("/es/account");

  const fill = async (from: string, to: string, repeat: string) => {
    await page.getByLabel("Contraseña actual").fill(from);
    await page.getByLabel("Contraseña nueva", { exact: true }).fill(to);
    await page.getByLabel("Repite la contraseña nueva").fill(repeat);
    await page.getByRole("button", { name: "Cambiar contraseña" }).click();
  };

  await fill(current, next, `${next}x`);
  await expect(
    page.getByText("Las contraseñas nuevas no coinciden."),
  ).toBeVisible();

  // Un solo cambio por ejecución (la API limita a 5 cada 15 minutos). La sesión actual sigue
  // abierta; la contraseña nueva queda para las pruebas siguientes.
  await fill(current, next, next);
  await expect(
    page.getByText("Contraseña cambiada.", { exact: false }),
  ).toBeVisible();
  setE2ePassword(next);
});
