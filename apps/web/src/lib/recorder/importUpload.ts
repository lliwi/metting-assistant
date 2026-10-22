"use client";

import { sendSegment, sha256Hex } from "./index";

export const IMPORT_CHUNK_BYTES = 8 * 1024 * 1024;
export const IMPORT_MAX_BYTES = 2 * 1024 ** 3;

export class ImportUploadError extends Error {
  constructor(public readonly code: string) {
    super(code);
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * Sube un archivo en bloques por el endpoint de segmentos. Cada bloque conserva su id entre
 * reintentos, así que un reenvío tras perder la respuesta es idempotente en el servidor.
 * No usa IndexedDB: el archivo sigue en el disco del usuario si hay que repetir.
 */
export async function uploadFileInChunks(
  file: File,
  recordingId: string,
  onProgress: (uploadedBytes: number) => void,
): Promise<number> {
  const total = Math.max(1, Math.ceil(file.size / IMPORT_CHUNK_BYTES));
  let uploaded = 0;
  for (let seq = 0; seq < total; seq++) {
    const blob = file.slice(seq * IMPORT_CHUNK_BYTES, (seq + 1) * IMPORT_CHUNK_BYTES);
    const chunk = {
      id: crypto.randomUUID(),
      recordingId,
      track: 0,
      seq,
      durationMs: 0,
      mimeType: file.type || "application/octet-stream",
      sha256: await sha256Hex(blob),
      blob,
      createdAt: Date.now(),
      status: "pending" as const,
      attempts: 0,
    };
    for (let attempt = 0; ; attempt++) {
      let outcome;
      try {
        outcome = await sendSegment(chunk);
      } catch {
        outcome = { kind: "retry" as const, reason: "network" };
      }
      if (outcome.kind === "ok") break;
      if (outcome.kind === "auth") throw new ImportUploadError("unauthenticated");
      if (outcome.kind === "fatal") throw new ImportUploadError(outcome.reason);
      await sleep(Math.min(60_000, 1_000 * 2 ** attempt));
    }
    uploaded += blob.size;
    onProgress(uploaded);
  }
  return total;
}
