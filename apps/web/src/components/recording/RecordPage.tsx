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

type Props = { dict: Dictionary; lang: Locale };
type Active = {
  stream: MediaStream;
  recordingId: string;
  track: number;
  title: string;
};
type Language = Schemas["RecordingCreate"]["language"];

const AUDIO_CONSTRAINTS: MediaStreamConstraints = {
  audio: {
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  },
};

/** Pide el micrófono antes de crear nada en el servidor; traduce los errores habituales. */
async function requestMicrophone(dict: Dictionary): Promise<MediaStream> {
  if (
    !navigator.mediaDevices?.getUserMedia ||
    typeof MediaRecorder === "undefined"
  ) {
    throw new Error(dict.recording.unsupported);
  }
  try {
    return await navigator.mediaDevices.getUserMedia(AUDIO_CONSTRAINTS);
  } catch (e) {
    const name = e instanceof DOMException ? e.name : "";
    if (name === "NotAllowedError" || name === "SecurityError")
      throw new Error(dict.recording.micDenied);
    if (name === "NotFoundError" || name === "OverconstrainedError") {
      throw new Error(dict.recording.micUnavailable);
    }
    throw new Error(dict.recording.unsupported);
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
  const fetchOrgs = useCallback(
    async () => (await api.GET("/v1/organizations")).data ?? [],
    [],
  );
  const { data: orgs } = useQuery(fetchOrgs);
  const writable = (orgs ?? []).filter((o) => o.role !== "viewer");

  const [title, setTitle] = useState(() =>
    format(dict.recording.defaultTitle, {
      date: new Intl.DateTimeFormat(lang, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date()),
    }),
  );
  const [orgId, setOrgId] = useState("");
  const [language, setLanguage] = useState<Language>(
    lang === "ca" ? "ca" : "es",
  );
  const [consent, setConsent] = useState(false);
  const selectedOrg = orgId || writable[0]?.id || "";

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
      <div className="field">
        <label htmlFor="org">{dict.recording.organization}</label>
        <select
          id="org"
          required
          value={selectedOrg}
          onChange={(e) => setOrgId(e.target.value)}
        >
          {writable.map((o) => (
            <option key={o.id} value={o.id}>
              {o.is_personal ? dict.orgs.personal : o.name}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="language">{dict.recording.language}</label>
        <select
          id="language"
          value={language}
          onChange={(e) => setLanguage(e.target.value as Language)}
        >
          {(["es", "ca", "en"] as const).map((l) => (
            <option key={l} value={l}>
              {dict.recording.languages[l]}
            </option>
          ))}
        </select>
      </div>

      <fieldset className="consent">
        <legend>{dict.recording.consentTitle}</legend>
        <p>{dict.recording.consentText}</p>
        <label className="checkbox">
          <input
            type="checkbox"
            required
            checked={consent}
            onChange={(e) => setConsent(e.target.checked)}
          />
          {dict.recording.consentCheck}
        </label>
      </fieldset>

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
