"use client";

import { Pencil, RefreshCw } from "lucide-react";
import { useState } from "react";

import { errorMessage, FormMessage, useAction } from "@/components/forms";
import type { Dictionary, Locale } from "@/i18n/client";
import { format } from "@/i18n/format";
import { api, type Schemas } from "@/lib/api/client";
import { formatDuration } from "@/lib/recorder/useCapture";

import { AiOutputEditor } from "./AiOutputEditor";
import { usePlayer } from "./Player";

type Output = Schemas["AiOutputOut"];
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

/** Un único botón para crear los documentos que aún no existen; el tipo se elige en la lista. */
export function AiGenerate({
  dict,
  recordingId,
  existing,
  onRequested,
}: {
  dict: Dictionary;
  recordingId: string;
  existing: Kind[];
  onRequested: (o: Output) => void;
}) {
  const { pending, error, run } = useAction(dict);
  const missing = KINDS.filter((k) => !existing.includes(k));
  const [choice, setChoice] = useState<Kind | null>(null);
  // Si el elegido ya se ha generado, se pasa al siguiente que falte.
  const kind = choice && missing.includes(choice) ? choice : missing[0];
  if (!kind) return null;

  async function generate(e: React.FormEvent) {
    e.preventDefault();
    const data = await run(() =>
      api.POST("/v1/recordings/{recording_id}/ai-outputs", {
        params: { path: { recording_id: recordingId } },
        body: { kind },
      }),
    );
    if (data) onRequested(data);
  }

  return (
    <form
      className="card stack"
      aria-labelledby="ai-generate-heading"
      onSubmit={generate}
    >
      <h2 id="ai-generate-heading">{dict.ai.generateHeading}</h2>
      <div className="row ai-generate">
        <label className="visually-hidden" htmlFor="ai-generate-kind">
          {dict.ai.kind}
        </label>
        <select
          id="ai-generate-kind"
          value={kind}
          onChange={(e) => setChoice(e.target.value as Kind)}
        >
          {missing.map((k) => (
            <option key={k} value={k}>
              {kindTitle(dict, k)}
            </option>
          ))}
        </select>
        <button type="submit" className="button primary" disabled={pending}>
          {dict.ai.generate}
        </button>
      </div>
      <FormMessage error={error} />
    </form>
  );
}

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

export function AiOutputSection({
  dict,
  lang,
  kind,
  output,
  recordingId,
  canGenerate,
  onRequested,
}: {
  dict: Dictionary;
  lang: Locale;
  kind: Kind;
  output: Output;
  recordingId: string;
  canGenerate: boolean;
  onRequested: (o: Output) => void;
}) {
  const { pending, error, run } = useAction(dict);
  const [editing, setEditing] = useState(false);
  const title = kindTitle(dict, kind);
  const generating = output.status === "pending";
  const ready = output.status === "ready";

  async function regenerate() {
    const data = await run(() =>
      api.POST("/v1/recordings/{recording_id}/ai-outputs", {
        params: { path: { recording_id: recordingId } },
        body: { kind },
      }),
    );
    if (data) onRequested(data);
  }

  return (
    <section
      className="card stack"
      aria-labelledby={`ai-${kind}`}
      aria-busy={generating}
    >
      <div className="row spread">
        <h2 id={`ai-${kind}`}>{title}</h2>
        <div className="row">
          {canGenerate && ready && !editing && (
            <button
              type="button"
              className="icon-link"
              onClick={() => setEditing(true)}
              aria-label={dict.edit.edit}
              title={dict.edit.edit}
            >
              <Pencil aria-hidden="true" size={18} />
            </button>
          )}
          {canGenerate && !generating && !editing && (
            <button
              type="button"
              className="icon-link"
              onClick={regenerate}
              disabled={pending}
              aria-label={dict.ai.regenerate}
              title={dict.ai.regenerate}
            >
              <RefreshCw aria-hidden="true" size={20} />
            </button>
          )}
        </div>
      </div>

      {generating && (
        <p role="status">
          {dict.ai.generating} <progress aria-label={dict.ai.generating} />
        </p>
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
        <Content dict={dict} kind={kind} content={output.content} />
      )}
      {ready && output.edited_at && (
        <p className="hint">
          {format(dict.edit.edited, {
            date: dateTime(lang, output.edited_at),
          })}
        </p>
      )}
      {ready && output.edited_at && (
        <Revisions dict={dict} lang={lang} kind={kind} outputId={output.id} />
      )}
      {output.status === "ready" && (
        <p className="notice" role="note">
          {dict.home.aiNotice}{" "}
          {output.model &&
            format(dict.ai.generatedWith, { model: output.model })}
        </p>
      )}
      <FormMessage error={error} />
    </section>
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
