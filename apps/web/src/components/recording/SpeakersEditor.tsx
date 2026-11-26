"use client";

import { Check, Users } from "lucide-react";
import { useState } from "react";

import { FormMessage, useAction } from "@/components/forms";
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

/** Color estable por hablante (H1, H2…): el mismo en las fichas y en cada frase. */
export function speakerClass(label: string): string {
  const n = Number(label.replace(/\D/g, "")) || 0;
  return `spk spk-${(Math.max(n, 1) - 1) % 8}`;
}

export function speakerLabels(transcript: Transcript): string[] {
  return [
    ...new Set(transcript.segments.map((s) => s.speaker).filter(Boolean)),
  ] as string[];
}

/** Guarda el nombre de una etiqueta. Vacío = vuelve a "Hablante N". */
export function useRenameSpeaker(
  dict: Dictionary,
  recordingId: string,
  onSaved: (t: Transcript) => void,
) {
  const action = useAction(dict);
  const [renamed, setRenamed] = useState(false);
  async function rename(label: string, name: string) {
    setRenamed(false);
    const data = await action.run(() =>
      api.PATCH("/v1/recordings/{recording_id}/transcript/speakers", {
        params: { path: { recording_id: recordingId } },
        body: { names: { [label]: name.trim() } },
      }),
    );
    if (data) {
      setRenamed(true);
      onSaved(data);
    }
    return Boolean(data);
  }
  return { ...action, renamed, setRenamed, rename };
}

/** Nombre de un hablante que se edita al pulsarlo: Intro o salir guarda, Escape cancela. */
export function SpeakerName({
  dict,
  label,
  names,
  editing,
  onEdit,
  onRename,
  onDone,
  className = "",
}: {
  dict: Dictionary;
  label: string;
  names: Record<string, string>;
  editing: boolean;
  onEdit: () => void;
  onRename: (label: string, name: string) => Promise<boolean>;
  onDone: () => void;
  className?: string;
}) {
  const shown = speakerName(dict, label, names) ?? label;
  const [value, setValue] = useState(names[label] ?? "");
  const [saving, setSaving] = useState(false);

  if (!editing) {
    return (
      <button
        type="button"
        className={`speaker-name ${speakerClass(label)} ${className}`}
        onClick={() => {
          setValue(names[label] ?? "");
          onEdit();
        }}
        aria-label={format(dict.speakers.renameAria, { name: shown })}
        title={dict.speakers.hint}
      >
        {shown}
      </button>
    );
  }

  async function save() {
    if (saving) return;
    if (value.trim() === (names[label] ?? "")) return onDone();
    setSaving(true);
    const ok = await onRename(label, value);
    setSaving(false);
    if (ok) onDone();
  }

  return (
    <form
      className={`speaker-edit ${speakerClass(label)} ${className}`}
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <input
        aria-label={format(dict.speakers.rename, {
          label: format(dict.speakers.label, { n: label.replace(/^H/, "") }),
        })}
        placeholder={format(dict.speakers.label, {
          n: label.replace(/^H/, ""),
        })}
        maxLength={100}
        value={value}
        autoFocus
        disabled={saving}
        onChange={(e) => setValue(e.target.value)}
        onBlur={() => void save()}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            onDone();
          }
        }}
      />
      <button
        type="submit"
        className="icon-link small"
        aria-label={dict.edit.save}
        title={dict.edit.save}
        // Sin perder el foco antes del clic (el blur ya guardaría).
        onMouseDown={(e) => e.preventDefault()}
      >
        <Check aria-hidden="true" size={16} />
      </button>
    </form>
  );
}

/** Fichas de los hablantes de la transcripción, con acceso a unir repetidos. */
export function SpeakerChips({
  dict,
  labels,
  names,
  editing,
  onEdit,
  onRename,
  onDone,
  canFindDuplicates,
  onFindDuplicates,
}: {
  dict: Dictionary;
  labels: string[];
  names: Record<string, string>;
  editing: string | null;
  onEdit: (label: string) => void;
  onRename: (label: string, name: string) => Promise<boolean>;
  onDone: () => void;
  canFindDuplicates: boolean;
  onFindDuplicates: () => void;
}) {
  if (labels.length === 0) return null;
  return (
    <div className="speakers" role="group" aria-label={dict.speakers.title}>
      <Users aria-hidden="true" size={18} className="muted-icon" />
      <ul className="chips">
        {labels.map((label) => (
          <li key={label}>
            <SpeakerName
              dict={dict}
              label={label}
              names={names}
              className="chip"
              editing={editing === `chip:${label}`}
              onEdit={() => onEdit(`chip:${label}`)}
              onRename={onRename}
              onDone={onDone}
            />
          </li>
        ))}
      </ul>
      {canFindDuplicates && (
        <button
          type="button"
          className="link-button subtle"
          onClick={onFindDuplicates}
          title={dict.speakers.duplicatesTitle}
        >
          {dict.speakers.findDuplicatesShort}
        </button>
      )}
    </div>
  );
}

/** Propuestas del LLM de hablantes que son la misma persona: se aplican una a una. */
export function SpeakerSuggestions({
  dict,
  recordingId,
  transcript,
  onMerged,
  onClose,
}: {
  dict: Dictionary;
  recordingId: string;
  transcript: Transcript;
  onMerged: (t: Transcript) => void;
  onClose: () => void;
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
    <section className="inset stack" aria-labelledby="speaker-duplicates">
      <div className="row spread">
        <h3 id="speaker-duplicates">{dict.speakers.duplicatesTitle}</h3>
        <button type="button" className="button small" onClick={onClose}>
          {dict.common.close}
        </button>
      </div>
      <p className="hint">{dict.speakers.duplicatesHint}</p>
      {suggestions === null && (
        <div>
          <button
            type="button"
            className="button primary small"
            onClick={() => void find()}
            disabled={search.pending}
          >
            {search.pending
              ? dict.speakers.searching
              : dict.speakers.findDuplicates}
          </button>
        </div>
      )}
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
                    className="button primary small"
                    onClick={() => void apply(s)}
                    disabled={merge.pending}
                  >
                    {dict.speakers.merge}
                  </button>
                  <button
                    type="button"
                    className="button small"
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
