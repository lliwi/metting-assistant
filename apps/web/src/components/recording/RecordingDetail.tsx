"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { RequireAuth } from "@/components/auth/RequireAuth";
import { errorMessage, FormMessage, useAction } from "@/components/forms";
import type { Dictionary, Locale } from "@/i18n/client";
import { format } from "@/i18n/format";
import { api, errorCode, type Schemas, type User } from "@/lib/api/client";
import { useQuery } from "@/lib/api/useQuery";
import { formatDuration } from "@/lib/recorder/useCapture";

import { AiOutputSection } from "./AiOutputs";
import type { Language } from "./SetupFields";
import { speakerName, SpeakersEditor } from "./SpeakersEditor";

type Props = { dict: Dictionary; lang: Locale };
type Recording = Schemas["RecordingDetail"];
type Transcript = Schemas["TranscriptOut"];

const LOW_CONFIDENCE = 0.5;

export function RecordingDetail({ dict, lang }: Props) {
  return (
    <RequireAuth lang={lang} loadingLabel={dict.common.loading}>
      {(user) => <Detail dict={dict} lang={lang} user={user} />}
    </RequireAuth>
  );
}

/** Estado de la grabación en vivo: carga inicial y cambios por SSE. */
function useLiveRecording(recordingId: string) {
  const [recording, setRecording] = useState<Recording | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Se incrementa con cada cambio de un documento de IA: dispara su recarga.
  const [aiTick, setAiTick] = useState(0);

  useEffect(() => {
    const source = new EventSource(`/api/v1/recordings/${recordingId}/events`);
    source.addEventListener("recording", (e) =>
      setRecording(JSON.parse(e.data) as Recording),
    );
    source.addEventListener("status", (e) => {
      const change = JSON.parse(e.data) as {
        status: Recording["status"];
        error?: string;
      };
      setRecording((r) =>
        r
          ? {
              ...r,
              status: change.status,
              error_code: change.error ?? r.error_code,
            }
          : r,
      );
    });
    source.addEventListener("ai_output", () => setAiTick((t) => t + 1));
    source.onerror = () => {
      // 404/401: EventSource no da el código; se consulta para mostrar el error real.
      if (source.readyState === EventSource.CLOSED) {
        void api
          .GET("/v1/recordings/{recording_id}", {
            params: { path: { recording_id: recordingId } },
          })
          .then(({ data, error }) => {
            if (data) setRecording(data);
            else setLoadError(errorCode(error));
          });
      }
    };
    return () => source.close();
  }, [recordingId]);

  return { recording, setRecording, loadError, aiTick };
}

function Detail({ dict, lang, user }: Props & { user: User }) {
  const recordingId = useParams<{ recordingId: string }>().recordingId;
  const { recording, setRecording, loadError, aiTick } =
    useLiveRecording(recordingId);
  const transcribed = recording?.status === "transcribed";

  const fetchTranscript = useCallback(async () => {
    if (!transcribed) return null;
    const { data } = await api.GET("/v1/recordings/{recording_id}/transcript", {
      params: { path: { recording_id: recordingId } },
    });
    return data ?? null;
  }, [recordingId, transcribed]);
  const { data: transcript, setData: setTranscript } =
    useQuery(fetchTranscript);

  const fetchOutputs = useCallback(async () => {
    if (!transcribed) return [];
    void aiTick; // dependencia explícita: recargar al recibir un evento ai_output
    const { data } = await api.GET("/v1/recordings/{recording_id}/ai-outputs", {
      params: { path: { recording_id: recordingId } },
    });
    return data ?? [];
  }, [recordingId, transcribed, aiTick]);
  const { data: outputs = [], setData: setOutputs } = useQuery(fetchOutputs);
  const byKind = Object.fromEntries(outputs.map((o) => [o.kind, o]));

  if (loadError)
    return <p className="alert error">{errorMessage(dict, loadError)}</p>;
  if (!recording) return <p role="status">{dict.common.loading}</p>;

  const dateFormat = new Intl.DateTimeFormat(lang, {
    dateStyle: "medium",
    timeStyle: "short",
  });

  return (
    <div className="stack-lg">
      <p>
        <Link href={`/${lang}/recordings`}>← {dict.detail.back}</Link>
      </p>
      <div>
        <h1>{recording.title}</h1>
        <p className="hint">
          {dateFormat.format(new Date(recording.created_at))} ·{" "}
          {formatDuration(recording.duration_ms)} ·{" "}
          <span className="badge">
            {dict.recordings.status[recording.status]}
          </span>
        </p>
      </div>

      {transcribed &&
        (["summary", "minutes"] as const).map((kind) => (
          <AiOutputSection
            key={kind}
            dict={dict}
            kind={kind}
            output={byKind[kind]}
            recordingId={recording.id}
            canGenerate
            onRequested={(o) =>
              setOutputs([...outputs.filter((x) => x.kind !== o.kind), o])
            }
          />
        ))}

      <section
        className="card stack"
        aria-labelledby="transcript-heading"
        aria-busy={!transcribed}
      >
        <h2 id="transcript-heading">{dict.detail.transcript}</h2>
        {recording.capture_warning === "audio_gaps" && (
          <p className="alert error">{dict.recording.audioGapsDetected}</p>
        )}
        <StatusMessage dict={dict} recording={recording} />
        {transcript && <TranscriptView dict={dict} transcript={transcript} />}
      </section>

      {transcript && (
        <SpeakersEditor
          key={transcript.id}
          dict={dict}
          recordingId={recording.id}
          transcript={transcript}
          onSaved={setTranscript}
        />
      )}

      {recording.owner_id === user.id &&
        (recording.status === "transcribed" ||
          recording.status === "failed") && (
          <Reprocess
            dict={dict}
            recording={recording}
            onQueued={setRecording}
          />
        )}
    </div>
  );
}

function StatusMessage({
  dict,
  recording,
}: {
  dict: Dictionary;
  recording: Recording;
}) {
  switch (recording.status) {
    case "uploading":
      return <p>{dict.detail.uploading}</p>;
    case "pending":
      return (
        <p role="status">
          {dict.detail.queued} <progress aria-label={dict.detail.queued} />
        </p>
      );
    case "processing":
      return (
        <p role="status">
          {dict.detail.processing}{" "}
          <progress aria-label={dict.detail.processing} />
        </p>
      );
    case "failed":
      return (
        <p className="alert error">
          {errorMessage(dict, recording.error_code ?? "unknown")}
        </p>
      );
    default:
      return null;
  }
}

function TranscriptView({
  dict,
  transcript,
}: {
  dict: Dictionary;
  transcript: Transcript;
}) {
  return (
    <>
      <p className="hint">
        {format(dict.detail.version, {
          version: transcript.version,
          provider: transcript.provider,
          model: transcript.model,
        })}
      </p>
      {transcript.segments.length === 0 ? (
        <p>{dict.detail.empty}</p>
      ) : (
        <ol className="transcript" lang={transcript.language}>
          {transcript.segments.map((s) => {
            const low = s.confidence !== null && s.confidence < LOW_CONFIDENCE;
            return (
              <li
                key={s.idx}
                id={`seg-${s.idx}`}
                className={low ? "low-confidence" : undefined}
              >
                <time
                  className="timestamp"
                  dateTime={`PT${Math.floor(s.start_ms / 1000)}S`}
                >
                  {formatDuration(s.start_ms)}
                </time>
                <span>
                  {s.speaker && (
                    <strong className="speaker">
                      {speakerName(dict, s.speaker, transcript.speaker_names)}
                      :{" "}
                    </strong>
                  )}
                  {s.text}
                  {low && (
                    <span className="visually-hidden">
                      {" "}
                      ({dict.detail.lowConfidence})
                    </span>
                  )}
                </span>
              </li>
            );
          })}
        </ol>
      )}
    </>
  );
}

function Reprocess({
  dict,
  recording,
  onQueued,
}: {
  dict: Dictionary;
  recording: Recording;
  onQueued: (r: Recording) => void;
}) {
  const { pending, error, run } = useAction(dict);
  const [language, setLanguage] = useState<Language>(
    recording.language as Language,
  );
  const [done, setDone] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const data = await run(() =>
      api.POST("/v1/recordings/{recording_id}/reprocess", {
        params: { path: { recording_id: recording.id } },
        body: { language },
      }),
    );
    if (data) {
      setDone(true);
      onQueued(data);
    }
  }

  return (
    <form className="card stack" onSubmit={submit}>
      <h2>{dict.detail.reprocess}</h2>
      <div className="field">
        <label htmlFor="reprocess-language">
          {dict.detail.reprocessLanguage}
        </label>
        <select
          id="reprocess-language"
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
      <FormMessage
        error={error}
        success={done ? dict.detail.reprocessQueued : null}
      />
      <button type="submit" className="button" disabled={pending}>
        {dict.detail.reprocess}
      </button>
    </form>
  );
}
