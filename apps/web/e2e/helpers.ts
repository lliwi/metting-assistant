import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

import { expect, type Page } from "@playwright/test";

export const E2E_EMAIL = "playwright.e2e@example.com";
export const STATE_FILE = path.join(__dirname, ".auth/state.json");
export const FIXTURE_AUDIO = path.join(__dirname, "fixtures/reunion-corta.ogg");

const PASSWORD_FILE = path.join(path.dirname(STATE_FILE), "password");

/** Contraseña actual de la cuenta de pruebas (la fija global-setup; puede cambiarla una prueba). */
export function e2ePassword(): string {
  return readFileSync(PASSWORD_FILE, "utf8");
}

export function setE2ePassword(password: string) {
  writeFileSync(PASSWORD_FILE, password, { mode: 0o600 });
}

export function unique(prefix: string): string {
  return `${prefix} ${new Date().toISOString().slice(0, 19).replace("T", " ")}`;
}

/** Espera a que la grabación quede transcrita (la página se actualiza por SSE). */
export async function waitTranscribed(page: Page) {
  await expect(
    page.locator("main p .badge").filter({ hasText: /^Transcrita$/ }),
  ).toBeVisible({ timeout: 150_000 });
}

/** Abre una grabación desde la lista por su título. */
export async function openRecording(page: Page, title: string) {
  await page.goto("/es/recordings");
  await page.getByRole("link", { name: title }).click();
  await expect(page).toHaveURL(/\/es\/recordings\/[0-9a-f-]{36}/);
  await expect(
    page.getByRole("heading", { level: 1, name: title }),
  ).toBeVisible();
}
