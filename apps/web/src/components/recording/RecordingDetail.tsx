"use client";

import { ArrowLeft, Pencil } from "lucide-react";
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

import { AiGenerate, AiOutputSection } from "./AiOutputs";
import { canDeleteRecording, DeleteRecording } from "./DeleteRecording";
import { EditableTitle } from "./EditableTitle";
import { ExportPanel } from "./ExportPanel";
import { TranscriptHistory } from "./TranscriptHistory";
import { PlayerProvider, scrollToSegment, usePlayer } from "./Player";
import { type Language, LANGUAGES } from "./SetupFields";
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
  const onAiRequested = (o: (typeof outputs)[number]) =>
    setOutputs([...outputs.filter((x) => x.kind !== o.kind), o]);

  if (loadError)
    return <p className="alert error">{errorMessage(dict, loadError)}</p>;
  if (!recording) return <p role="status">{dict.common.loading}</p>;

  const dateFormat = new Intl.DateTimeFormat(lang, {
    dateStyle: "medium",
    timeStyle: "short",
  });

  return (
    <PlayerProvider
      dict={dict}
      recordingId={recording.id}
      available={recording.audio_ready}
    >
      <div className="stack-lg">
        <div>
          <Link
            href={`/${lang}/recordings`}
            className="icon-link back-link"
            aria-label={dict.detail.back}
            title={dict.detail.back}
          >
            <ArrowLeft aria-hidden="true" size={22} />
          </Link>
          <EditableTitle
            dict={dict}
            recording={recording}
            onSaved={(r) =>
              setRecording((prev) => (prev ? { ...prev, ...r } : r))
            }
          />
          <p className="hint">
            {dateFormat.format(new Date(recording.created_at))} ·{" "}
            {formatDuration(recording.duration_ms)} ·{" "}
            <span className="badge">
              {dict.recordings.status[recording.status]}
            </span>
          </p>
        </div>

        {transcribed &&
          (["summary", "minutes"] as const).map(
            (kind) =>
              byKind[kind] && (
                <AiOutputSection
                  key={kind}
                  dict={dict}
                  lang={lang}
                  kind={kind}
                  output={byKind[kind]}
                  recordingId={recording.id}
                  canGenerate
                  onRequested={onAiRequested}
                />
              ),
          )}
        {transcribed && (
          <AiGenerate
            dict={dict}
            recordingId={recording.id}
            existing={outputs.map((o) => o.kind)}
            onRequested={onAiRequested}
          />
        )}

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
          {transcript && (
            <TranscriptView
              dict={dict}
              transcript={transcript}
              recordingId={recording.id}
              onUpdated={setTranscript}
            />
          )}
          {transcript && (
            <TranscriptHistory
              dict={dict}
              lang={lang}
              recordingId={recording.id}
              segments={transcript.segments}
              version={transcript.id}
            />
          )}
        </section>

        {transcribed && <ExportPanel dict={dict} recordingId={recording.id} />}

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

        {recording.status !== "processing" &&
          canDeleteRecording(user, recording) && (
            <DeleteRecording dict={dict} lang={lang} recording={recording} />
          )}
      </div>
    </PlayerProvider>
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
  recordingId,
  onUpdated,
}: {
  dict: Dictionary;
  transcript: Transcript;
  recordingId: string;
  onUpdated: (t: Transcript) => void;
}) {
  const player = usePlayer();
  const [editingIdx, setEditingIdx] = useState<number | null>(null);
  const labels = [...new Set(segments_speakers(transcript))];
  const segments = transcript.segments;
  // Frase que suena ahora: la última que empieza antes de la posición del audio.
  let active = -1;
  for (const s of segments) {
    if (s.start_ms <= player.currentMs) active = s.idx;
    else break;
  }

  // Al abrir un enlace #seg-N (búsqueda, acta), ir a esa frase cuando ya está cargada.
  useEffect(() => {
    const match = /^#seg-(\d+)$/.exec(window.location.hash);
    if (match) scrollToSegment(Number(match[1]));
  }, [transcript.id]);

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
                className={
                  [low && "low-confidence", s.idx === active && "playing"]
                    .filter(Boolean)
                    .join(" ") || undefined
                }
                aria-current={s.idx === active ? "true" : undefined}
              >
                {player.available ? (
                  <button
                    type="button"
                    className="timestamp link-button"
                    onClick={() => player.seek(s.start_ms)}
                    aria-label={format(dict.player.playFrom, {
                      time: formatDuration(s.start_ms),
                    })}
                  >
                    {formatDuration(s.start_ms)}
                  </button>
                ) : (
                  <time
                    className="timestamp"
                    dateTime={`PT${Math.floor(s.start_ms / 1000)}S`}
                  >
                    {formatDuration(s.start_ms)}
                  </time>
                )}
                {editingIdx === s.idx ? (
                  <SegmentEditor
                    dict={dict}
                    recordingId={recordingId}
                    segment={s}
                    labels={labels}
                    names={transcript.speaker_names}
                    onDone={(t) => {
                      setEditingIdx(null);
                      if (t) onUpdated(t);
                    }}
                  />
                ) : (
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
                )}
                {editingIdx !== s.idx && (
                  <button
                    type="button"
                    className="icon-link segment-edit"
                    onClick={() => setEditingIdx(s.idx)}
                    aria-label={format(dict.edit.editSegment, {
                      time: formatDuration(s.start_ms),
                    })}
                    title={dict.edit.edit}
                  >
                    <Pencil aria-hidden="true" size={16} />
                  </button>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </>
  );
}

type Engine = Schemas["EnginesOut"]["default"];

async function fetchEngines() {
  const { data } = await api.GET("/v1/transcription-engines");
  return data ?? null;
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
  const { data: engines } = useQuery(fetchEngines);
  const [engine, setEngine] = useState<Engine | "">("");
  const current: Engine | undefined =
    (recording.transcription_engine as Engine | null) ?? engines?.default;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const data = await run(() =>
      api.POST("/v1/recordings/{recording_id}/reprocess", {
        params: { path: { recording_id: recording.id } },
        body: { language, ...(engine ? { engine } : {}) },
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
          {LANGUAGES.map((l) => (
            <option key={l} value={l}>
              {dict.recording.languages[l]}
            </option>
          ))}
        </select>
      </div>
      {engines && engines.available.length > 1 && (
        <div className="field">
          <label htmlFor="reprocess-engine">{dict.detail.engine}</label>
          <select
            id="reprocess-engine"
            value={engine || current}
            onChange={(e) => setEngine(e.target.value as Engine)}
            aria-describedby="reprocess-engine-hint"
          >
            {engines.available.map((name) => (
              <option key={name} value={name}>
                {dict.detail.engines[name]}
              </option>
            ))}
          </select>
          <p id="reprocess-engine-hint" className="hint">
            {(engine || current) === "local"
              ? engines.local_speakers
                ? dict.detail.engineLocalHint
                : dict.detail.engineLocalNoSpeakersHint
              : dict.detail.engineOpenaiHint}
          </p>
        </div>
      )}
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

function segments_speakers(transcript: Transcript): string[] {
  return transcript.segments
    .map((s) => s.speaker)
    .filter((x): x is string => Boolean(x));
}

/** Corrige el texto y el hablante de una frase. El cambio queda en el historial. */
function SegmentEditor({
  dict,
  recordingId,
  segment,
  labels,
  names,
  onDone,
}: {
  dict: Dictionary;
  recordingId: string;
  segment: Transcript["segments"][number];
  labels: string[];
  names: Record<string, string>;
  onDone: (t: Transcript | null) => void;
}) {
  const [text, setText] = useState(segment.text);
  const [speaker, setSpeaker] = useState(segment.speaker ?? "");
  const { pending, error, run } = useAction(dict);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const data = await run(() =>
      api.PATCH("/v1/recordings/{recording_id}/transcript/segments/{idx}", {
        params: { path: { recording_id: recordingId, idx: segment.idx } },
        body: { text, ...(labels.length ? { speaker: speaker || null } : {}) },
      }),
    );
    if (data) onDone(data);
  }

  return (
    <form className="stack segment-form" onSubmit={save}>
      {labels.length > 0 && (
        <div className="field">
          <label htmlFor={`spk-${segment.idx}`}>{dict.edit.speaker}</label>
          <select
            id={`spk-${segment.idx}`}
            value={speaker}
            onChange={(e) => setSpeaker(e.target.value)}
          >
            <option value="">{dict.edit.noSpeaker}</option>
            {labels.map((l) => (
              <option key={l} value={l}>
                {speakerName(dict, l, names)}
              </option>
            ))}
          </select>
        </div>
      )}
      <label className="visually-hidden" htmlFor={`txt-${segment.idx}`}>
        {dict.edit.text}
      </label>
      <textarea
        id={`txt-${segment.idx}`}
        rows={3}
        required
        maxLength={5000}
        value={text}
        autoFocus
        onChange={(e) => setText(e.target.value)}
      />
      <FormMessage error={error} />
      <div className="row">
        <button
          type="submit"
          className="button primary small"
          disabled={pending}
        >
          {dict.edit.save}
        </button>
        <button
          type="button"
          className="button small"
          onClick={() => onDone(null)}
        >
          {dict.edit.cancel}
        </button>
      </div>
    </form>
  );
}
