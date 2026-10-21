import type { SegmentStore, StoredSegment } from "./segmentStore";

/** Resultado de un intento de subida, decidido a partir de la respuesta HTTP. */
export type SendOutcome =
  | { kind: "ok" }
  | { kind: "retry"; reason: string }
  | { kind: "auth" } // sesión caducada: esperar a que el usuario vuelva a entrar
  | { kind: "fatal"; reason: string }; // el servidor nunca lo aceptará (p. ej. captura cerrada)

export type SendFn = (segment: StoredSegment) => Promise<SendOutcome>;

export type UploaderStats = {
  pending: number;
  failed: number;
  uploading: boolean;
  waitingForAuth: boolean;
  lastError: string | null;
  /** Motivo del primer segmento fallido (persistido: sobrevive a recargas). */
  failedReason: string | null;
  /** Segmentos pendientes de subir por grabación (los fallidos se cuentan aparte). */
  localByRecording: Record<string, number>;
};

type Options = {
  baseDelayMs?: number;
  maxDelayMs?: number;
  /** Inyectable en tests. Debe poder interrumpirse con `signal`. */
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
  random?: () => number;
};

const defaultSleep = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      clearTimeout(timer);
      resolve();
    });
  });

/** Clasifica la respuesta de POST /segments. */
export async function classifyResponse(
  response: Response,
): Promise<SendOutcome> {
  if (response.ok) return { kind: "ok" };
  if (response.status === 401) return { kind: "auth" };
  let code = `http_${response.status}`;
  try {
    const body = (await response.json()) as { error?: { code?: string } };
    code = body.error?.code ?? code;
  } catch {
    // cuerpo no JSON (p. ej. 502 del proxy)
  }
  const transient =
    response.status >= 500 ||
    response.status === 408 ||
    response.status === 429 ||
    code === "checksum_mismatch" || // corrupción en tránsito: reenviar
    code === "csrf_failed"; // token rotado en otra pestaña: se relee en el reintento
  return transient
    ? { kind: "retry", reason: code }
    : { kind: "fatal", reason: code };
}

/**
 * Sube en serie, en orden de grabación, los segmentos guardados en IndexedDB.
 * Un segmento solo se borra del dispositivo cuando el servidor lo confirma.
 */
export class SegmentUploader {
  private running = false;
  private wake = new AbortController();
  private attempt = 0;
  private waitingForAuth = false;
  private uploading = false;
  private lastError: string | null = null;
  private listeners = new Set<(stats: UploaderStats) => void>();
  private readonly baseDelayMs: number;
  private readonly maxDelayMs: number;
  private readonly sleep: NonNullable<Options["sleep"]>;
  private readonly random: () => number;

  constructor(
    private readonly store: SegmentStore,
    private readonly send: SendFn,
    options: Options = {},
  ) {
    this.baseDelayMs = options.baseDelayMs ?? 1_000;
    this.maxDelayMs = options.maxDelayMs ?? 60_000;
    this.sleep = options.sleep ?? defaultSleep;
    this.random = options.random ?? Math.random;
  }

  subscribe(listener: (stats: UploaderStats) => void): () => void {
    this.listeners.add(listener);
    void this.emit();
    return () => this.listeners.delete(listener);
  }

  /** Arranca el bucle (idempotente). Devuelve una promesa que termina al detenerlo. */
  start(): Promise<void> {
    if (this.running) return Promise.resolve();
    this.running = true;
    return this.loop();
  }

  stop(): void {
    this.running = false;
    this.notify();
  }

  /** Despierta el bucle: nuevo segmento, red recuperada o sesión renovada. */
  notify(options: { authRestored?: boolean } = {}): void {
    if (options.authRestored) this.waitingForAuth = false;
    this.attempt = 0;
    this.wake.abort();
  }

  /** Reintenta los segmentos marcados como fallidos (acción explícita del usuario). */
  async retryFailed(): Promise<void> {
    for (const segment of await this.store.listAll()) {
      if (segment.status === "failed") {
        await this.store.update({
          ...segment,
          status: "pending",
          error: undefined,
        });
      }
    }
    this.notify();
  }

  async stats(): Promise<UploaderStats> {
    const all = await this.store.listAll();
    const localByRecording: Record<string, number> = {};
    const failed = all.filter((s) => s.status === "failed");
    for (const s of all) {
      if (s.status === "pending") {
        localByRecording[s.recordingId] =
          (localByRecording[s.recordingId] ?? 0) + 1;
      }
    }
    return {
      pending: all.length - failed.length,
      failed: failed.length,
      uploading: this.uploading,
      waitingForAuth: this.waitingForAuth,
      lastError: this.lastError,
      failedReason: failed[0]?.error ?? null,
      localByRecording,
    };
  }

  private async emit(): Promise<void> {
    if (this.listeners.size === 0) return;
    const stats = await this.stats();
    for (const listener of this.listeners) listener(stats);
  }

  private async idle(ms: number): Promise<void> {
    this.wake = new AbortController();
    await this.sleep(ms, this.wake.signal);
  }

  private backoff(): number {
    const exp = Math.min(this.maxDelayMs, this.baseDelayMs * 2 ** this.attempt);
    // Jitter: evita que muchos clientes reintenten a la vez tras una caída del servidor.
    return Math.round(exp * (0.5 + this.random() * 0.5));
  }

  private async loop(): Promise<void> {
    while (this.running) {
      if (this.waitingForAuth) {
        await this.idle(this.maxDelayMs);
        continue;
      }
      const segment = await this.store.nextPending();
      if (!segment) {
        await this.emit();
        await this.idle(this.maxDelayMs);
        continue;
      }

      this.uploading = true;
      await this.emit();
      let outcome: SendOutcome;
      try {
        outcome = await this.send(segment);
      } catch (error) {
        outcome = {
          kind: "retry",
          reason: error instanceof Error ? error.message : "network",
        };
      }
      this.uploading = false;

      switch (outcome.kind) {
        case "ok":
          await this.store.delete(segment.id);
          this.attempt = 0;
          this.lastError = null;
          break;
        case "retry":
          this.lastError = outcome.reason;
          await this.store.update({
            ...segment,
            attempts: segment.attempts + 1,
          });
          await this.emit();
          await this.idle(this.backoff());
          this.attempt += 1;
          break;
        case "auth":
          this.waitingForAuth = true;
          this.lastError = "unauthenticated";
          break;
        case "fatal":
          // Se conserva en el dispositivo: el usuario decide (reintentar o descartar).
          this.lastError = outcome.reason;
          await this.store.update({
            ...segment,
            status: "failed",
            error: outcome.reason,
            attempts: segment.attempts + 1,
          });
          break;
      }
      await this.emit();
    }
  }
}
