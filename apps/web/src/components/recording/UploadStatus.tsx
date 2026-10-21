"use client";

import type { Dictionary } from "@/i18n/client";
import { format } from "@/i18n/format";
import { errorMessage } from "@/components/forms";
import { discardFailed, getRecorderQueue } from "@/lib/recorder";
import { useOnline, useUploaderStats } from "@/lib/recorder/useUploaderStats";

/** Conectividad y segmentos pendientes de una grabación (o de todas). */
export function UploadStatus({
  dict,
  recordingId,
}: {
  dict: Dictionary;
  recordingId?: string;
}) {
  const stats = useUploaderStats();
  const online = useOnline();
  // Lo fallido se muestra aparte: no se subirá sin intervención del usuario.
  const failed = stats?.failed ?? 0;
  const local = recordingId
    ? (stats?.localByRecording[recordingId] ?? 0)
    : (stats?.pending ?? 0);

  return (
    <div className="upload-status" role="status" aria-live="polite">
      <p className={online ? "badge ok" : "badge warn"}>
        {online ? dict.recording.online : dict.recording.offline}
      </p>
      <p>
        {local > 0
          ? format(dict.recording.pendingSegments, { count: local })
          : dict.recording.allUploaded}
      </p>
      {stats?.waitingForAuth && (
        <p className="alert error">{dict.recording.sessionExpired}</p>
      )}
      {failed > 0 && (
        <p className="alert error">
          {format(dict.recording.failedSegments, {
            count: failed,
            error: errorMessage(dict, stats?.failedReason ?? "unknown"),
          })}{" "}
          <button
            type="button"
            className="link-button"
            onClick={() => void getRecorderQueue().uploader.retryFailed()}
          >
            {dict.recording.retry}
          </button>{" "}
          <button
            type="button"
            className="link-button"
            onClick={() => {
              if (window.confirm(dict.recording.discardConfirm))
                void discardFailed();
            }}
          >
            {dict.recording.discard}
          </button>
        </p>
      )}
    </div>
  );
}
