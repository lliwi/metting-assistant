import { defineConfig, devices } from "@playwright/test";

/**
 * Pruebas E2E contra la aplicación desplegada (por defecto, el entorno de desarrollo de
 * Docker Compose). El usuario de pruebas se crea con scripts/users.sh en global-setup.
 *
 *   npm run e2e                       # http://localhost:8080
 *   E2E_BASE_URL=https://… npm run e2e
 *
 * Las pruebas de importación y grabación transcriben audio con el motor configurado (con
 * OpenAI, ~0,01 $ por ejecución).
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never", outputFolder: "e2e-report" }]],
  outputDir: "e2e-results",
  globalSetup: "./e2e/global-setup.ts",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:8080",
    locale: "es-ES",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      testIgnore: /mobile\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        // Micrófono simulado (tono) y permiso concedido sin diálogo.
        permissions: ["microphone"],
        launchOptions: {
          args: [
            "--use-fake-device-for-media-stream",
            "--use-fake-ui-for-media-stream",
          ],
        },
      },
    },
    {
      name: "mobile",
      use: { ...devices["Pixel 7"] },
      testMatch: /mobile\.spec\.ts/,
    },
  ],
});
