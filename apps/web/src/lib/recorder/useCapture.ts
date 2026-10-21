"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { getRecorderQueue, sha256Hex } from "./index";
import { pickMimeType, SegmentedRecorder } from "./segmentedRecorder";

export const SEGMENT_MS = 30_000;

export type CapturePhase =
  "recording" | "paused" | "stopping" | "stopped" | "interrupted";

type Params = {
  stream: MediaStream;
  recordingId: string;
  track: number;
};

/** Ciclo de vida de una pista de grabación: grabadora, nivel, wake lock y cola local. */
export function useCapture({ stream, recordingId, track }: Params) {
  const [phase, setPhase] = useState<CapturePhase>("recording");
  const [elapsedMs, setElapsedMs] = useState(0);
  const [level, setLevel] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const recorderRef = useRef<SegmentedRecorder | null>(null);
  const savedMsRef = useRef(0);

  // Grabadora: cada segmento se persiste en IndexedDB antes de intentar subirlo.
  useEffect(() => {
    const { store, uploader } = getRecorderQueue();
    const recorder = new SegmentedRecorder(stream, {
      segmentMs: SEGMENT_MS,
      mimeType: pickMimeType(),
      onSegment: async ({ seq, blob, durationMs, mimeType }) => {
        await store.addSegment({
          id: crypto.randomUUID(),
          recordingId,
          track,
          seq,
          durationMs,
          mimeType,
          sha256: await sha256Hex(blob),
          blob,
          createdAt: Date.now(),
          status: "pending",
          attempts: 0,
        });
        savedMsRef.current += durationMs;
        uploader.notify();
      },
      onError: (e) =>
        setError(e instanceof Error ? e.message : "recorder_error"),
    });
    recorderRef.current = recorder;
    recorder.start(0);

    const audioTrack = stream.getAudioTracks()[0];
    const onEnded = () => {
      setPhase("interrupted");
      void recorder.stop();
    };
    audioTrack?.addEventListener("ended", onEnded);

    const tick = setInterval(() => {
      setElapsedMs(savedMsRef.current + recorder.currentSegmentMs());
    }, 500);

    return () => {
      clearInterval(tick);
      audioTrack?.removeEventListener("ended", onEnded);
      void recorder.stop();
      stream.getTracks().forEach((t) => t.stop());
    };
  }, [stream, recordingId, track]);

  // Nivel de entrada (RMS), ~10 actualizaciones por segundo.
  useEffect(() => {
    const context = new AudioContext();
    const analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    context.createMediaStreamSource(stream).connect(analyser);
    const samples = new Float32Array(analyser.fftSize);
    const interval = setInterval(() => {
      analyser.getFloatTimeDomainData(samples);
      let sum = 0;
      for (const v of samples) sum += v * v;
      setLevel(Math.min(1, Math.sqrt(sum / samples.length) * 4));
    }, 100);
    return () => {
      clearInterval(interval);
      void context.close();
    };
  }, [stream]);

  // Evita que la pantalla se bloquee (y la página se suspenda) mientras se graba.
  useEffect(() => {
    if (phase !== "recording" || !("wakeLock" in navigator)) return;
    let lock: WakeLockSentinel | null = null;
    const acquire = () => {
      void navigator.wakeLock.request("screen").then(
        (l) => (lock = l),
        () => undefined,
      );
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") acquire();
    };
    acquire();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      void lock?.release();
    };
  }, [phase]);

  // Aviso al cerrar la pestaña con la grabación en marcha.
  useEffect(() => {
    if (phase !== "recording" && phase !== "paused") return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [phase]);

  const pause = useCallback(() => {
    recorderRef.current?.pause();
    setPhase("paused");
  }, []);

  const resume = useCallback(() => {
    recorderRef.current?.resume();
    setPhase("recording");
  }, []);

  /** Cierra el último segmento (queda guardado en el dispositivo) y libera el micrófono. */
  const stop = useCallback(async () => {
    setPhase("stopping");
    await recorderRef.current?.stop();
    stream.getTracks().forEach((t) => t.stop());
    setPhase("stopped");
  }, [stream]);

  return { phase, elapsedMs, level, error, pause, resume, stop };
}

export function formatDuration(ms: number): string {
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}
