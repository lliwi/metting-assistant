"use client";

import { Download } from "lucide-react";
import { useState } from "react";

import type { Dictionary } from "@/i18n/client";
import { format } from "@/i18n/format";

import { languageName } from "./Translation";

const FORMATS = ["pdf", "docx", "md", "txt", "srt", "vtt"] as const;
const TRANSCRIPT_ONLY = new Set(["txt", "srt", "vtt"]);

/** Descargas directas: el navegador envía la cookie de sesión y guarda el archivo. */
export function ExportPanel({
  dict,
  recordingId,
  original,
  translations,
}: {
  dict: Dictionary;
  recordingId: string;
  /** Idioma de la transcripción y aquellos a los que está traducida. */
  original: string;
  translations: string[];
}) {
  const [parts, setParts] = useState({
    transcript: true,
    summary: true,
    minutes: true,
    class_notes: true,
  });
  const [choice, setChoice] = useState("");
  const language = translations.includes(choice) ? choice : "";
  const toggle = (key: keyof typeof parts) => () =>
    setParts((p) => ({ ...p, [key]: !p[key] }));

  function href(fmt: (typeof FORMATS)[number]) {
    const params = new URLSearchParams(
      TRANSCRIPT_ONLY.has(fmt)
        ? []
        : Object.entries(parts).map(([k, v]) => [k, String(v)]),
    );
    if (language) params.set("language", language);
    const query = params.size ? `?${params}` : "";
    return `/api/v1/recordings/${recordingId}/exports/${fmt}${query}`;
  }

  return (
    <section className="card stack" aria-labelledby="export-heading">
      <h2 id="export-heading">{dict.export.title}</h2>
      <fieldset className="consent">
        <legend>{dict.export.include}</legend>
        <div className="row wrap">
          {(["transcript", "summary", "minutes", "class_notes"] as const).map(
            (key) => (
              <label key={key} className="checkbox">
                <input
                  type="checkbox"
                  checked={parts[key]}
                  onChange={toggle(key)}
                />
                {dict.export[key]}
              </label>
            ),
          )}
        </div>
      </fieldset>
      {translations.length > 0 && (
        <div className="field">
          <label htmlFor="export-language">{dict.export.language}</label>
          <select
            id="export-language"
            value={language}
            onChange={(e) => setChoice(e.target.value)}
          >
            <option value="">
              {format(dict.translation.original, {
                language: languageName(dict, original),
              })}
            </option>
            {translations.map((l) => (
              <option key={l} value={l}>
                {languageName(dict, l)}
              </option>
            ))}
          </select>
        </div>
      )}
      <div className="row wrap">
        {FORMATS.map((fmt) => (
          <a key={fmt} className="button small" href={href(fmt)} download>
            <Download aria-hidden="true" size={16} /> {dict.export.formats[fmt]}
          </a>
        ))}
      </div>
      <p className="hint">{dict.export.subtitlesHint}</p>
    </section>
  );
}
