import { expect, test } from "@playwright/test";

import {
  FIXTURE_AUDIO,
  openRecording,
  STATE_FILE,
  unique,
  waitTranscribed,
} from "./helpers";

test.use({ storageState: STATE_FILE });

test.describe.serial("grabaciones", () => {
  const title = unique("E2E importación");

  test("importar un audio y transcribirlo", async ({ page }) => {
    await page.goto("/es/import");
    await page.getByLabel("Archivo").setInputFiles(FIXTURE_AUDIO);
    await page.getByLabel("Título").fill(title);
    await page.getByLabel("Idioma del audio").selectOption("es");
    await page.getByLabel(/Confirmo que cuento con la autorización/).check();
    await page.getByRole("button", { name: "Importar" }).click();
    await expect(page.getByText("Archivo importado")).toBeVisible({
      timeout: 60_000,
    });

    await openRecording(page, title);
    await waitTranscribed(page);
    const transcript = page.getByRole("region", { name: "Transcripción" });
    await expect(transcript).toContainText(/Marta/);
    await expect(page.locator("audio")).toBeVisible();
  });

  test("buscar por el texto de la transcripción", async ({ page }) => {
    await page.goto("/es/recordings");
    await page
      .getByRole("searchbox", { name: "Buscar en las grabaciones" })
      .fill("semanal");
    await page.getByRole("button", { name: "Buscar" }).click();
    await expect(page.getByRole("link", { name: title })).toBeVisible();
    await expect(
      page
        .locator("mark")
        .filter({ hasText: /semanal/i })
        .first(),
    ).toBeVisible();
  });

  test("corregir una frase queda en el historial", async ({ page }) => {
    await openRecording(page, title);
    await page.getByRole("button", { name: /Editar frase de 00:00/ }).click();
    const text = page.getByLabel("Texto");
    await text.fill(`${await text.inputValue()} (revisado)`);
    await page.getByRole("button", { name: "Guardar", exact: true }).click();
    await expect(
      page.getByRole("region", { name: "Transcripción" }),
    ).toContainText("(revisado)");
    await page.getByText("Historial de cambios").click();
    await expect(page.locator(".history-item ins").first()).toContainText(
      "(revisado)",
    );
  });

  test("exportar a Markdown y PDF", async ({ page }) => {
    await openRecording(page, title);
    const md = page.waitForEvent("download");
    await page.getByRole("link", { name: "Markdown" }).click();
    const mdFile = await md;
    expect(mdFile.suggestedFilename()).toMatch(/^e2e-importacion.*\.md$/);

    const pdf = await page.request.get(
      (await page.getByRole("link", { name: "PDF" }).getAttribute("href"))!,
    );
    expect(pdf.headers()["content-type"]).toBe("application/pdf");
    expect((await pdf.body()).subarray(0, 4).toString()).toBe("%PDF");
  });

  test("eliminar la grabación", async ({ page }) => {
    await openRecording(page, title);
    await page.getByRole("button", { name: "Eliminar grabación" }).click();
    await expect(page.getByText(/No se puede deshacer/)).toBeVisible();
    await page.getByRole("button", { name: "Eliminar definitivamente" }).click();
    await expect(page).toHaveURL(/\/es\/recordings$/);
    await expect(page.getByRole("link", { name: title })).toHaveCount(0);
  });

  test("grabar con el micrófono", async ({ page }) => {
    const recTitle = unique("E2E grabación");
    await page.goto("/es/record");
    await page.getByLabel("Título").fill(recTitle);
    await page.getByLabel(/Confirmo que cuento con la autorización/).check();
    await page.getByRole("button", { name: "Empezar a grabar" }).click();
    await expect(page.getByText("Grabando", { exact: true })).toBeVisible();
    await page.waitForTimeout(4_000);
    await page.getByRole("button", { name: "Finalizar" }).click();
    await expect(
      page.getByRole("status").filter({ hasText: /grabación/i }),
    ).toBeVisible({
      timeout: 60_000,
    });

    await openRecording(page, recTitle);
    // El micrófono simulado emite un tono: basta con que la captura se cierre y se procese.
    await expect(
      page.getByText(/^(Pendiente|Procesando|Transcrita|Error)/).first(),
    ).toBeVisible({ timeout: 60_000 });
  });
});
