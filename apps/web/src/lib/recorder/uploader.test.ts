import { beforeEach, describe, expect, it } from "vitest";

import { SegmentStore, type StoredSegment } from "./segmentStore";
import {
  classifyResponse,
  SegmentUploader,
  type SendOutcome,
} from "./uploader";

let dbCounter = 0;
let store: SegmentStore;

beforeEach(() => {
  store = new SegmentStore(`test-${++dbCounter}`);
});

function segment(seq: number, recordingId = "rec-1", track = 0): StoredSegment {
  return {
    id: `${recordingId}-${track}-${seq}`,
    recordingId,
    track,
    seq,
    durationMs: 30_000,
    mimeType: "audio/webm",
    sha256: "0".repeat(64),
    blob: new Blob([new Uint8Array([seq])], { type: "audio/webm" }),
    createdAt: seq,
    status: "pending",
    attempts: 0,
  };
}

/** Reloj virtual: los "sleep" del uploader avanzan este reloj en lugar de esperar. */
function virtualClock() {
  const clock = { now: 0, sleeps: [] as number[] };
  const sleep = async (ms: number, signal: AbortSignal) => {
    clock.sleeps.push(ms);
    if (!signal.aborted) clock.now += ms;
    // Cede una macrotarea, como haría un temporizador real.
    await new Promise((r) => setTimeout(r, 0));
  };
  return { clock, sleep };
}

async function runUntil(
  uploader: SegmentUploader,
  condition: () => Promise<boolean>,
) {
  const loop = uploader.start();
  for (let i = 0; i < 1000 && !(await condition()); i++) {
    await new Promise((r) => setTimeout(r, 0));
  }
  uploader.stop();
  await loop;
}

describe("SegmentStore", () => {
  it("cuenta los segmentos producidos por pista", async () => {
    await store.addSegment(segment(0));
    await store.addSegment(segment(1));
    await store.addSegment(segment(0, "rec-1", 1));
    const capture = await store.getCapture("rec-1");
    expect(capture?.tracks).toEqual({ 0: 2, 1: 1 });
    expect(capture?.finishRequested).toBe(false);
  });

  it("devuelve primero el segmento más antiguo pendiente", async () => {
    await store.addSegment(segment(2));
    await store.addSegment(segment(0));
    await store.addSegment(segment(1));
    await store.update({ ...segment(0), status: "failed" });
    expect((await store.nextPending())?.seq).toBe(1);
  });
});

describe("SegmentUploader", () => {
  it("sube en orden y borra cada segmento solo tras confirmación", async () => {
    for (const seq of [0, 1, 2]) await store.addSegment(segment(seq));
    const sent: number[] = [];
    const uploader = new SegmentUploader(
      store,
      async (s) => {
        sent.push(s.seq);
        // En el momento del envío el segmento sigue guardado en el dispositivo.
        expect((await store.listAll()).some((x) => x.id === s.id)).toBe(true);
        return { kind: "ok" };
      },
      virtualClock(),
    );
    await runUntil(uploader, async () => (await store.listAll()).length === 0);
    expect(sent).toEqual([0, 1, 2]);
  });

  it("no pierde nada con 5 minutos sin red y sincroniza al volver", async () => {
    const { clock, sleep } = virtualClock();
    const outageMs = 5 * 60_000;
    let online = false;
    const delivered: number[] = [];

    // 10 segmentos de 30 s grabados durante el corte.
    for (let seq = 0; seq < 10; seq++) await store.addSegment(segment(seq));

    const uploader = new SegmentUploader(
      store,
      async (s): Promise<SendOutcome> => {
        if (clock.now >= outageMs) online = true;
        if (!online) throw new TypeError("Failed to fetch");
        delivered.push(s.seq);
        return { kind: "ok" };
      },
      { sleep, random: () => 1 },
    );
    await runUntil(uploader, async () => (await store.listAll()).length === 0);

    expect(delivered).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
    // Backoff exponencial acotado a 60 s mientras no hay red.
    expect(Math.max(...clock.sleeps)).toBe(60_000);
    expect(clock.sleeps.slice(0, 4)).toEqual([1_000, 2_000, 4_000, 8_000]);
  });

  it("vuelve a intentar enseguida cuando se le notifica (evento online)", async () => {
    await store.addSegment(segment(0));
    let calls = 0;
    const sleeps: number[] = [];
    const uploader: SegmentUploader = new SegmentUploader(
      store,
      async () => {
        calls += 1;
        return calls === 1
          ? { kind: "retry", reason: "network" }
          : { kind: "ok" };
      },
      {
        sleep: async (ms, signal) => {
          sleeps.push(ms);
          uploader.notify(); // la red vuelve mientras espera
          expect(signal.aborted).toBe(true);
        },
        random: () => 1,
      },
    );
    await runUntil(uploader, async () => (await store.listAll()).length === 0);
    expect(calls).toBe(2);
  });

  it("marca como fallido lo que el servidor no aceptará y sigue con el resto", async () => {
    await store.addSegment(segment(0));
    await store.addSegment(segment(1));
    const uploader = new SegmentUploader(
      store,
      async (s) =>
        s.seq === 0
          ? { kind: "fatal", reason: "recording_closed" }
          : { kind: "ok" },
      virtualClock(),
    );
    await runUntil(
      uploader,
      async () => (await store.nextPending()) === undefined,
    );
    const left = await store.listAll();
    expect(left.map((s) => [s.seq, s.status, s.error])).toEqual([
      [0, "failed", "recording_closed"],
    ]);

    await uploader.retryFailed();
    expect((await store.nextPending())?.seq).toBe(0);
  });

  it("se detiene con sesión caducada y continúa al renovarla", async () => {
    await store.addSegment(segment(0));
    let authed = false;
    const uploader = new SegmentUploader(
      store,
      async () => (authed ? { kind: "ok" } : { kind: "auth" }),
      virtualClock(),
    );
    const loop = uploader.start();
    for (let i = 0; i < 50 && !(await uploader.stats()).waitingForAuth; i++) {
      await new Promise((r) => setTimeout(r, 0));
    }
    expect((await uploader.stats()).waitingForAuth).toBe(true);
    expect(await store.listAll()).toHaveLength(1);

    authed = true;
    uploader.notify({ authRestored: true });
    for (let i = 0; i < 200 && (await store.listAll()).length > 0; i++) {
      await new Promise((r) => setTimeout(r, 0));
    }
    uploader.stop();
    await loop;
    expect(await store.listAll()).toHaveLength(0);
  });
});

describe("classifyResponse", () => {
  const json = (status: number, code?: string) =>
    new Response(code ? JSON.stringify({ error: { code } }) : "x", { status });

  it.each([
    [json(201), "ok"],
    [json(200), "ok"],
    [json(401, "unauthenticated"), "auth"],
    [json(502), "retry"],
    [json(429, "rate_limited"), "retry"],
    [json(422, "checksum_mismatch"), "retry"],
    [json(403, "csrf_failed"), "retry"],
    [json(409, "recording_closed"), "fatal"],
    [json(415, "unsupported_media"), "fatal"],
    [json(404, "not_found"), "fatal"],
  ])("%#: clasifica la respuesta", async (response, kind) => {
    expect((await classifyResponse(response)).kind).toBe(kind);
  });
});
