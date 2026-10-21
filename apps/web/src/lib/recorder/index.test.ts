import { afterEach, describe, expect, it, vi } from "vitest";

import { getRecorderQueue, tryComplete } from "./index";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("tryComplete", () => {
  it("no cierra una captura local que el usuario no ha finalizado", async () => {
    // Regresión: al pulsar "Finalizar" la cola podía estar vacía un instante antes de
    // recibir el último segmento, y la captura se cerraba sin él.
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const { store } = getRecorderQueue();
    await store.ensureTrack("rec-open", 0);

    expect(await tryComplete("rec-open")).toEqual({ kind: "not_requested" });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("espera mientras queden segmentos en el dispositivo", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const { store } = getRecorderQueue();
    await store.addSegment({
      id: "s1",
      recordingId: "rec-busy",
      track: 0,
      seq: 0,
      durationMs: 1000,
      mimeType: "audio/webm",
      sha256: "0".repeat(64),
      blob: new Blob(["x"]),
      createdAt: 1,
      status: "pending",
      attempts: 0,
    });
    await store.requestFinish("rec-busy");

    expect(await tryComplete("rec-busy")).toEqual({
      kind: "waiting",
      local: 1,
    });
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
