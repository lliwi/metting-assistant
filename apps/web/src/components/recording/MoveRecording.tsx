"use client";

import { useState } from "react";

import { FormMessage, useAction } from "@/components/forms";
import type { Dictionary } from "@/i18n/client";
import { format } from "@/i18n/format";
import { api, type Schemas } from "@/lib/api/client";

type Recording = Schemas["RecordingDetail"];

/** Igual que la API: con la captura cerrada y sin procesamiento en curso. */
export const MOVABLE: Recording["status"][] = [
  "transcribed",
  "failed",
  "cancelled",
];

type Organization = Schemas["OrganizationOut"];

/** Organizaciones a las que se puede mover: donde se puede grabar, salvo la actual. */
export function moveTargets(
  orgs: Organization[],
  recording: Recording,
): Organization[] {
  return orgs.filter(
    (o) => o.role !== "viewer" && o.id !== recording.organization_id,
  );
}

/** Destinos: la preferida primero (orden de la API). */
export function MoveRecording({
  dict,
  recording,
  targets,
  onMoved,
}: {
  dict: Dictionary;
  recording: Recording;
  targets: Organization[];
  onMoved: (r: Recording) => void;
}) {
  const [choice, setChoice] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const { pending, error, run } = useAction(dict);
  const target = targets.find((o) => o.id === choice) ?? targets[0];
  const label = (o: Organization) =>
    o.is_personal ? dict.orgs.personal : o.name;

  if (!target) return null;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const moved = target;
    const data = await run(() =>
      api.POST("/v1/recordings/{recording_id}/move", {
        params: { path: { recording_id: recording.id } },
        body: { organization_id: moved.id },
      }),
    );
    if (data) {
      setChoice("");
      setDone(format(dict.move.done, { name: label(moved) }));
      onMoved(data);
    }
  }

  return (
    <form className="stack" onSubmit={submit}>
      <p className="hint">{dict.move.hint}</p>
      <div className="row ai-generate">
        <label className="visually-hidden" htmlFor="move-target">
          {dict.move.target}
        </label>
        <select
          id="move-target"
          value={target.id}
          onChange={(e) => {
            setChoice(e.target.value);
            setDone(null);
          }}
        >
          {targets.map((o) => (
            <option key={o.id} value={o.id}>
              {label(o)}
            </option>
          ))}
        </select>
        <button type="submit" className="button" disabled={pending}>
          {dict.move.submit}
        </button>
      </div>
      <FormMessage error={error} success={done} />
    </form>
  );
}
