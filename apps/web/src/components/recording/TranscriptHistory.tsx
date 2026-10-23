"use client";

import { useCallback, useState } from "react";

import type { Dictionary, Locale } from "@/i18n/client";
import { format } from "@/i18n/format";
import { api, type Schemas } from "@/lib/api/client";
import { formatDuration } from "@/lib/recorder/useCapture";

type Edit = Schemas["TranscriptEditOut"];

/** Historial de correcciones manuales; se carga al desplegarlo. */
export function TranscriptHistory({
  dict,
  lang,
  recordingId,
  segments,
  version,
}: {
  dict: Dictionary;
  lang: Locale;
  recordingId: string;
  segments: Schemas["TranscriptSegmentOut"][];
  version: string;
}) {
  const [edits, setEdits] = useState<Edit[] | null>(null);
  const dateFormat = new Intl.DateTimeFormat(lang, {
    dateStyle: "medium",
    timeStyle: "short",
  });
  const startOf = (idx: number) =>
    segments.find((s) => s.idx === idx)?.start_ms ?? 0;

  const load = useCallback(async () => {
    const { data } = await api.GET(
      "/v1/recordings/{recording_id}/transcript/history",
      {
        params: { path: { recording_id: recordingId } },
      },
    );
    setEdits(data ?? []);
  }, [recordingId]);

  return (
    <details
      key={version}
      onToggle={(e) => {
        if (e.currentTarget.open) void load();
      }}
    >
      <summary>{dict.edit.history}</summary>
      {edits === null ? (
        <p role="status">{dict.common.loading}</p>
      ) : edits.length === 0 ? (
        <p className="hint">{dict.edit.noHistory}</p>
      ) : (
        <ul className="list">
          {edits.map((e, i) => (
            <li key={i} className="history-item">
              <strong>
                {format(
                  e.field === "text"
                    ? dict.edit.changedText
                    : dict.edit.changedSpeaker,
                  {
                    time: formatDuration(startOf(e.segment_idx)),
                  },
                )}
              </strong>
              <div>
                <del>{e.old_value ?? "—"}</del> →{" "}
                <ins>{e.new_value ?? "—"}</ins>
              </div>
              <div className="hint">
                {format(dict.edit.by, {
                  name: e.editor_name ?? "—",
                  date: dateFormat.format(new Date(e.created_at)),
                })}
              </div>
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}
