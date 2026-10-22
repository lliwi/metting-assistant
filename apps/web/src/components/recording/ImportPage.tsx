"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { RequireAuth } from "@/components/auth/RequireAuth";
import { errorMessage, Field, FormMessage } from "@/components/forms";
import type { Dictionary, Locale } from "@/i18n/client";
import { format } from "@/i18n/format";
import { api, errorCode } from "@/lib/api/client";
import {
  IMPORT_MAX_BYTES,
  ImportUploadError,
  uploadFileInChunks,
} from "@/lib/recorder/importUpload";

import { SetupFields, useSetupState } from "./SetupFields";

type Props = { dict: Dictionary; lang: Locale };
type Phase =
  | { kind: "form" }
  | { kind: "uploading"; percent: number }
  | { kind: "processing"; recordingId: string }
  | { kind: "ready" };

const ACCEPT =
  "audio/*,video/mp4,video/quicktime,video/webm,.mp3,.m4a,.aac,.wav,.ogg,.oga,.opus,.webm,.mp4,.mov";

export function ImportPage({ dict, lang }: Props) {
  return (
    <RequireAuth lang={lang} loadingLabel={dict.common.loading}>
      {() => <Importer dict={dict} lang={lang} />}
    </RequireAuth>
  );
}

function Importer({ dict, lang }: Props) {
  const setup = useSetupState(lang);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [phase, setPhase] = useState<Phase>({ kind: "form" });
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!file || !setup.consent || !setup.organizationId) return;
    if (file.size > IMPORT_MAX_BYTES) {
      setError(dict.import.tooLarge);
      return;
    }
    setError(null);
    setPhase({ kind: "uploading", percent: 0 });
    const { data, error: apiError } = await api.POST("/v1/recordings", {
      body: {
        organization_id: setup.organizationId,
        title: title || file.name,
        language: setup.language,
        source: "import",
        filename: file.name,
        consent: true,
      },
    });
    if (!data) {
      setError(errorMessage(dict, errorCode(apiError)));
      setPhase({ kind: "form" });
      return;
    }
    try {
      const chunks = await uploadFileInChunks(file, data.id, (bytes) =>
        setPhase({
          kind: "uploading",
          percent: Math.floor((bytes / file.size) * 100),
        }),
      );
      const done = await api.POST("/v1/recordings/{recording_id}/complete", {
        params: { path: { recording_id: data.id } },
        body: { tracks: { "0": chunks } },
      });
      if (!done.response.ok) throw new ImportUploadError(errorCode(done.error));
      setPhase({ kind: "processing", recordingId: data.id });
    } catch (err) {
      const code = err instanceof ImportUploadError ? err.code : "network";
      setError(
        code === "network" ? dict.errors.network : errorMessage(dict, code),
      );
      setPhase({ kind: "form" });
    }
  }

  if (phase.kind === "processing") {
    return (
      <Processing
        dict={dict}
        recordingId={phase.recordingId}
        onReady={() => setPhase({ kind: "ready" })}
        onFailed={(code) => {
          setError(errorMessage(dict, code));
          setPhase({ kind: "form" });
        }}
      />
    );
  }

  if (phase.kind === "ready") {
    return (
      <div className="card stack">
        <h1>{dict.import.title}</h1>
        <p className="alert success" role="status">
          {dict.import.ready}
        </p>
        <Link className="button primary" href={`/${lang}/recordings`}>
          {dict.recording.openList}
        </Link>
      </div>
    );
  }

  const uploading = phase.kind === "uploading";
  return (
    <form className="card stack" onSubmit={submit}>
      <h1>{dict.import.title}</h1>
      <Field
        label={dict.import.file}
        hint={dict.import.fileHint}
        type="file"
        accept={ACCEPT}
        required
        disabled={uploading}
        onChange={(e) => {
          const selected = e.target.files?.[0] ?? null;
          setFile(selected);
          if (selected && !title)
            setTitle(selected.name.replace(/\.[^.]+$/, ""));
        }}
      />
      <Field
        label={dict.recording.titleLabel}
        required
        maxLength={300}
        disabled={uploading}
        value={title}
        onChange={(e) => setTitle(e.target.value)}
      />
      <SetupFields dict={dict} state={setup} />
      <FormMessage error={error} />
      {uploading && (
        <div className="field" role="status">
          <label htmlFor="import-progress">
            {format(dict.import.uploading, { percent: phase.percent })}
          </label>
          <progress id="import-progress" max={100} value={phase.percent} />
        </div>
      )}
      <button
        type="submit"
        className="button primary large"
        disabled={uploading || !file || !setup.consent || !setup.organizationId}
      >
        {dict.import.submit}
      </button>
    </form>
  );
}

/** Sigue por SSE el análisis y la extracción de audio del worker. */
function Processing({
  dict,
  recordingId,
  onReady,
  onFailed,
}: {
  dict: Dictionary;
  recordingId: string;
  onReady: () => void;
  onFailed: (code: string) => void;
}) {
  // Refs: los callbacks del padre cambian en cada render y no deben reabrir la conexión.
  const callbacks = useRef({ onReady, onFailed });
  useEffect(() => {
    callbacks.current = { onReady, onFailed };
  });

  useEffect(() => {
    const source = new EventSource(`/api/v1/recordings/${recordingId}/events`);
    const handle = (event: MessageEvent<string>) => {
      const data = JSON.parse(event.data) as {
        status?: string;
        audio_ready?: boolean;
        error?: string | null;
        error_code?: string | null;
      };
      if (data.audio_ready) {
        source.close();
        callbacks.current.onReady();
      } else if (data.status === "failed") {
        source.close();
        callbacks.current.onFailed(data.error ?? data.error_code ?? "unknown");
      }
    };
    source.addEventListener("recording", handle);
    source.addEventListener("status", handle);
    return () => source.close();
  }, [recordingId]);

  return (
    <div className="card stack">
      <h1>{dict.import.title}</h1>
      <p role="status">{dict.import.processing}</p>
      <progress aria-label={dict.import.processing} />
    </div>
  );
}
