"use client";

import { Pencil, RefreshCw, Sparkles } from "lucide-react";
import { useState } from "react";

import { errorMessage, FormMessage, useAction } from "@/components/forms";
import type { Dictionary, Locale } from "@/i18n/client";
import { format } from "@/i18n/format";
import { api, type Schemas } from "@/lib/api/client";
import { formatDuration } from "@/lib/recorder/useCapture";

import { AiOutputEditor } from "./AiOutputEditor";
import { documentText } from "./contentText";
import { type Language } from "./SetupFields";
import { LanguagePicker, languageName } from "./Translation";
import { ShareActions } from "./ShareActions";
import { usePlayer } from "./Player";

type Output = Schemas["AiOutputOut"];
export type DocumentTranslation = Schemas["DocumentTranslationOut"];
type Kind = Output["kind"];
type Ref = { idx: number; start_ms: number };
type Item = {
  texto?: string;
  descripcion?: string;
  segmentos?: Ref[];
} & Record<string, unknown>;

/** Enlaces a los segmentos de la transcripción de donde sale cada punto. */
function Sources({ dict, refs }: { dict: Dictionary; refs?: Ref[] }) {
  const player = usePlayer();
  if (!refs?.length) return null;
  return (
    <span className="sources">
      {refs.map((r) => (
        <a
          key={r.idx}
          href={`#seg-${r.idx}`}
          onClick={(e) => {
            // Salta al audio y a la frase; el enlace sigue funcionando sin JavaScript.
            e.preventDefault();
            player.seek(r.start_ms, r.idx);
          }}
          className="source-link"
          aria-label={format(dict.ai.goTo, {
            time: formatDuration(r.start_ms),
          })}
        >
          {formatDuration(r.start_ms)}
        </a>
      ))}
    </span>
  );
}

function ItemList({
  dict,
  items,
  render,
}: {
  dict: Dictionary;
  items?: Item[];
  render: (i: Item) => React.ReactNode;
}) {
  if (!items?.length) return <p className="hint">{dict.ai.empty}</p>;
  return (
    <ul className="ai-list">
      {items.map((item, i) => (
        <li key={i}>
          {render(item)} <Sources dict={dict} refs={item.segmentos} />
        </li>
      ))}
    </ul>
  );
}

function Summary({
  dict,
  content,
}: {
  dict: Dictionary;
  content: Record<string, unknown>;
}) {
  const paragraphs = String(content.resumen ?? "")
    .split(/\n+/)
    .filter(Boolean);
  return (
    <>
      {paragraphs.map((p, i) => (
        <p key={i}>{p}</p>
      ))}
      <h3>{dict.ai.keyPoints}</h3>
      <ItemList
        dict={dict}
        items={content.puntos_clave as Item[]}
        render={(i) => i.texto}
      />
    </>
  );
}

function Minutes({
  dict,
  content,
}: {
  dict: Dictionary;
  content: Record<string, unknown>;
}) {
  const attendees = (content.asistentes as string[] | undefined) ?? [];
  const topics =
    (content.temas as
      (Item & { titulo?: string; resumen?: string })[] | undefined) ?? [];
  return (
    <>
      {typeof content.titulo === "string" && (
        <p className="minutes-title">{content.titulo}</p>
      )}
      <h3>{dict.ai.attendees}</h3>
      <p>{attendees.length ? attendees.join(", ") : dict.ai.empty}</p>

      <h3>{dict.ai.topics}</h3>
      {topics.length ? (
        topics.map((t, i) => (
          <div key={i} className="topic">
            <h4>{t.titulo}</h4>
            <p>
              {t.resumen} <Sources dict={dict} refs={t.segmentos} />
            </p>
          </div>
        ))
      ) : (
        <p className="hint">{dict.ai.empty}</p>
      )}

      <h3>{dict.ai.decisions}</h3>
      <ItemList
        dict={dict}
        items={content.decisiones as Item[]}
        render={(i) => i.texto}
      />

      <h3>{dict.ai.tasks}</h3>
      <ItemList
        dict={dict}
        items={content.tareas as Item[]}
        render={(i) => (
          <>
            {i.descripcion}
            {(i.responsable || i.fecha_limite) && (
              <span className="hint">
                {" "}
                (
                {[
                  i.responsable && `${dict.ai.owner}: ${String(i.responsable)}`,
                  i.fecha_limite && `${dict.ai.due}: ${String(i.fecha_limite)}`,
                ]
                  .filter(Boolean)
                  .join(" · ")}
                )
              </span>
            )}
          </>
        )}
      />

      <h3>{dict.ai.openQuestions}</h3>
      <ItemList
        dict={dict}
        items={content.preguntas_abiertas as Item[]}
        render={(i) => i.texto}
      />
    </>
  );
}

export const KINDS: Kind[] = ["summary", "minutes", "class_notes"];

function ClassNotes({
  dict,
  content,
}: {
  dict: Dictionary;
  content: Record<string, unknown>;
}) {
  const sections =
    (content.secciones as
      (Item & { titulo?: string; contenido?: string })[] | undefined) ?? [];
  const paragraphs = (text: unknown) =>
    String(text ?? "")
      .split(/\n+/)
      .filter(Boolean);
  return (
    <>
      {typeof content.titulo === "string" && (
        <p className="minutes-title">{content.titulo}</p>
      )}
      {paragraphs(content.introduccion).map((p, i) => (
        <p key={i}>{p}</p>
      ))}
      {sections.map((s, i) => (
        <div key={i} className="topic">
          <h3>{s.titulo}</h3>
          {paragraphs(s.contenido).map((p, j, all) => (
            <p key={j}>
              {p}{" "}
              {j === all.length - 1 && (
                <Sources dict={dict} refs={s.segmentos} />
              )}
            </p>
          ))}
        </div>
      ))}

      <h3>{dict.ai.concepts}</h3>
      <ItemList
        dict={dict}
        items={content.conceptos as Item[]}
        render={(i) => (
          <>
            <strong>{String(i.termino ?? "")}:</strong>{" "}
            {String(i.definicion ?? "")}
          </>
        )}
      />

      <h3>{dict.ai.examples}</h3>
      <ItemList
        dict={dict}
        items={content.ejemplos as Item[]}
        render={(i) => i.texto}
      />

      <h3>{dict.ai.review}</h3>
      <ItemList
        dict={dict}
        items={content.para_repasar as Item[]}
        render={(i) => i.texto}
      />

      <h3>{dict.ai.homework}</h3>
      <ItemList
        dict={dict}
        items={content.deberes as Item[]}
        render={(i) => (
          <>
            {i.descripcion}
            {i.fecha_limite && (
              <span className="hint">
                {" "}
                ({dict.ai.due}: {String(i.fecha_limite)})
              </span>
            )}
          </>
        )}
      />
    </>
  );
}

/** Contenido de un documento según su tipo. */
function Content({
  dict,
  kind,
  content,
}: {
  dict: Dictionary;
  kind: Kind;
  content: Record<string, unknown>;
}) {
  switch (kind) {
    case "summary":
      return <Summary dict={dict} content={content} />;
    case "minutes":
      return <Minutes dict={dict} content={content} />;
    case "class_notes":
      return <ClassNotes dict={dict} content={content} />;
  }
}

export function kindTitle(dict: Dictionary, kind: Kind): string {
  return {
    summary: dict.ai.summary,
    minutes: dict.ai.minutes,
    class_notes: dict.ai.classNotes,
  }[kind];
}

/** Nombre corto para la pestaña. */
export function kindTab(dict: Dictionary, kind: Kind): string {
  return {
    summary: dict.ai.summary,
    minutes: dict.ai.tabMinutes,
    class_notes: dict.ai.tabClassNotes,
  }[kind];
}

const EMPTY: Record<
  Kind,
  {
    title: keyof Dictionary["ai"];
    text: keyof Dictionary["ai"];
    action: keyof Dictionary["ai"];
  }
> = {
  summary: {
    title: "emptySummary",
    text: "describeSummary",
    action: "generateSummary",
  },
  minutes: {
    title: "emptyMinutes",
    text: "describeMinutes",
    action: "generateMinutes",
  },
  class_notes: {
    title: "emptyClassNotes",
    text: "describeClassNotes",
    action: "generateClassNotes",
  },
};

/** Pestaña de un documento: vacío (con botón para generarlo), generándose, fallido o listo. */
export function DocumentPanel({
  dict,
  lang,
  kind,
  output,
  recordingId,
  recordingTitle,
  original,
  language,
  onLanguage,
  translations,
  onRequested,
  onTranslationRequested,
}: {
  dict: Dictionary;
  lang: Locale;
  kind: Kind;
  output: Output | undefined;
  recordingId: string;
  recordingTitle: string;
  /** Idioma del documento (el de la transcripción). */
  original: string;
  /** Idioma de lectura elegido; null = original. */
  language: Language | null;
  onLanguage: (language: Language | null) => void;
  translations: DocumentTranslation[];
  onRequested: (o: Output) => void;
  onTranslationRequested: (t: DocumentTranslation) => void;
}) {
  const { pending, error, run } = useAction(dict);
  const translating = useAction(dict);
  const [editing, setEditing] = useState(false);
  const generating = output?.status === "pending";
  const ready = output?.status === "ready";
  const empty = EMPTY[kind];

  async function generate() {
    const data = await run(() =>
      api.POST("/v1/recordings/{recording_id}/ai-outputs", {
        params: { path: { recording_id: recordingId } },
        body: { kind },
      }),
    );
    if (data) onRequested(data);
  }

  if (!output || generating) {
    return (
      <div className="empty-state" aria-busy={generating}>
        <Sparkles aria-hidden="true" size={28} className="muted-icon" />
        <h2>{generating ? dict.ai.generating : dict.ai[empty.title]}</h2>
        <p className="hint">
          {generating ? dict.ai.generatingHint : dict.ai[empty.text]}
        </p>
        {generating ? (
          <progress aria-label={dict.ai.generating} />
        ) : (
          <button
            type="button"
            className="button primary"
            onClick={generate}
            disabled={pending}
          >
            <Sparkles aria-hidden="true" size={18} /> {dict.ai[empty.action]}
          </button>
        )}
        <FormMessage error={error} />
      </div>
    );
  }

  const translation = language
    ? translations.find((t) => t.language === language)
    : undefined;
  const translated = translation?.status === "ready" ? translation : undefined;
  const content = translated?.content ?? output.content;
  const title = `${recordingTitle} · ${kindTitle(dict, kind)}${
    translated ? ` (${languageName(dict, translated.language)})` : ""
  }`;

  async function translate() {
    if (!language || !output) return;
    const outputId = output.id;
    const data = await translating.run(() =>
      api.POST("/v1/ai-outputs/{output_id}/translations", {
        params: { path: { output_id: outputId } },
        body: { language },
      }),
    );
    if (data) onTranslationRequested(data);
  }

  return (
    <div className="stack">
      <div className="panel-toolbar">
        <h2 className="panel-title">{kindTitle(dict, kind)}</h2>
        {!editing && (
          <div className="row wrap toolbar-actions">
            {ready && (
              <ShareActions
                dict={dict}
                title={title}
                text={() => documentText(dict, kind, title, content)}
              />
            )}
            {ready && !translated && (
              <button
                type="button"
                className="button small ghost"
                onClick={() => setEditing(true)}
                title={dict.edit.edit}
              >
                <Pencil aria-hidden="true" size={16} />
                <span className="label-wide">{dict.edit.edit}</span>
              </button>
            )}
            <button
              type="button"
              className="button small ghost"
              onClick={generate}
              disabled={pending}
              title={
                output.status === "failed" ? dict.ai.retry : dict.ai.regenerate
              }
            >
              <RefreshCw aria-hidden="true" size={16} />
              <span className="label-wide">
                {output.status === "failed"
                  ? dict.ai.retry
                  : dict.ai.regenerate}
              </span>
            </button>
            {ready && (
              <LanguagePicker
                dict={dict}
                original={original}
                language={language}
                onLanguage={onLanguage}
              />
            )}
          </div>
        )}
      </div>

      {ready && language && !editing && (
        <div className="translation-status">
          {!translation && (
            <div className="row wrap inline-status">
              <p className="hint">
                {format(dict.translation.documentPending, {
                  language: languageName(dict, language),
                })}
              </p>
              <button
                type="button"
                className="button small primary"
                onClick={translate}
                disabled={translating.pending}
              >
                {dict.translation.translate}
              </button>
            </div>
          )}
          {translation?.status === "pending" && (
            <p role="status">
              {dict.translation.translatingDocument}{" "}
              <progress aria-label={dict.translation.translatingDocument} />
            </p>
          )}
          {translation?.status === "failed" && (
            <div className="row wrap inline-status">
              <p className="alert error">
                {errorMessage(
                  dict,
                  translation.error_code ?? "translation_failed",
                )}
              </p>
              <button
                type="button"
                className="button small primary"
                onClick={translate}
                disabled={translating.pending}
              >
                {dict.ai.retry}
              </button>
            </div>
          )}
          {translated?.outdated && (
            <div className="row wrap inline-status">
              <p className="hint">{dict.translation.documentOutdated}</p>
              <button
                type="button"
                className="button small primary"
                onClick={translate}
                disabled={translating.pending}
              >
                {dict.translation.update}
              </button>
            </div>
          )}
          {translated && (
            <p className="notice compact" role="note">
              {dict.translation.notice}{" "}
              {translated.model &&
                format(dict.ai.generatedWith, { model: translated.model })}
              {translated.outdated ? "" : `. ${dict.translation.editOriginal}`}
            </p>
          )}
          <FormMessage error={translating.error} />
        </div>
      )}

      {output.status === "failed" && (
        <p className="alert error">
          {errorMessage(dict, output.error_code ?? "generation_failed")}
        </p>
      )}
      {ready && editing && (
        <AiOutputEditor
          dict={dict}
          output={output}
          onCancel={() => setEditing(false)}
          onSaved={(o) => {
            setEditing(false);
            onRequested(o);
          }}
        />
      )}
      {ready && !editing && (
        <article
          className="document"
          lang={translated ? translated.language : undefined}
        >
          <Content dict={dict} kind={kind} content={content} />
        </article>
      )}
      <FormMessage error={error} />
      {ready && (
        <footer className="panel-foot">
          <p className="hint">
            {dict.home.aiNotice}{" "}
            {output.model &&
              format(dict.ai.generatedWith, { model: output.model })}
            {output.edited_at &&
              ` · ${format(dict.edit.edited, {
                date: dateTime(lang, output.edited_at),
              })}`}
          </p>
          <p className="hint">{dict.ai.sourcesHint}</p>
          {output.edited_at && (
            <Revisions
              dict={dict}
              lang={lang}
              kind={kind}
              outputId={output.id}
            />
          )}
        </footer>
      )}
    </div>
  );
}

/** Versiones anteriores a cada edición manual (quién y cuándo); se cargan al desplegar. */
function Revisions({
  dict,
  lang,
  kind,
  outputId,
}: {
  dict: Dictionary;
  lang: Locale;
  kind: Kind;
  outputId: string;
}) {
  const [revisions, setRevisions] = useState<
    Schemas["AiOutputRevisionOut"][] | null
  >(null);
  return (
    <details
      onToggle={(e) => {
        if (!e.currentTarget.open) return;
        void api
          .GET("/v1/ai-outputs/{output_id}/revisions", {
            params: { path: { output_id: outputId } },
          })
          .then(({ data }) => setRevisions(data ?? []));
      }}
    >
      <summary>
        {revisions
          ? format(dict.edit.revisionsCount, { count: revisions.length })
          : dict.edit.revisions}
      </summary>
      {revisions === null ? (
        <p role="status">{dict.common.loading}</p>
      ) : (
        <ul className="list">
          {revisions.map((r) => (
            <li key={r.id} className="history-item">
              <span className="hint">
                {format(dict.edit.by, {
                  name: r.editor_name ?? "—",
                  date: dateTime(lang, r.created_at),
                })}
              </span>
              <div className="revision">
                <Content dict={dict} kind={kind} content={r.content} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}

function dateTime(lang: Locale, iso: string): string {
  return new Intl.DateTimeFormat(lang, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(iso));
}
