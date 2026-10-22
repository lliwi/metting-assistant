"use client";

import { RefreshCw } from "lucide-react";

import { errorMessage, FormMessage, useAction } from "@/components/forms";
import type { Dictionary } from "@/i18n/client";
import { format } from "@/i18n/format";
import { api, type Schemas } from "@/lib/api/client";
import { formatDuration } from "@/lib/recorder/useCapture";

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
  if (!refs?.length) return null;
  return (
    <span className="sources">
      {refs.map((r) => (
        <a
          key={r.idx}
          href={`#seg-${r.idx}`}
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

export function AiOutputSection({
  dict,
  kind,
  output,
  recordingId,
  canGenerate,
  onRequested,
}: {
  dict: Dictionary;
  kind: Kind;
  output?: Output;
  recordingId: string;
  canGenerate: boolean;
  onRequested: (o: Output) => void;
}) {
  const { pending, error, run } = useAction(dict);
  const title = kind === "summary" ? dict.ai.summary : dict.ai.minutes;
  const generating = output?.status === "pending";

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
        {canGenerate && output && !generating && (
          <button
            type="button"
            className="icon-link"
            onClick={regenerate}
            disabled={pending}
            aria-label={output ? dict.ai.regenerate : dict.ai.generate}
            title={output ? dict.ai.regenerate : dict.ai.generate}
          >
            <RefreshCw aria-hidden="true" size={20} />
          </button>
        )}
      </div>

      {!output && (
        <div className="stack">
          <p className="hint">{dict.ai.notStarted}</p>
          {canGenerate && (
            <button
              type="button"
              className="button primary"
              onClick={regenerate}
              disabled={pending}
            >
              {kind === "summary"
                ? dict.ai.generateSummary
                : dict.ai.generateMinutes}
            </button>
          )}
        </div>
      )}
      {generating && (
        <p role="status">
          {dict.ai.generating} <progress aria-label={dict.ai.generating} />
        </p>
      )}
      {output?.status === "failed" && (
        <p className="alert error">
          {errorMessage(dict, output.error_code ?? "generation_failed")}
        </p>
      )}
      {output?.status === "ready" &&
        (kind === "summary" ? (
          <Summary dict={dict} content={output.content} />
        ) : (
          <Minutes dict={dict} content={output.content} />
        ))}
      {output?.status === "ready" && (
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
