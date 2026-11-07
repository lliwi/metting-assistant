import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api/client";

import {
  getRecorderQueue,
  sendSegment,
  tryComplete,
  uploadTimeoutMs,
} from "./index";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("sendSegment", () => {
  it("corta una subida colgada para que la cola pueda reintentar", async () => {
    vi.useFakeTimers();
    try {
      vi.stubGlobal("document", { cookie: "ma_csrf=token" });
      vi.stubGlobal(
        "fetch",
        (_url: string, init: RequestInit) =>
          new Promise((_resolve, reject) => {
            init.signal?.addEventListener("abort", () =>
              reject(init.signal?.reason),
            );
          }),
      );
      const blob = new Blob(["audio"]);
      const pending = sendSegment({
        id: "s1",
        recordingId: "r1",
        track: 0,
        seq: 0,
        durationMs: 30_000,
        mimeType: "audio/webm",
        sha256: "0".repeat(64),
        blob,
        createdAt: 0,
        status: "pending",
        attempts: 0,
      });
      const outcome = expect(pending).rejects.toThrow("upload_timeout");
      await vi.advanceTimersByTimeAsync(uploadTimeoutMs(blob.size));
      await outcome;
    } finally {
      vi.useRealTimers();
    }
  });

  it("da más margen a los bloques grandes de una importación", () => {
    expect(uploadTimeoutMs(8 * 1024 * 1024)).toBeGreaterThan(300_000);
  });
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

  it("cierra admitiendo huecos cuando ya no queda nada en el dispositivo", async () => {
    const post = vi
      .spyOn(api, "POST")
      .mockResolvedValue({ response: new Response("{}") } as never);
    const { store } = getRecorderQueue();
    await store.ensureTrack("rec-gaps", 0);
    await store.requestFinish("rec-gaps");

    try {
      expect(await tryComplete("rec-gaps")).toEqual({ kind: "done" });
      expect(post.mock.calls[0][1]).toMatchObject({
        body: { tracks: { "0": 0 }, allow_gaps: true },
      });
    } finally {
      post.mockRestore();
    }
  });
});
