"use client";

import {
  ArrowLeft,
  ClipboardList,
  Download,
  FileText,
  FolderInput,
  GraduationCap,
  MoreHorizontal,
  Pencil,
  RotateCcw,
  Sparkles,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Fragment, useCallback, useEffect, useRef, useState } from "react";

import { RequireAuth } from "@/components/auth/RequireAuth";
import { Dialog } from "@/components/Dialog";
import { errorMessage, FormMessage, useAction } from "@/components/forms";
import { Menu, type MenuItem } from "@/components/Menu";
import type { Dictionary, Locale } from "@/i18n/client";
import { format } from "@/i18n/format";
import { api, errorCode, type Schemas, type User } from "@/lib/api/client";
import { useQuery } from "@/lib/api/useQuery";
import { formatDuration } from "@/lib/recorder/useCapture";

import { DocumentPanel, KINDS, kindTab } from "./AiOutputs";
import { canDeleteRecording, DeleteRecording } from "./DeleteRecording";
import { EditableTitle } from "./EditableTitle";
import { ExportPanel } from "./ExportPanel";
import { MOVABLE, MoveRecording, moveTargets } from "./MoveRecording";
import { PlayerBar, PlayerProvider, scrollToSegment, usePlayer } from "./Player";
import { type Language, LANGUAGES } from "./SetupFields";
import {
  SpeakerChips,
  SpeakerName,
  speakerLabels,
  speakerName,
  SpeakerSuggestions,
  useRenameSpeaker,
} from "./SpeakersEditor";
import { transcriptText } from "./contentText";
import { ShareActions } from "./ShareActions";
import { TranscriptHistory } from "./TranscriptHistory";
import {
  languageName,
  type TranslatedSegments,
  type Translation,
  TranslationBar,
} from "./Translation";

type Props = { dict: Dictionary; lang: Locale };
type Recording = Schemas["RecordingDetail"];
type Transcript = Schemas["TranscriptOut"];
type Output = Schemas["AiOutputOut"];
type Kind = Output["kind"];
type Tab = "transcript" | Kind;
type DialogName = "export" | "reprocess" | "move" | "delete";

const LOW_CONFIDENCE = 0.5;
const TABS: Tab[] = ["transcript", ...KINDS];
const TAB_ICONS: Record<Tab, typeof FileText> = {
  transcript: FileText,
  summary: Sparkles,
  minutes: ClipboardList,
  class_notes: GraduationCap,
};

function isTab(value: string | null): value is Tab {
  return TABS.includes(value as Tab);
}

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
  // Igual, con cada avance de una traducción.
  const [translationTick, setTranslationTick] = useState(0);

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
    source.addEventListener("translation", () =>
      setTranslationTick((t) => t + 1),
    );
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

  return { recording, setRecording, loadError, aiTick, translationTick };
}

async function fetchOrganizations() {
  return (await api.GET("/v1/organizations")).data ?? [];
}

function Detail({ dict, lang, user }: Props & { user: User }) {
  const recordingId = useParams<{ recordingId: string }>().recordingId;
  const { recording, setRecording, loadError, aiTick, translationTick } =
    useLiveRecording(recordingId);
  const transcribed = recording?.status === "transcribed";

  // Pestaña en la URL (?tab=minutes): la lista enlaza directamente con cada documento.
  const requestedTab = useSearchParams().get("tab");
  const tab: Tab = isTab(requestedTab) ? requestedTab : "transcript";
  const selectTab = useCallback((next: Tab) => {
    const url = new URL(window.location.href);
    if (next === "transcript") url.searchParams.delete("tab");
    else url.searchParams.set("tab", next);
    url.hash = "";
    window.history.replaceState(null, "", url);
  }, []);
  const [dialog, setDialog] = useState<DialogName | null>(null);

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
  const byKind = Object.fromEntries(outputs.map((o) => [o.kind, o])) as Partial<
    Record<Kind, Output>
  >;
  const onAiRequested = (o: Output) =>
    setOutputs([...outputs.filter((x) => x.kind !== o.kind), o]);

  const fetchTranslations = useCallback(async () => {
    // Dependencias explícitas: avance de una traducción o cambio en la transcripción (una
    // frase corregida deja su traducción desactualizada).
    void translationTick;
    if (!transcript) return [];
    const { data } = await api.GET(
      "/v1/recordings/{recording_id}/translations",
      { params: { path: { recording_id: recordingId } } },
    );
    return data ?? [];
  }, [recordingId, transcript, translationTick]);
  const { data: translations = [], setData: setTranslations } =
    useQuery(fetchTranslations);
  // Idioma en que se lee la transcripción; null = original.
  const [chosen, setChosen] = useState<Language | null>(null);
  const language = chosen === transcript?.language ? null : chosen;
  const translation = translations.find((t) => t.language === language);

  const fetchTranslated = useCallback(async () => {
    void translationTick;
    if (!transcript || !language || !translation?.id) return null;
    const { data } = await api.GET(
      "/v1/recordings/{recording_id}/translations/{language}",
      { params: { path: { recording_id: recordingId, language } } },
    );
    return data ?? null;
  }, [recordingId, transcript, language, translation?.id, translationTick]);
  const { data: translated } = useQuery(fetchTranslated);
  // Mientras llega la del idioma recién elegido, `translated` es la anterior.
  const translatedSegments: TranslatedSegments | null =
    language &&
    translated?.language === language &&
    translated.transcript_id === transcript?.id
      ? new Map(translated.segments.map((s) => [s.idx, s]))
      : null;

  const { data: organizations = [] } = useQuery(fetchOrganizations);

  if (loadError)
    return <p className="alert error">{errorMessage(dict, loadError)}</p>;
  if (!recording) return <p role="status">{dict.common.loading}</p>;

  const dateFormat = new Intl.DateTimeFormat(lang, {
    dateStyle: "medium",
    timeStyle: "short",
  });
  const canManage = canDeleteRecording(user, recording);
  const canReprocess =
    recording.owner_id === user.id &&
    (recording.status === "transcribed" || recording.status === "failed");
  const targets = moveTargets(organizations, recording);
  const menu: MenuItem[] = [
    ...(canReprocess
      ? [
          {
            key: "reprocess",
            label: dict.detail.reprocess,
            icon: <RotateCcw aria-hidden="true" size={18} />,
          },
        ]
      : []),
    ...(MOVABLE.includes(recording.status) && canManage && targets.length
      ? [
          {
            key: "move",
            label: dict.detail.move,
            icon: <FolderInput aria-hidden="true" size={18} />,
          },
        ]
      : []),
    ...(recording.status !== "processing" && canManage
      ? [
          {
            key: "delete",
            label: dict.detail.delete,
            icon: <Trash2 aria-hidden="true" size={18} />,
            danger: true,
          },
        ]
      : []),
  ];
  const readyDocuments = KINDS.filter((k) => byKind[k]?.status === "ready");
  const dialogTitle: Record<DialogName, string> = {
    export: dict.export.title,
    reprocess: dict.detail.reprocess,
    move: dict.move.title,
    delete: dict.detail.deleteHeading,
  };

  return (
    <PlayerProvider
      recordingId={recording.id}
      available={recording.audio_ready}
      reveal={() => selectTab("transcript")}
    >
      <div className="detail">
        <header className="detail-head">
          <Link href={`/${lang}/recordings`} className="back-link">
            <ArrowLeft aria-hidden="true" size={18} /> {dict.recordings.title}
          </Link>
          <div className="detail-title">
            <EditableTitle
              dict={dict}
              recording={recording}
              onSaved={(r) =>
                setRecording((prev) => (prev ? { ...prev, ...r } : r))
              }
            />
            <div className="row detail-actions">
              {transcribed && (
                <button
                  type="button"
                  className="button"
                  onClick={() => setDialog("export")}
                >
                  <Download aria-hidden="true" size={18} />
                  <span className="label-wide">{dict.detail.export}</span>
                </button>
              )}
              <Menu
                label={dict.detail.more}
                icon={<MoreHorizontal aria-hidden="true" size={20} />}
                items={menu}
                onSelect={(key) => setDialog(key as DialogName)}
              />
            </div>
          </div>
          <p className="meta">
            <span>{dateFormat.format(new Date(recording.created_at))}</span>
            <span>{formatDuration(recording.duration_ms)}</span>
            <span className={`badge status-${recording.status}`}>
              {dict.recordings.status[recording.status]}
            </span>
          </p>
        </header>

        <PlayerBar dict={dict} />

        {recording.capture_warning === "audio_gaps" && (
          <p className="alert error">{dict.recording.audioGapsDetected}</p>
        )}

        {!transcribed ? (
          <StatusCard
            dict={dict}
            recording={recording}
            onReprocess={canReprocess ? () => setDialog("reprocess") : null}
          />
        ) : (
          <>
            <Tabs
              dict={dict}
              current={tab}
              outputs={byKind}
              onSelect={selectTab}
            />
            <section
              role="tabpanel"
              id="panel-transcript"
              aria-labelledby="tab-transcript"
              className="card panel"
              hidden={tab !== "transcript"}
            >
              {transcript ? (
                <TranscriptPanel
                  dict={dict}
                  lang={lang}
                  recording={recording}
                  transcript={transcript}
                  onUpdated={setTranscript}
                  language={language}
                  translation={translation}
                  translated={translatedSegments}
                  onLanguage={setChosen}
                  onTranslationRequested={(t: Translation) =>
                    setTranslations([
                      ...translations.filter((x) => x.language !== t.language),
                      t,
                    ])
                  }
                />
              ) : (
                <p role="status">{dict.common.loading}</p>
              )}
            </section>
            {KINDS.map((kind) => (
              <section
                key={kind}
                role="tabpanel"
                id={`panel-${kind}`}
                aria-labelledby={`tab-${kind}`}
                className="card panel"
                hidden={tab !== kind}
              >
                <DocumentPanel
                  dict={dict}
                  lang={lang}
                  kind={kind}
                  output={byKind[kind]}
                  recordingId={recording.id}
                  recordingTitle={recording.title}
                  onRequested={onAiRequested}
                />
              </section>
            ))}
          </>
        )}
      </div>

      <Dialog
        open={dialog !== null}
        title={dialog ? dialogTitle[dialog] : ""}
        closeLabel={dict.common.close}
        onClose={() => setDialog(null)}
      >
        {dialog === "export" && (
          <ExportPanel
            dict={dict}
            recordingId={recording.id}
            documents={readyDocuments}
            original={transcript?.language ?? recording.language}
            translations={translations
              .filter((t) => t.status === "ready")
              .map((t) => t.language)}
          />
        )}
        {dialog === "reprocess" && (
          <Reprocess
            dict={dict}
            recording={recording}
            onQueued={(r) => {
              setRecording(r);
              setDialog(null);
            }}
          />
        )}
        {dialog === "move" && (
          <MoveRecording
            dict={dict}
            recording={recording}
            targets={targets}
            onMoved={setRecording}
          />
        )}
        {dialog === "delete" && (
          <DeleteRecording
            dict={dict}
            lang={lang}
            recording={recording}
            onCancel={() => setDialog(null)}
          />
        )}
      </Dialog>
    </PlayerProvider>
  );
}

/** Pestañas (patrón WAI-ARIA): flechas para moverse; se activa al enfocar. */
function Tabs({
  dict,
  current,
  outputs,
  onSelect,
}: {
  dict: Dictionary;
  current: Tab;
  outputs: Partial<Record<Kind, Output>>;
  onSelect: (tab: Tab) => void;
}) {
  const list = useRef<HTMLDivElement>(null);

  function onKeyDown(e: React.KeyboardEvent) {
    const index = TABS.indexOf(current);
    const to =
      e.key === "ArrowRight"
        ? (index + 1) % TABS.length
        : e.key === "ArrowLeft"
          ? (index - 1 + TABS.length) % TABS.length
          : e.key === "Home"
            ? 0
            : e.key === "End"
              ? TABS.length - 1
              : null;
    if (to === null) return;
    e.preventDefault();
    onSelect(TABS[to]);
    list.current?.querySelector<HTMLButtonElement>(`#tab-${TABS[to]}`)?.focus();
  }

  return (
    <div
      ref={list}
      role="tablist"
      aria-label={dict.detail.tabs}
      className="tabs"
      onKeyDown={onKeyDown}
    >
      {TABS.map((t) => {
        const Icon = TAB_ICONS[t];
        const output = t === "transcript" ? null : outputs[t];
        const missing = t !== "transcript" && !output;
        return (
          <button
            key={t}
            type="button"
            role="tab"
            id={`tab-${t}`}
            aria-controls={`panel-${t}`}
            aria-selected={current === t}
            tabIndex={current === t ? 0 : -1}
            className={missing ? "tab is-empty" : "tab"}
            onClick={() => onSelect(t)}
          >
            <Icon aria-hidden="true" size={18} />
            {t === "transcript" ? dict.detail.transcript : kindTab(dict, t)}
            {output?.status === "pending" && (
              <>
                <span className="tab-dot pending" aria-hidden="true" />
                <span className="visually-hidden">{dict.ai.generating}</span>
              </>
            )}
            {output?.status === "failed" && (
              <>
                <span className="tab-dot failed" aria-hidden="true" />
                <span className="visually-hidden">{dict.ai.failed}</span>
              </>
            )}
          </button>
        );
      })}
    </div>
  );
}

function StatusCard({
  dict,
  recording,
  onReprocess,
}: {
  dict: Dictionary;
  recording: Recording;
  onReprocess: (() => void) | null;
}) {
  switch (recording.status) {
    case "uploading":
      return (
        <div className="card empty-state">
          <p>{dict.detail.uploading}</p>
        </div>
      );
    case "pending":
    case "processing": {
      const text =
        recording.status === "pending"
          ? dict.detail.queued
          : dict.detail.processing;
      return (
        <div className="card empty-state" role="status">
          <FileText aria-hidden="true" size={28} className="muted-icon" />
          <p>{text}</p>
          <progress aria-label={text} />
        </div>
      );
    }
    case "failed":
      return (
        <div className="card empty-state">
          <TriangleAlert aria-hidden="true" size={28} className="danger-icon" />
          <p className="alert error">
            {errorMessage(dict, recording.error_code ?? "unknown")}
          </p>
          {onReprocess && (
            <>
              <p className="hint">{dict.detail.failedHelp}</p>
              <button
                type="button"
                className="button primary"
                onClick={onReprocess}
              >
                <RotateCcw aria-hidden="true" size={18} /> {dict.detail.reprocess}
              </button>
            </>
          )}
        </div>
      );
    default:
      return null;
  }
}

/** Transcripción: hablantes (se renombran pulsándolos), idioma y frases. */
function TranscriptPanel({
  dict,
  lang,
  recording,
  transcript,
  onUpdated,
  language,
  translation,
  translated,
  onLanguage,
  onTranslationRequested,
}: {
  dict: Dictionary;
  lang: Locale;
  recording: Recording;
  transcript: Transcript;
  onUpdated: (t: Transcript) => void;
  language: Language | null;
  translation: Translation | undefined;
  translated: TranslatedSegments | null;
  onLanguage: (l: Language | null) => void;
  onTranslationRequested: (t: Translation) => void;
}) {
  // Qué nombre se está editando: "chip:H1" en las fichas o "line:12:H1" en una frase.
  const [editingSpeaker, setEditingSpeaker] = useState<string | null>(null);
  const [duplicates, setDuplicates] = useState(false);
  const speakers = useRenameSpeaker(dict, recording.id, onUpdated);
  const labels = speakerLabels(transcript);
  const shareTitle = `${recording.title} · ${dict.detail.transcript}${
    translated && language ? ` (${languageName(dict, language)})` : ""
  }`;

  return (
    <div className="stack">
      <div className="panel-toolbar">
        <SpeakerChips
          dict={dict}
          labels={labels}
          names={transcript.speaker_names}
          editing={editingSpeaker}
          onEdit={(key) => {
            speakers.setRenamed(false);
            setEditingSpeaker(key);
          }}
          onRename={speakers.rename}
          onDone={() => setEditingSpeaker(null)}
          canFindDuplicates={labels.length > 1}
          onFindDuplicates={() => setDuplicates(true)}
        />
        {transcript.segments.length > 0 && (
          <div className="row share-actions">
            <ShareActions
              dict={dict}
              title={shareTitle}
              text={() =>
                transcriptText(
                  shareTitle,
                  transcript.segments.map((s) => ({
                    start_ms: s.start_ms,
                    speaker: speakerName(dict, s.speaker, transcript.speaker_names),
                    text: translated?.get(s.idx)?.text ?? s.text,
                  })),
                )
              }
            />
          </div>
        )}
        {transcript.segments.length > 0 && (
          <TranslationBar
            dict={dict}
            recordingId={recording.id}
            original={transcript.language}
            language={language}
            translation={translation}
            onLanguage={onLanguage}
            onRequested={onTranslationRequested}
          />
        )}
      </div>
      <FormMessage
        error={speakers.error}
        success={speakers.renamed ? dict.speakers.renamed : null}
      />
      {duplicates && (
        <SpeakerSuggestions
          dict={dict}
          recordingId={recording.id}
          transcript={transcript}
          onMerged={onUpdated}
          onClose={() => setDuplicates(false)}
        />
      )}
      <TranscriptView
        dict={dict}
        transcript={transcript}
        translated={translated}
        language={language}
        gaps={recording.audio_gaps}
        recordingId={recording.id}
        onUpdated={onUpdated}
        editingSpeaker={editingSpeaker}
        onEditSpeaker={(key) => {
          speakers.setRenamed(false);
          setEditingSpeaker(key);
        }}
        onRenameSpeaker={speakers.rename}
        onSpeakerDone={() => setEditingSpeaker(null)}
      />
      <footer className="panel-foot">
        <p className="hint">
          {format(dict.detail.version, {
            version: transcript.version,
            provider: transcript.provider,
            model: transcript.model,
          })}
        </p>
        <TranscriptHistory
          dict={dict}
          lang={lang}
          recordingId={recording.id}
          segments={transcript.segments}
          version={transcript.id}
        />
      </footer>
    </div>
  );
}
type AudioGap = Schemas["AudioGapOut"];

function gapText(dict: Dictionary, gap: AudioGap): string {
  const seconds = Math.round((gap.lost_ms ?? 0) / 1000);
  switch (gap.kind) {
    case "missing":
      return format(dict.detail.gapMissing, {
        count: gap.segments ?? 0,
        seconds,
      });
    case "dropped":
      return format(dict.detail.gapDropped, { seconds });
    default:
      return dict.detail.gapInterruption;
  }
}

/** Aviso de audio perdido en su sitio de la transcripción: no es una frase ni se edita. */
function GapMarker({ dict, gap }: { dict: Dictionary; gap: AudioGap }) {
  return (
    <li className="audio-gap" role="note">
      <time
        className="timestamp"
        dateTime={`PT${Math.floor(gap.start_ms / 1000)}S`}
      >
        {formatDuration(gap.start_ms)}
      </time>
      <span>
        <TriangleAlert
          aria-hidden="true"
          size={16}
          style={{ verticalAlign: "-3px" }}
        />{" "}
        {gapText(dict, gap)}
      </span>
    </li>
  );
}

function TranscriptView({
  dict,
  transcript,
  translated,
  language,
  gaps,
  recordingId,
  onUpdated,
  editingSpeaker,
  onEditSpeaker,
  onRenameSpeaker,
  onSpeakerDone,
}: {
  dict: Dictionary;
  transcript: Transcript;
  /** Texto traducido por frase; null = se muestra el original. */
  translated: TranslatedSegments | null;
  language: Language | null;
  gaps: AudioGap[];
  recordingId: string;
  onUpdated: (t: Transcript) => void;
  editingSpeaker: string | null;
  onEditSpeaker: (key: string) => void;
  onRenameSpeaker: (label: string, name: string) => Promise<boolean>;
  onSpeakerDone: () => void;
}) {
  const player = usePlayer();
  const [editingIdx, setEditingIdx] = useState<number | null>(null);
  const labels = speakerLabels(transcript);
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

  if (segments.length === 0) return <p>{dict.detail.empty}</p>;

  return (
    <ol
      className="transcript"
      lang={translated && language ? language : transcript.language}
    >
      {segments.map((s, i) => {
        const low = s.confidence !== null && s.confidence < LOW_CONFIDENCE;
        const translation = translated?.get(s.idx);
        const note = !translated
          ? null
          : !translation
            ? dict.translation.untranslated
            : translation.outdated
              ? dict.translation.outdated
              : null;
        // Cada hueco, antes de la primera frase que empieza después de él.
        const previous = i > 0 ? segments[i - 1].start_ms : -1;
        const before = gaps.filter(
          (g) => g.start_ms > previous && g.start_ms <= s.start_ms,
        );
        const after =
          i === segments.length - 1
            ? gaps.filter((g) => g.start_ms > s.start_ms)
            : [];
        const editing = editingIdx === s.idx && !translated;
        return (
          <Fragment key={s.idx}>
            {before.map((g) => (
              <GapMarker key={`gap-${g.start_ms}-${g.kind}`} dict={dict} gap={g} />
            ))}
            <li
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
                  className="timestamp"
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
              <div className="segment">
                {s.speaker && !editing && (
                  <SpeakerName
                    dict={dict}
                    label={s.speaker}
                    names={transcript.speaker_names}
                    editing={editingSpeaker === `line:${s.idx}`}
                    onEdit={() => onEditSpeaker(`line:${s.idx}`)}
                    onRename={onRenameSpeaker}
                    onDone={onSpeakerDone}
                  />
                )}
                {editing ? (
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
                  <p className="segment-text">
                    <span
                      lang={
                        translated && !translation
                          ? transcript.language
                          : undefined
                      }
                    >
                      {translation?.text ?? s.text}
                    </span>
                    {note && <span className="badge">{note}</span>}
                    {low && (
                      <span className="visually-hidden">
                        {" "}
                        ({dict.detail.lowConfidence})
                      </span>
                    )}
                  </p>
                )}
              </div>
              {!editing && !translated ? (
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
              ) : (
                <span />
              )}
            </li>
            {after.map((g) => (
              <GapMarker key={`gap-${g.start_ms}-${g.kind}`} dict={dict} gap={g} />
            ))}
          </Fragment>
        );
      })}
    </ol>
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
    <form className="stack" onSubmit={submit}>
      <p className="hint">{dict.detail.reprocessHint}</p>
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
              : (engine || current) === "elevenlabs"
                ? dict.detail.engineElevenlabsHint
                : dict.detail.engineOpenaiHint}
          </p>
        </div>
      )}
      <FormMessage
        error={error}
        success={done ? dict.detail.reprocessQueued : null}
      />
      <div className="row dialog-actions">
        <button type="submit" className="button primary" disabled={pending}>
          {dict.detail.reprocess}
        </button>
      </div>
    </form>
  );
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
