"use client";

import { api, csrfToken, CSRF_HEADER, errorCode } from "@/lib/api/client";

import { SegmentStore, type StoredSegment } from "./segmentStore";
import {
  classifyResponse,
  SegmentUploader,
  type SendOutcome,
} from "./uploader";

export { SegmentStore } from "./segmentStore";
export type { StoredSegment } from "./segmentStore";
export type { UploaderStats } from "./uploader";

export async function sha256Hex(blob: Blob): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    await blob.arrayBuffer(),
  );
  return Array.from(new Uint8Array(digest), (b) =>
    b.toString(16).padStart(2, "0"),
  ).join("");
}

export async function sendSegment(
  segment: StoredSegment,
): Promise<SendOutcome> {
  const form = new FormData();
  form.set("segment_id", segment.id);
  form.set("track", String(segment.track));
  form.set("seq", String(segment.seq));
  form.set("duration_ms", String(segment.durationMs));
  form.set("sha256", segment.sha256);
  form.set("file", segment.blob, `segment-${segment.track}-${segment.seq}`);
  const response = await fetch(
    `/api/v1/recordings/${segment.recordingId}/segments`,
    {
      method: "POST",
      body: form,
      credentials: "same-origin",
      headers: { [CSRF_HEADER]: await csrfToken() },
    },
  );
  return classifyResponse(response);
}

let singleton: { store: SegmentStore; uploader: SegmentUploader } | null = null;

/** Cola única por pestaña: la grabadora añade segmentos y UploadSync la mantiene en marcha. */
/** Descarta del dispositivo los segmentos que el servidor ya no aceptará. */
export async function discardFailed(): Promise<void> {
  const { store, uploader } = getRecorderQueue();
  for (const segment of await store.listAll()) {
    if (segment.status === "failed") await store.delete(segment.id);
  }
  uploader.notify();
}

export function getRecorderQueue() {
  if (!singleton) {
    const store = new SegmentStore();
    singleton = { store, uploader: new SegmentUploader(store, sendSegment) };
  }
  return singleton;
}

export type CompleteResult =
  | { kind: "done" }
  | { kind: "waiting"; local: number } // aún hay segmentos en el dispositivo
  | { kind: "not_requested" } // la captura local sigue abierta
  | { kind: "missing"; segments: { track: number; seq: number }[] }
  | { kind: "error"; code: string };

async function serverTrackCounts(
  recordingId: string,
): Promise<Record<number, number>> {
  const { data } = await api.GET("/v1/recordings/{recording_id}", {
    params: { path: { recording_id: recordingId } },
  });
  return Object.fromEntries(
    (data?.tracks ?? []).map((t) => [t.track, t.segments]),
  );
}

/** Cierra la captura en el servidor si ya no queda nada por subir desde este dispositivo. */
export async function tryComplete(
  recordingId: string,
): Promise<CompleteResult> {
  const { store } = getRecorderQueue();
  const local = await store.listByRecording(recordingId);
  if (local.length > 0) return { kind: "waiting", local: local.length };
  const capture = await store.getCapture(recordingId);
  // Si la captura es de este dispositivo, solo se cierra cuando el usuario la finalizó
  // (la grabadora puede estar a punto de entregar otro segmento).
  if (capture && !capture.finishRequested) return { kind: "not_requested" };
  // Si se captó en otro dispositivo, lo que el servidor tiene es lo esperado.
  const tracks = capture?.tracks ?? (await serverTrackCounts(recordingId));
  const { error, response } = await api.POST(
    "/v1/recordings/{recording_id}/complete",
    {
      params: { path: { recording_id: recordingId } },
      body: {
        tracks: Object.fromEntries(Object.entries(tracks)) as Record<
          string,
          number
        >,
      },
    },
  );
  if (response.ok || errorCode(error) === "recording_closed") {
    await store.deleteCapture(recordingId);
    return { kind: "done" };
  }
  if (errorCode(error) === "missing_segments") {
    const details = (
      error as { error?: { details?: { track: number; seq: number }[] } }
    ).error;
    return { kind: "missing", segments: details?.details ?? [] };
  }
  return { kind: "error", code: errorCode(error) };
}
