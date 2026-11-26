import type { Dictionary } from "@/i18n/client";
import type { Schemas } from "@/lib/api/client";
import { formatDuration } from "@/lib/recorder/useCapture";

type Kind = Schemas["AiOutputOut"]["kind"];
type Item = Record<string, unknown>;

/** Texto plano de cada contenido, para copiarlo o compartirlo (correo, chat…). */

const str = (v: unknown) => (typeof v === "string" ? v.trim() : "");
const items = (v: unknown): Item[] =>
  Array.isArray(v) ? v.filter((i): i is Item => !!i && typeof i === "object") : [];

function list(
  dict: Dictionary,
  heading: string,
  entries: Item[],
  render: (i: Item) => string,
): string[] {
  const lines = entries.map(render).filter(Boolean).map((l) => `- ${l}`);
  return ["", heading, ...(lines.length ? lines : [dict.ai.empty])];
}

function task(dict: Dictionary, i: Item): string {
  const extra = [
    str(i.responsable) && `${dict.ai.owner}: ${str(i.responsable)}`,
    str(i.fecha_limite) && `${dict.ai.due}: ${str(i.fecha_limite)}`,
  ].filter(Boolean);
  return `${str(i.descripcion)}${extra.length ? ` (${extra.join(" · ")})` : ""}`;
}

export function documentText(
  dict: Dictionary,
  kind: Kind,
  title: string,
  content: Item,
): string {
  const out: string[] = [title];
  if (kind === "summary") {
    out.push("", str(content.resumen));
    out.push(
      ...list(dict, dict.ai.keyPoints, items(content.puntos_clave), (i) =>
        str(i.texto),
      ),
    );
  } else if (kind === "minutes") {
    if (str(content.titulo)) out.push("", str(content.titulo));
    const attendees = Array.isArray(content.asistentes)
      ? content.asistentes.map(str).filter(Boolean)
      : [];
    out.push("", dict.ai.attendees, attendees.join(", ") || dict.ai.empty);
    out.push("", dict.ai.topics);
    const topics = items(content.temas);
    if (!topics.length) out.push(dict.ai.empty);
    for (const t of topics) out.push(`- ${str(t.titulo)}: ${str(t.resumen)}`);
    out.push(
      ...list(dict, dict.ai.decisions, items(content.decisiones), (i) =>
        str(i.texto),
      ),
      ...list(dict, dict.ai.tasks, items(content.tareas), (i) => task(dict, i)),
      ...list(
        dict,
        dict.ai.openQuestions,
        items(content.preguntas_abiertas),
        (i) => str(i.texto),
      ),
    );
  } else {
    if (str(content.titulo)) out.push("", str(content.titulo));
    if (str(content.introduccion)) out.push("", str(content.introduccion));
    for (const s of items(content.secciones))
      out.push("", str(s.titulo), str(s.contenido));
    out.push(
      ...list(dict, dict.ai.concepts, items(content.conceptos), (i) =>
        `${str(i.termino)}: ${str(i.definicion)}`,
      ),
      ...list(dict, dict.ai.examples, items(content.ejemplos), (i) =>
        str(i.texto),
      ),
      ...list(dict, dict.ai.review, items(content.para_repasar), (i) =>
        str(i.texto),
      ),
      ...list(dict, dict.ai.homework, items(content.deberes), (i) => {
        const due = str(i.fecha_limite);
        return `${str(i.descripcion)}${due ? ` (${dict.ai.due}: ${due})` : ""}`;
      }),
    );
  }
  return out.join("\n").trim() + "\n";
}

/** Una frase por línea: [mm:ss] Nombre: texto (en el idioma que se está leyendo). */
export function transcriptText(
  title: string,
  lines: { start_ms: number; speaker: string | null; text: string }[],
): string {
  return (
    [
      title,
      "",
      ...lines.map(
        (l) =>
          `[${formatDuration(l.start_ms)}] ${l.speaker ? `${l.speaker}: ` : ""}${l.text}`,
      ),
    ].join("\n") + "\n"
  );
}
