"use client";

import { useState } from "react";

import { Field, FormMessage, useAction } from "@/components/forms";
import type { Dictionary } from "@/i18n/client";
import { format } from "@/i18n/format";
import { api, type Schemas } from "@/lib/api/client";

type Transcript = Schemas["TranscriptOut"];
type Suggestion = Schemas["SpeakerSuggestion"];

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

function speakerLabels(transcript: Transcript): string[] {
  return [
    ...new Set(transcript.segments.map((s) => s.speaker).filter(Boolean)),
  ] as string[];
}

/** Cambia cuando cambian las etiquetas o sus nombres: el formulario se reinicia con ellos. */
export function speakersKey(transcript: Transcript): string {
  return `${transcript.id}:${speakerLabels(transcript).join(",")}:${JSON.stringify(
    transcript.speaker_names,
  )}`;
}

/** Da nombre a los hablantes detectados (H1 → "Laura") y une los repetidos. */
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
  const labels = speakerLabels(transcript);
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
    <div className="card stack">
      <form className="stack" onSubmit={submit}>
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
        <FormMessage
          error={error}
          success={saved ? dict.speakers.saved : null}
        />
        <button type="submit" className="button" disabled={pending}>
          {dict.speakers.save}
        </button>
      </form>
      {labels.length > 1 && (
        <SpeakerSuggestions
          dict={dict}
          recordingId={recordingId}
          transcript={transcript}
          onMerged={onSaved}
        />
      )}
    </div>
  );
}

/** Propuestas del LLM de hablantes que son la misma persona: se aplican una a una. */
function SpeakerSuggestions({
  dict,
  recordingId,
  transcript,
  onMerged,
}: {
  dict: Dictionary;
  recordingId: string;
  transcript: Transcript;
  onMerged: (t: Transcript) => void;
}) {
  const [suggestions, setSuggestions] = useState<Suggestion[] | null>(null);
  const [merged, setMerged] = useState(false);
  const search = useAction(dict);
  const merge = useAction(dict);
  const label = (l: string) =>
    speakerName(dict, l, transcript.speaker_names) ?? l;

  async function find() {
    setMerged(false);
    const data = await search.run(() =>
      api.POST("/v1/recordings/{recording_id}/transcript/speaker-suggestions", {
        params: { path: { recording_id: recordingId } },
      }),
    );
    if (data) setSuggestions(data.suggestions);
  }

  async function apply(s: Suggestion) {
    const data = await merge.run(() =>
      api.POST("/v1/recordings/{recording_id}/transcript/speakers/merge", {
        params: { path: { recording_id: recordingId } },
        body: { labels: s.labels, name: s.name },
      }),
    );
    if (data) {
      setSuggestions((all) => all?.filter((x) => x !== s) ?? null);
      setMerged(true);
      onMerged(data);
    }
  }

  return (
    <section className="stack" aria-labelledby="speaker-duplicates">
      <h3 id="speaker-duplicates">{dict.speakers.duplicatesTitle}</h3>
      <p className="hint">{dict.speakers.duplicatesHint}</p>
      <button
        type="button"
        className="button"
        onClick={() => void find()}
        disabled={search.pending}
      >
        {search.pending
          ? dict.speakers.searching
          : dict.speakers.findDuplicates}
      </button>
      <FormMessage
        error={search.error ?? merge.error}
        success={merged ? dict.speakers.merged : null}
      />
      {suggestions?.length === 0 && !merged && (
        <p role="status">{dict.speakers.noSuggestions}</p>
      )}
      {suggestions && suggestions.length > 0 && (
        <ul className="stack speaker-suggestions">
          {suggestions.map((s) => {
            const labels = s.labels.map(label).join(" + ");
            return (
              <li key={s.labels.join("+")} className="stack">
                <strong>
                  {s.name
                    ? format(dict.speakers.suggestionNamed, {
                        labels,
                        name: s.name,
                      })
                    : format(dict.speakers.suggestion, { labels })}
                </strong>
                {s.reason && <span className="hint">{s.reason}</span>}
                <div className="actions">
                  <button
                    type="button"
                    className="button primary"
                    onClick={() => void apply(s)}
                    disabled={merge.pending}
                  >
                    {dict.speakers.merge}
                  </button>
                  <button
                    type="button"
                    className="button"
                    onClick={() =>
                      setSuggestions(
                        (all) => all?.filter((x) => x !== s) ?? null,
                      )
                    }
                  >
                    {dict.speakers.dismiss}
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
