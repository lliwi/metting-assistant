import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SegmentedRecorder, type SegmentReady } from "./segmentedRecorder";

/** MediaRecorder mínimo: emite un fragmento por segundo y uno final al parar. */
class FakeMediaRecorder {
  static instances: FakeMediaRecorder[] = [];
  state: "inactive" | "recording" | "paused" = "inactive";
  mimeType = "audio/webm;codecs=opus";
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: ((e: unknown) => void) | null = null;

  constructor() {
    FakeMediaRecorder.instances.push(this);
  }
  start() {
    this.state = "recording";
  }
  pause() {
    this.state = "paused";
  }
  resume() {
    this.state = "recording";
  }
  stop() {
    this.state = "inactive";
    this.ondataavailable?.({ data: new Blob(["audio"]) });
    queueMicrotask(() => this.onstop?.());
  }
}

describe("SegmentedRecorder", () => {
  let now = 0;
  let segments: SegmentReady[];

  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("MediaRecorder", FakeMediaRecorder);
    FakeMediaRecorder.instances = [];
    now = 0;
    segments = [];
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  function recorder() {
    return new SegmentedRecorder({} as MediaStream, {
      segmentMs: 30_000,
      mimeType: "audio/webm;codecs=opus",
      onSegment: (s) => {
        segments.push(s);
      },
      now: () => now,
    });
  }

  function advance(ms: number) {
    for (let t = 0; t < ms; t += 250) {
      now += 250;
      vi.advanceTimersByTime(250);
    }
  }

  it("rota cada 30 s con un MediaRecorder nuevo y numeración consecutiva", async () => {
    const r = recorder();
    r.start();
    advance(75_000);
    await r.stop();
    await vi.runAllTimersAsync();

    expect(segments.map((s) => s.seq)).toEqual([0, 1, 2]);
    expect(segments.map((s) => s.durationMs)).toEqual([30_000, 30_000, 15_000]);
    expect(FakeMediaRecorder.instances).toHaveLength(3);
    expect(
      FakeMediaRecorder.instances.every((m) => m.state === "inactive"),
    ).toBe(true);
  });

  it("arranca el siguiente antes de detener el anterior (sin hueco)", () => {
    const r = recorder();
    r.start();
    advance(30_000);
    const [first, second] = FakeMediaRecorder.instances;
    expect(second.state).toBe("recording");
    expect(first.state).toBe("inactive");
    void r.stop();
  });

  it("no cuenta el tiempo en pausa", async () => {
    const r = recorder();
    r.start();
    advance(10_000);
    r.pause();
    advance(60_000);
    expect(FakeMediaRecorder.instances).toHaveLength(1);
    r.resume();
    advance(5_000);
    await r.stop();
    await vi.runAllTimersAsync();
    expect(segments).toHaveLength(1);
    expect(segments[0].durationMs).toBe(15_000);
  });

  it("continúa la numeración al reanudar una pista", async () => {
    const r = recorder();
    r.start(7);
    advance(1_000);
    await r.stop();
    await vi.runAllTimersAsync();
    expect(segments[0].seq).toBe(7);
  });
});
