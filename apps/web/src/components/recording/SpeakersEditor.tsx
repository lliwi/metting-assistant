"use client";

import { useState } from "react";

import { Field, FormMessage, useAction } from "@/components/forms";
import type { Dictionary } from "@/i18n/client";
import { format } from "@/i18n/format";
import { api, type Schemas } from "@/lib/api/client";

type Transcript = Schemas["TranscriptOut"];

export function speakerName(
  dict: Dictionary,
  label: string | null,
  names: Record<string, string>,
) {
  if (!label) return null;
  return (
    names[label] ?? format(dict.speakers.label, { n: label.replace(/^H/, "") })
  );
}

/** Da nombre a los hablantes detectados (H1 → "Laura"). */
export function SpeakersEditor({
  dict,
  recordingId,
  transcript,
  onSaved,
}: {
  dict: Dictionary;
  recordingId: string;
  transcript: Transcript;
  onSaved: (t: Transcript) => void;
}) {
  const labels = [
    ...new Set(transcript.segments.map((s) => s.speaker).filter(Boolean)),
  ] as string[];
  const [names, setNames] = useState<Record<string, string>>(() => ({
    ...transcript.speaker_names,
  }));
  const [saved, setSaved] = useState(false);
  const { pending, error, run } = useAction(dict);

  if (labels.length === 0) return null;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSaved(false);
    const body = Object.fromEntries(labels.map((l) => [l, names[l] ?? ""]));
    const data = await run(() =>
      api.PATCH("/v1/recordings/{recording_id}/transcript/speakers", {
        params: { path: { recording_id: recordingId } },
        body: { names: body },
      }),
    );
    if (data) {
      setSaved(true);
      onSaved(data);
    }
  }

  return (
    <form className="card stack" onSubmit={submit}>
      <h2>{dict.speakers.title}</h2>
      <div className="speaker-grid">
        {labels.map((label) => {
          const generic = format(dict.speakers.label, {
            n: label.replace(/^H/, ""),
          });
          return (
            <Field
              key={label}
              label={format(dict.speakers.rename, { label: generic })}
              maxLength={100}
              placeholder={generic}
              value={names[label] ?? ""}
              onChange={(e) =>
                setNames((n) => ({ ...n, [label]: e.target.value }))
              }
            />
          );
        })}
      </div>
      <FormMessage error={error} success={saved ? dict.speakers.saved : null} />
      <button type="submit" className="button" disabled={pending}>
        {dict.speakers.save}
      </button>
    </form>
  );
}
