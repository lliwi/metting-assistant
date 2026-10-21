"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import type { Dictionary, Locale } from "@/i18n/client";
import {
  getRecorderQueue,
  tryComplete,
  type CompleteResult,
} from "@/lib/recorder";
import { formatDuration, useCapture } from "@/lib/recorder/useCapture";
import { useUploaderStats } from "@/lib/recorder/useUploaderStats";

import { UploadStatus } from "./UploadStatus";

type Props = {
  dict: Dictionary;
  lang: Locale;
  stream: MediaStream;
  recordingId: string;
  track: number;
  title: string;
  onResume: () => void;
};

export function RecorderView({
  dict,
  lang,
  stream,
  recordingId,
  track,
  title,
  onResume,
}: Props) {
  const capture = useCapture({ stream, recordingId, track });
  const [finishing, setFinishing] = useState(false);
  // Solo tras entregar el último segmento a la cola: antes, "0 pendientes" no significa
  // que no quede audio por subir.
  const [finishRequested, setFinishRequested] = useState(false);
  const [result, setResult] = useState<CompleteResult | null>(null);
  const stats = useUploaderStats();
  const localPending = stats?.localByRecording[recordingId] ?? 0;

  async function finish() {
    setFinishing(true);
    await capture.stop();
    // Queda anotado en el dispositivo: si se cierra la página, UploadSync lo completará.
    await getRecorderQueue().store.requestFinish(recordingId);
    setFinishRequested(true);
    getRecorderQueue().uploader.notify();
  }

  // Cuando ya no quede nada en el dispositivo, cerrar la captura en el servidor.
  useEffect(() => {
    if (!finishRequested || localPending > 0 || result?.kind === "done") return;
    let cancelled = false;
    void tryComplete(recordingId).then((r) => {
      if (!cancelled) setResult(r);
    });
    return () => {
      cancelled = true;
    };
  }, [finishRequested, localPending, recordingId, result]);

  const recording = capture.phase === "recording";
  const paused = capture.phase === "paused";

  if (result?.kind === "done") {
    return (
      <div className="card stack recorder">
        <h1>{title}</h1>
        <p className="alert success" role="status">
          {dict.recording.completed}
        </p>
        <Link className="button primary" href={`/${lang}/recordings`}>
          {dict.recording.openList}
        </Link>
      </div>
    );
  }

  return (
    <div className="card stack recorder">
      <h1>{title}</h1>

      {capture.phase === "interrupted" ? (
        <div className="stack">
          <p className="alert error" role="alert">
            {dict.recording.interrupted}
          </p>
          <button type="button" className="button primary" onClick={onResume}>
            {dict.recording.continue}
          </button>
          <button
            type="button"
            className="button"
            onClick={finish}
            disabled={finishing}
          >
            {dict.recording.finish}
          </button>
        </div>
      ) : (
        <>
          <div className="recorder-state">
            <span
              className={recording ? "rec-dot live" : "rec-dot"}
              aria-hidden="true"
            />
            <strong>
              {recording
                ? dict.recording.recording
                : paused
                  ? dict.recording.paused
                  : ""}
            </strong>
          </div>
          <p className="elapsed" aria-label={dict.recording.elapsed}>
            <time>{formatDuration(capture.elapsedMs)}</time>
          </p>
          <div className="field">
            <label htmlFor="level">{dict.recording.level}</label>
            <meter
              id="level"
              min={0}
              max={1}
              low={0.05}
              high={0.9}
              optimum={0.4}
              value={capture.level}
            />
          </div>
          {!finishing && (
            <div className="actions">
              {recording && (
                <button
                  type="button"
                  className="button"
                  onClick={capture.pause}
                >
                  {dict.recording.pause}
                </button>
              )}
              {paused && (
                <button
                  type="button"
                  className="button"
                  onClick={capture.resume}
                >
                  {dict.recording.resume}
                </button>
              )}
              <button type="button" className="button primary" onClick={finish}>
                {dict.recording.finish}
              </button>
            </div>
          )}
          {!finishing && <p className="hint">{dict.recording.keepOpen}</p>}
        </>
      )}

      {finishing && localPending > 0 && (
        <p role="status">{dict.recording.uploadingFinal}</p>
      )}
      {result?.kind === "missing" && (
        <p className="alert error">{dict.recording.missing}</p>
      )}
      {capture.error && <p className="alert error">{capture.error}</p>}

      <UploadStatus dict={dict} recordingId={recordingId} />
    </div>
  );
}
