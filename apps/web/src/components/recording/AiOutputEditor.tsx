"use client";

import { Plus, Trash2 } from "lucide-react";
import { useId, useState } from "react";

import { FormMessage, useAction } from "@/components/forms";
import type { Dictionary } from "@/i18n/client";
import { api, type Schemas } from "@/lib/api/client";

type Output = Schemas["AiOutputOut"];
type Item = Record<string, unknown> & { segmentos?: unknown[] };
type Content = Record<string, unknown>;

function TextArea({
  label,
  value,
  onChange,
  rows = 2,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  rows?: number;
}) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <textarea
        id={id}
        rows={rows}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

function Input({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input id={id} value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

/** Lista editable de elementos con los campos indicados (añadir, quitar, modificar). */
function ItemsEditor({
  dict,
  legend,
  items,
  fields,
  empty,
  onChange,
}: {
  dict: Dictionary;
  legend: string;
  items: Item[];
  fields: {
    key: string;
    label: string;
    multiline?: boolean;
    optional?: boolean;
  }[];
  empty: Item;
  onChange: (items: Item[]) => void;
}) {
  const update = (i: number, key: string, value: string, optional?: boolean) =>
    onChange(
      items.map((item, j) =>
        j === i
          ? { ...item, [key]: optional && !value.trim() ? null : value }
          : item,
      ),
    );
  return (
    <fieldset className="consent stack">
      <legend>{legend}</legend>
      {items.map((item, i) => (
        <div key={i} className="edit-item">
          {fields.map((f) =>
            f.multiline ? (
              <TextArea
                key={f.key}
                label={f.label}
                value={String(item[f.key] ?? "")}
                onChange={(v) => update(i, f.key, v, f.optional)}
              />
            ) : (
              <Input
                key={f.key}
                label={f.label}
                value={String(item[f.key] ?? "")}
                onChange={(v) => update(i, f.key, v, f.optional)}
              />
            ),
          )}
          <button
            type="button"
            className="button small danger"
            onClick={() => onChange(items.filter((_, j) => j !== i))}
          >
            <Trash2 aria-hidden="true" size={14} /> {dict.edit.remove}
          </button>
        </div>
      ))}
      <button
        type="button"
        className="button small"
        onClick={() => onChange([...items, { ...empty, segmentos: [] }])}
      >
        <Plus aria-hidden="true" size={14} /> {dict.edit.add}
      </button>
    </fieldset>
  );
}

export function AiOutputEditor({
  dict,
  output,
  onSaved,
  onCancel,
}: {
  dict: Dictionary;
  output: Output;
  onSaved: (o: Output) => void;
  onCancel: () => void;
}) {
  const [content, setContent] = useState<Content>(() =>
    structuredClone(output.content),
  );
  const { pending, error, run } = useAction(dict);
  const set = (key: string) => (value: unknown) =>
    setContent((c) => ({ ...c, [key]: value }));
  const items = (key: string) => (content[key] as Item[] | undefined) ?? [];
  const textField = { key: "texto", label: dict.edit.text, multiline: true };

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const data = await run(() =>
      api.PATCH("/v1/ai-outputs/{output_id}", {
        params: { path: { output_id: output.id } },
        body: { content },
      }),
    );
    if (data) onSaved(data);
  }

  return (
    <form className="stack" onSubmit={save}>
      {output.kind === "summary" ? (
        <>
          <TextArea
            label={dict.ai.summary}
            rows={6}
            value={String(content.resumen ?? "")}
            onChange={set("resumen")}
          />
          <ItemsEditor
            dict={dict}
            legend={dict.ai.keyPoints}
            items={items("puntos_clave")}
            fields={[textField]}
            empty={{ texto: "" }}
            onChange={set("puntos_clave")}
          />
        </>
      ) : (
        <>
          <Input
            label={dict.edit.title}
            value={String(content.titulo ?? "")}
            onChange={set("titulo")}
          />
          <div className="field">
            <Input
              label={`${dict.ai.attendees} (${dict.edit.attendeesHint})`}
              value={((content.asistentes as string[] | undefined) ?? []).join(
                ", ",
              )}
              onChange={(v) =>
                set("asistentes")(
                  v
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean),
                )
              }
            />
          </div>
          <ItemsEditor
            dict={dict}
            legend={dict.ai.topics}
            items={items("temas")}
            fields={[
              { key: "titulo", label: dict.edit.topicTitle },
              {
                key: "resumen",
                label: dict.edit.topicSummary,
                multiline: true,
              },
            ]}
            empty={{ titulo: "", resumen: "" }}
            onChange={set("temas")}
          />
          <ItemsEditor
            dict={dict}
            legend={dict.ai.decisions}
            items={items("decisiones")}
            fields={[textField]}
            empty={{ texto: "" }}
            onChange={set("decisiones")}
          />
          <ItemsEditor
            dict={dict}
            legend={dict.ai.tasks}
            items={items("tareas")}
            fields={[
              {
                key: "descripcion",
                label: dict.edit.description,
                multiline: true,
              },
              { key: "responsable", label: dict.ai.owner, optional: true },
              { key: "fecha_limite", label: dict.ai.due, optional: true },
            ]}
            empty={{ descripcion: "", responsable: null, fecha_limite: null }}
            onChange={set("tareas")}
          />
          <ItemsEditor
            dict={dict}
            legend={dict.ai.openQuestions}
            items={items("preguntas_abiertas")}
            fields={[textField]}
            empty={{ texto: "" }}
            onChange={set("preguntas_abiertas")}
          />
        </>
      )}
      <p className="hint">{dict.edit.refsKept}</p>
      <FormMessage error={error} />
      <div className="row">
        <button type="submit" className="button primary" disabled={pending}>
          {dict.edit.save}
        </button>
        <button type="button" className="button" onClick={onCancel}>
          {dict.edit.cancel}
        </button>
      </div>
    </form>
  );
}
