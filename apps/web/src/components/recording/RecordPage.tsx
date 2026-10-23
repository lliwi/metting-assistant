"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useState } from "react";

import { RequireAuth } from "@/components/auth/RequireAuth";
import { errorMessage, Field, FormMessage } from "@/components/forms";
import type { Dictionary, Locale } from "@/i18n/client";
import { format } from "@/i18n/format";
import { api, errorCode, type Schemas, type User } from "@/lib/api/client";
import { useQuery } from "@/lib/api/useQuery";
import { getRecorderQueue, SegmentStore } from "@/lib/recorder";

import { RecorderView } from "./RecorderView";
import { SetupFields, useSetupState } from "./SetupFields";

type Props = { dict: Dictionary; lang: Locale };
type Active = {
  stream: MediaStream;
  recordingId: string;
  track: number;
  title: string;
};

const AUDIO_CONSTRAINTS: MediaStreamConstraints = {
  audio: {
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  },
};

/** Pide el micrófono antes de crear nada en el servidor; traduce los errores habituales. */
async function requestMicrophone(dict: Dictionary): Promise<MediaStream> {
  // Sin contexto seguro (HTTPS o localhost) el navegador oculta el micrófono, aunque el
  // usuario le haya dado permiso: es la causa más habitual al entrar desde el móvil por IP.
  if (!window.isSecureContext) {
    const https = `https://${window.location.hostname}:8443${window.location.pathname}`;
    throw new Error(format(dict.recording.insecureContext, { url: https }));
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error(dict.recording.unsupported);
  }
  if (typeof MediaRecorder === "undefined") {
    throw new Error(dict.recording.noMediaRecorder);
  }
  try {
    return await navigator.mediaDevices.getUserMedia(AUDIO_CONSTRAINTS);
  } catch (e) {
    const name = e instanceof DOMException ? e.name : String(e);
    if (name === "NotAllowedError" || name === "SecurityError") {
      throw new Error(dict.recording.micDenied);
    }
    if (name === "NotFoundError" || name === "OverconstrainedError") {
      throw new Error(dict.recording.micUnavailable);
    }
    if (name === "NotReadableError" || name === "AbortError") {
      throw new Error(dict.recording.micBusy);
    }
    // Se muestra el nombre del error para poder diagnosticarlo.
    throw new Error(`${dict.recording.unsupported} (${name})`);
  }
}

function isAppleMobile(): boolean {
  if (typeof navigator === "undefined") return false;
  return (
    /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.maxTouchPoints > 1 && /Mac/.test(navigator.userAgent))
  );
}

export function RecordPage({ dict, lang }: Props) {
  return (
    <RequireAuth lang={lang} loadingLabel={dict.common.loading}>
      {(user) => <Recorder dict={dict} lang={lang} user={user} />}
    </RequireAuth>
  );
}

function Recorder({ dict, lang, user }: Props & { user: User }) {
  const resumeId = useSearchParams().get("recording");
  const [active, setActive] = useState<Active | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [iosNotice] = useState(isAppleMobile);

  /** Abre una pista nueva en una grabación existente (reanudación). */
  const resume = useCallback(
    async (recordingId: string, title: string) => {
      setBusy(true);
      setError(null);
      try {
        const stream = await requestMicrophone(dict);
        const { data, error: apiError } = await api.POST(
          "/v1/recordings/{recording_id}/tracks",
          {
            params: { path: { recording_id: recordingId } },
          },
        );
        if (!data) {
          stream.getTracks().forEach((t) => t.stop());
          setError(errorMessage(dict, errorCode(apiError)));
          return;
        }
        await getRecorderQueue().store.ensureTrack(recordingId, data.track);
        setActive({ stream, recordingId, track: data.track, title });
      } catch (e) {
        setError(e instanceof Error ? e.message : dict.errors.unknown);
      } finally {
        setBusy(false);
      }
    },
    [dict],
  );

  if (active) {
    return (
      <>
        {iosNotice && <p className="notice">{dict.recording.iosNotice}</p>}
        <RecorderView
          key={`${active.recordingId}-${active.track}`}
          dict={dict}
          lang={lang}
          stream={active.stream}
          recordingId={active.recordingId}
          track={active.track}
          title={active.title}
          ownerId={user.id}
          onResume={() => void resume(active.recordingId, active.title)}
        />
      </>
    );
  }

  return (
    <>
      {iosNotice && <p className="notice">{dict.recording.iosNotice}</p>}
      {resumeId ? (
        <ResumeForm
          dict={dict}
          user={user}
          recordingId={resumeId}
          busy={busy}
          error={error}
          onStart={resume}
        />
      ) : (
        <NewRecordingForm
          dict={dict}
          lang={lang}
          busy={busy}
          error={error}
          onStart={async (body) => {
            setBusy(true);
            setError(null);
            try {
              const stream = await requestMicrophone(dict);
              const { data, error: apiError } = await api.POST(
                "/v1/recordings",
                { body },
              );
              if (!data) {
                stream.getTracks().forEach((t) => t.stop());
                setError(errorMessage(dict, errorCode(apiError)));
                return;
              }
              void SegmentStore.requestPersistence();
              await getRecorderQueue().store.ensureTrack(data.id, 0);
              setActive({
                stream,
                recordingId: data.id,
                track: 0,
                title: data.title,
              });
            } catch (e) {
              setError(e instanceof Error ? e.message : dict.errors.unknown);
            } finally {
              setBusy(false);
            }
          }}
        />
      )}
    </>
  );
}

function NewRecordingForm({
  dict,
  lang,
  busy,
  error,
  onStart,
}: {
  dict: Dictionary;
  lang: Locale;
  busy: boolean;
  error: string | null;
  onStart: (body: Schemas["RecordingCreate"]) => Promise<void>;
}) {
  const setup = useSetupState(lang);
  const { consent, language, organizationId: selectedOrg } = setup;
  const [title, setTitle] = useState(() =>
    format(dict.recording.defaultTitle, {
      date: new Intl.DateTimeFormat(lang, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date()),
    }),
  );

  return (
    <form
      className="card stack"
      onSubmit={(e) => {
        e.preventDefault();
        if (!consent || !selectedOrg) return;
        void onStart({
          organization_id: selectedOrg,
          title,
          language,
          source: "browser",
          consent: true,
        });
      }}
    >
      <h1>{dict.recording.newTitle}</h1>
      <Field
        label={dict.recording.titleLabel}
        required
        maxLength={300}
        value={title}
        onChange={(e) => setTitle(e.target.value)}
      />
      <SetupFields dict={dict} state={setup} />

      <FormMessage error={error} />
      {busy && <p role="status">{dict.recording.requestingMic}</p>}
      <button
        type="submit"
        className="button primary large"
        disabled={busy || !consent || !selectedOrg}
      >
        {dict.recording.start}
      </button>
    </form>
  );
}

function ResumeForm({
  dict,
  user,
  recordingId,
  busy,
  error,
  onStart,
}: {
  dict: Dictionary;
  user: User;
  recordingId: string;
  busy: boolean;
  error: string | null;
  onStart: (recordingId: string, title: string) => Promise<void>;
}) {
  const fetchRecording = useCallback(async () => {
    const { data, error: apiError } = await api.GET(
      "/v1/recordings/{recording_id}",
      {
        params: { path: { recording_id: recordingId } },
      },
    );
    return { data, code: data ? null : errorCode(apiError) };
  }, [recordingId]);
  const { data: result } = useQuery(fetchRecording);

  if (!result) return <p role="status">{dict.common.loading}</p>;
  const rec = result.data;
  if (!rec)
    return (
      <p className="alert error">
        {errorMessage(dict, result.code ?? "unknown")}
      </p>
    );
  if (rec.status !== "uploading" || rec.owner_id !== user.id) {
    return <p className="alert error">{dict.recording.notResumable}</p>;
  }

  return (
    <div className="card stack">
      <h1>{format(dict.recording.resumeTitle, { title: rec.title })}</h1>
      <p className="hint">{dict.recording.consentCheck}</p>
      <FormMessage error={error} />
      {busy && <p role="status">{dict.recording.requestingMic}</p>}
      <button
        type="button"
        className="button primary large"
        disabled={busy}
        onClick={() => void onStart(rec.id, rec.title)}
      >
        {dict.recording.continue}
      </button>
    </div>
  );
}
