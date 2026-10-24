import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

import { request, type FullConfig } from "@playwright/test";

import { E2E_EMAIL, STATE_FILE } from "./helpers";

const ROOT = path.resolve(__dirname, "../../..");

/** Crea (o actualiza) la cuenta de pruebas e inicia sesión una vez para todas las pruebas. */
export default async function globalSetup(config: FullConfig) {
  const baseURL = config.projects[0].use.baseURL!;
  const password = `e2e ${randomBytes(12).toString("hex")}`;
  const users = (args: string[]) =>
    execFileSync(path.join(ROOT, "scripts/users.sh"), args, {
      input: `${password}\n`,
      stdio: ["pipe", "pipe", "pipe"],
    });
  try {
    users(["create", E2E_EMAIL, "--name", "Prueba E2E", "--password-stdin"]);
  } catch {
    users(["set-password", E2E_EMAIL, "--password-stdin"]);
  }

  const api = await request.newContext({ baseURL });
  const { csrf_token } = await (await api.get("/api/v1/auth/csrf")).json();
  const res = await api.post("/api/v1/auth/login", {
    headers: { "X-CSRF-Token": csrf_token },
    data: { email: E2E_EMAIL, password },
  });
  if (!res.ok())
    throw new Error(`Login E2E: ${res.status()} ${await res.text()}`);
  mkdirSync(path.dirname(STATE_FILE), { recursive: true });
  await api.storageState({ path: STATE_FILE });
  writeFileSync(path.join(path.dirname(STATE_FILE), "password"), password, {
    mode: 0o600,
  });
  await api.dispose();
}
