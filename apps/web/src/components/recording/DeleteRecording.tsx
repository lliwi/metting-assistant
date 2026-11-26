"use client";

import { useRouter } from "next/navigation";

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

/** Confirmación del borrado definitivo (dentro de su diálogo). */
export function DeleteRecording({
  dict,
  lang,
  recording,
  onCancel,
}: {
  dict: Dictionary;
  lang: Locale;
  recording: Recording;
  onCancel: () => void;
}) {
  const router = useRouter();
  const { pending, error, run } = useAction(dict);

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
    <div className="stack">
      <p role="alert">{dict.detail.deleteWarning}</p>
      <FormMessage error={error} />
      <div className="row dialog-actions">
        <button type="button" className="button" onClick={onCancel}>
          {dict.edit.cancel}
        </button>
        <button
          type="button"
          className="button danger-solid"
          disabled={pending}
          onClick={confirm}
        >
          {dict.detail.deleteSubmit}
        </button>
      </div>
    </div>
  );
}
