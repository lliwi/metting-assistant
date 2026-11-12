"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useState, useSyncExternalStore } from "react";

import { RequireAuth } from "@/components/auth/RequireAuth";
import { errorMessage, Field, FormMessage } from "@/components/forms";
import type { Dictionary, Locale } from "@/i18n/client";
import { format } from "@/i18n/format";
import { api, errorCode, type Schemas, type User } from "@/lib/api/client";
import { useQuery } from "@/lib/api/useQuery";
import { getRecorderQueue, SegmentStore } from "@/lib/recorder";
import {
  type CaptureMode,
  type CaptureSource,
  meetingSource,
  MIC_CONSTRAINTS,
  microphoneSource,
  NoDisplayAudioError,
  requestDisplayAudio,
  supportsMeetingCapture,
} from "@/lib/recorder/captureSource";

import { RecorderView } from "./RecorderView";
import { SetupFields, useSetupState } from "./SetupFields";

type Props = { dict: Dictionary; lang: Locale };
type CaptureChoice = { mode: CaptureMode; includeMic: boolean };
type Active = CaptureChoice & {
  source: CaptureSource;
  recordingId: string;
  track: number;
  title: string;
};

/** Comprobaciones previas comunes a cualquier fuente; no espera nada (conserva el gesto). */
function checkSupport(dict: Dictionary): void {
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
}

/**
 * Pide los permisos antes de crear nada en el servidor. En una reunión online se pide primero
 * la pestaña: el navegador solo la ofrece con una activación del usuario reciente, y el
 * diálogo del micrófono la consumiría.
 */
async function requestCapture(
  dict: Dictionary,
  { mode, includeMic }: CaptureChoice,
): Promise<CaptureSource> {
  checkSupport(dict);
  if (mode === "microphone") {
    return microphoneSource(await requestMicrophone(dict));
  }
  if (!supportsMeetingCapture()) {
    throw new Error(dict.recording.meetingUnsupported);
  }
  let display: MediaStream;
  try {
    display = await requestDisplayAudio();
  } catch (e) {
    if (e instanceof NoDisplayAudioError) {
      throw new Error(dict.recording.meetingNoAudio);
    }
    const name = e instanceof DOMException ? e.name : String(e);
    if (name === "NotAllowedError" || name === "AbortError") {
      throw new Error(dict.recording.meetingDenied);
    }
    throw new Error(`${dict.recording.meetingUnsupported} (${name})`);
  }
  try {
    const mic = includeMic ? await requestMicrophone(dict) : null;
    return meetingSource(display, mic);
  } catch (e) {
    display.getTracks().forEach((t) => t.stop());
    throw e;
  }
}

/** Pide el micrófono; traduce los errores habituales. */
async function requestMicrophone(dict: Dictionary): Promise<MediaStream> {
  try {
    return await navigator.mediaDevices.getUserMedia({ audio: MIC_CONSTRAINTS });
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
    async (recordingId: string, title: string, choice: CaptureChoice) => {
      setBusy(true);
      setError(null);
      try {
        const source = await requestCapture(dict, choice);
        const { data, error: apiError } = await api.POST(
          "/v1/recordings/{recording_id}/tracks",
          {
            params: { path: { recording_id: recordingId } },
          },
        );
        if (!data) {
          source.dispose();
          setError(errorMessage(dict, errorCode(apiError)));
          return;
        }
        await getRecorderQueue().store.ensureTrack(recordingId, data.track);
        setActive({ ...choice, source, recordingId, track: data.track, title });
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
          source={active.source}
          mode={active.mode}
          recordingId={active.recordingId}
          track={active.track}
          title={active.title}
          ownerId={user.id}
          onResume={() =>
            void resume(active.recordingId, active.title, {
              mode: active.mode,
              includeMic: active.includeMic,
            })
          }
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
          onStart={async (body, choice) => {
            setBusy(true);
            setError(null);
            try {
              const source = await requestCapture(dict, choice);
              const { data, error: apiError } = await api.POST(
                "/v1/recordings",
                { body },
              );
              if (!data) {
                source.dispose();
                setError(errorMessage(dict, errorCode(apiError)));
                return;
              }
              void SegmentStore.requestPersistence();
              await getRecorderQueue().store.ensureTrack(data.id, 0);
              setActive({
                ...choice,
                source,
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
  onStart: (
    body: Schemas["RecordingCreate"],
    choice: CaptureChoice,
  ) => Promise<void>;
}) {
  const setup = useSetupState(lang);
  const capture = useCaptureChoice();
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
        void onStart(
          {
            organization_id: selectedOrg,
            title,
            language,
            source: "browser",
            consent: true,
          },
          capture.choice,
        );
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
      <CaptureFields dict={dict} state={capture} />
      <SetupFields dict={dict} state={setup} />

      <FormMessage error={error} />
      {busy && <RequestingStatus dict={dict} mode={capture.choice.mode} />}
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
  onStart: (
    recordingId: string,
    title: string,
    choice: CaptureChoice,
  ) => Promise<void>;
}) {
  const capture = useCaptureChoice();
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
      <CaptureFields dict={dict} state={capture} />
      <FormMessage error={error} />
      {busy && <RequestingStatus dict={dict} mode={capture.choice.mode} />}
      <button
        type="button"
        className="button primary large"
        disabled={busy}
        onClick={() => void onStart(rec.id, rec.title, capture.choice)}
      >
        {dict.recording.continue}
      </button>
    </div>
  );
}

const noSubscription = () => () => undefined;

function useCaptureChoice() {
  // En el servidor no hay navigator: allí se renderiza como no disponible.
  const supported = useSyncExternalStore(
    noSubscription,
    supportsMeetingCapture,
    () => false,
  );
  const [mode, setMode] = useState<CaptureMode>("microphone");
  const [includeMic, setIncludeMic] = useState(true);
  return {
    supported,
    choice: {
      mode: supported ? mode : "microphone",
      includeMic,
    } satisfies CaptureChoice,
    setMode,
    setIncludeMic,
  };
}

function CaptureFields({
  dict,
  state,
}: {
  dict: Dictionary;
  state: ReturnType<typeof useCaptureChoice>;
}) {
  if (!state.supported) return null;
  const { mode, includeMic } = state.choice;
  return (
    <fieldset className="field">
      <legend>{dict.recording.sourceLabel}</legend>
      <label className="checkbox">
        <input
          type="radio"
          name="capture-mode"
          checked={mode === "microphone"}
          onChange={() => state.setMode("microphone")}
        />
        {dict.recording.sourceMicrophone}
      </label>
      <label className="checkbox">
        <input
          type="radio"
          name="capture-mode"
          checked={mode === "meeting"}
          onChange={() => state.setMode("meeting")}
        />
        {dict.recording.sourceMeeting}
      </label>
      {mode === "meeting" && (
        <>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={includeMic}
              onChange={(e) => state.setIncludeMic(e.target.checked)}
            />
            {dict.recording.meetingIncludeMic}
          </label>
          <p className="hint">{dict.recording.meetingHelp}</p>
        </>
      )}
    </fieldset>
  );
}

function RequestingStatus({
  dict,
  mode,
}: {
  dict: Dictionary;
  mode: CaptureMode;
}) {
  return (
    <p role="status">
      {mode === "meeting"
        ? dict.recording.requestingMeeting
        : dict.recording.requestingMic}
    </p>
  );
}
