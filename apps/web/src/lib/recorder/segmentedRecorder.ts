/**
 * Graba un MediaStream en segmentos independientes.
 *
 * Con `MediaRecorder.start(timeslice)` solo el primer fragmento lleva la cabecera del
 * contenedor: los demás no se pueden decodificar por separado. Por eso cada segmento usa
 * un MediaRecorder nuevo. El siguiente se arranca antes de detener el anterior para no
 * dejar huecos (a costa de un solapamiento de milisegundos).
 */

export type SegmentReady = {
  seq: number;
  blob: Blob;
  durationMs: number;
  mimeType: string;
};

export type RecorderOptions = {
  segmentMs: number;
  mimeType: string;
  audioBitsPerSecond?: number;
  onSegment: (segment: SegmentReady) => void | Promise<void>;
  onError?: (error: unknown) => void;
  /** Fuente de tiempo inyectable en tests. */
  now?: () => number;
};

type Active = {
  recorder: MediaRecorder;
  chunks: Blob[];
  seq: number;
  activeMs: number; // tiempo grabado, sin pausas
  resumedAt: number | null;
  done: Promise<void>;
};

const PREFERRED_TYPES = [
  "audio/webm;codecs=opus",
  "audio/ogg;codecs=opus",
  "audio/mp4;codecs=mp4a.40.2", // Safari
  "audio/mp4",
  "audio/webm",
];

export function pickMimeType(): string {
  if (typeof MediaRecorder === "undefined") return "";
  return PREFERRED_TYPES.find((t) => MediaRecorder.isTypeSupported(t)) ?? "";
}

export class SegmentedRecorder {
  private current: Active | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private nextSeq = 0;
  private paused = false;
  private pendingStops: Promise<void>[] = [];
  private readonly now: () => number;

  constructor(
    private readonly stream: MediaStream,
    private readonly options: RecorderOptions,
  ) {
    this.now = options.now ?? (() => performance.now());
  }

  get isPaused(): boolean {
    return this.paused;
  }

  start(startSeq = 0): void {
    this.nextSeq = startSeq;
    this.current = this.open();
    this.timer = setInterval(() => this.tick(), 250);
  }

  pause(): void {
    const c = this.current;
    if (!c || this.paused) return;
    this.accumulate(c);
    c.resumedAt = null;
    c.recorder.pause();
    this.paused = true;
  }

  resume(): void {
    const c = this.current;
    if (!c || !this.paused) return;
    c.recorder.resume();
    c.resumedAt = this.now();
    this.paused = false;
  }

  /** Cierra el segmento en curso y espera a que todos los segmentos se hayan entregado. */
  async stop(): Promise<void> {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    const c = this.current;
    this.current = null;
    if (c) this.close(c);
    await Promise.all(this.pendingStops);
  }

  /** Milisegundos grabados en el segmento actual (para el indicador de la interfaz). */
  currentSegmentMs(): number {
    const c = this.current;
    if (!c) return 0;
    return c.activeMs + (c.resumedAt !== null ? this.now() - c.resumedAt : 0);
  }

  private open(): Active {
    const recorder = new MediaRecorder(this.stream, {
      mimeType: this.options.mimeType || undefined,
      audioBitsPerSecond: this.options.audioBitsPerSecond ?? 32_000,
    });
    const active: Active = {
      recorder,
      chunks: [],
      seq: this.nextSeq++,
      activeMs: 0,
      resumedAt: this.now(),
      done: Promise.resolve(),
    };
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) active.chunks.push(event.data);
    };
    recorder.onerror = (event) => this.options.onError?.(event);
    active.done = new Promise<void>((resolve) => {
      recorder.onstop = () => {
        const mimeType = recorder.mimeType || this.options.mimeType;
        const blob = new Blob(active.chunks, { type: mimeType });
        Promise.resolve(
          blob.size > 0
            ? this.options.onSegment({
                seq: active.seq,
                blob,
                durationMs: Math.round(active.activeMs),
                mimeType,
              })
            : undefined,
        )
          .catch((error: unknown) => this.options.onError?.(error))
          .finally(resolve);
      };
    });
    // Fragmentos de 1 s dentro del mismo segmento: si la pestaña se cierra, se pierde
    // como mucho el segmento en curso, nunca los ya entregados.
    recorder.start(1_000);
    return active;
  }

  private accumulate(c: Active): void {
    if (c.resumedAt !== null) {
      c.activeMs += this.now() - c.resumedAt;
      c.resumedAt = this.now();
    }
  }

  private close(c: Active): void {
    this.accumulate(c);
    c.resumedAt = null;
    if (c.recorder.state !== "inactive") c.recorder.stop();
    this.pendingStops.push(c.done);
    void c.done.then(() => {
      this.pendingStops = this.pendingStops.filter((p) => p !== c.done);
    });
  }

  private tick(): void {
    const c = this.current;
    if (!c || this.paused) return;
    if (this.currentSegmentMs() < this.options.segmentMs) return;
    // Primero el siguiente, luego cerrar el actual: sin hueco entre segmentos.
    this.current = this.open();
    this.close(c);
  }
}
