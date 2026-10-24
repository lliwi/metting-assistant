"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { FormMessage, useAction } from "@/components/forms";
import type { Dictionary, Locale } from "@/i18n/client";
import { api, type Schemas, type User } from "@/lib/api/client";

type Recording = Schemas["RecordingDetail"];

/** Igual que la API: quien grabó o un administrador de la organización. */
export function canDeleteRecording(user: User, recording: Recording): boolean {
  if (recording.owner_id === user.id) return true;
  const membership = user.memberships.find(
    (m) => m.organization_id === recording.organization_id,
  );
  return membership?.role === "owner" || membership?.role === "admin";
}

/** Borrado definitivo en dos pasos: el primer botón solo muestra la advertencia. */
export function DeleteRecording({
  dict,
  lang,
  recording,
}: {
  dict: Dictionary;
  lang: Locale;
  recording: Recording;
}) {
  const router = useRouter();
  const { pending, error, run } = useAction(dict);
  const [open, setOpen] = useState(false);

  async function confirm() {
    const result = await run(() =>
      api.DELETE("/v1/recordings/{recording_id}", {
        params: { path: { recording_id: recording.id } },
      }),
    );
    // 204 sin cuerpo: run devuelve null si ha ido bien y undefined si ha fallado.
    if (result !== undefined) router.push(`/${lang}/recordings`);
  }

  return (
    <section
      className="card stack danger-zone"
      aria-labelledby="delete-recording-heading"
    >
      <h2 id="delete-recording-heading">{dict.detail.deleteHeading}</h2>
      {!open ? (
        <div>
          <button
            type="button"
            className="button danger"
            onClick={() => setOpen(true)}
          >
            {dict.detail.delete}
          </button>
        </div>
      ) : (
        <>
          <p role="alert">{dict.detail.deleteWarning}</p>
          <FormMessage error={error} />
          <div className="row">
            <button
              type="button"
              className="button danger"
              disabled={pending}
              onClick={confirm}
            >
              {dict.detail.deleteSubmit}
            </button>
            <button
              type="button"
              className="button"
              onClick={() => setOpen(false)}
            >
              {dict.edit.cancel}
            </button>
          </div>
        </>
      )}
    </section>
  );
}
