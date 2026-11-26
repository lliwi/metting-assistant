"use client";

import { Download } from "lucide-react";
import { useState } from "react";

import type { Dictionary } from "@/i18n/client";
import { format } from "@/i18n/format";
import type { Schemas } from "@/lib/api/client";

import { languageName } from "./Translation";

const FORMATS = ["pdf", "docx", "md", "txt", "srt", "vtt"] as const;
const TRANSCRIPT_ONLY = new Set(["txt", "srt", "vtt"]);
type Part = "transcript" | Schemas["AiOutputOut"]["kind"];

/** Descargas directas: el navegador envía la cookie de sesión y guarda el archivo. */
export function ExportPanel({
  dict,
  recordingId,
  documents,
  original,
  translations,
}: {
  dict: Dictionary;
  recordingId: string;
  /** Documentos de IA listos: solo se ofrecen esos. */
  documents: Schemas["AiOutputOut"]["kind"][];
  /** Idioma de la transcripción y aquellos a los que está traducida. */
  original: string;
  translations: string[];
}) {
  const available: Part[] = ["transcript", ...documents];
  const [excluded, setExcluded] = useState<Set<Part>>(new Set());
  const [choice, setChoice] = useState("");
  const language = translations.includes(choice) ? choice : "";

  function toggle(part: Part) {
    setExcluded((current) => {
      const next = new Set(current);
      if (next.has(part)) next.delete(part);
      else next.add(part);
      return next;
    });
  }

  function href(fmt: (typeof FORMATS)[number]) {
    const params = new URLSearchParams(
      TRANSCRIPT_ONLY.has(fmt)
        ? []
        : (["transcript", "summary", "minutes", "class_notes"] as const).map(
            (k) => [k, String(available.includes(k) && !excluded.has(k))],
          ),
    );
    if (language) params.set("language", language);
    const query = params.size ? `?${params}` : "";
    return `/api/v1/recordings/${recordingId}/exports/${fmt}${query}`;
  }

  return (
    <div className="stack">
      {available.length > 1 && (
        <fieldset className="consent">
          <legend>{dict.export.include}</legend>
          <div className="row wrap">
            {available.map((key) => (
              <label key={key} className="checkbox">
                <input
                  type="checkbox"
                  checked={!excluded.has(key)}
                  onChange={() => toggle(key)}
                />
                {dict.export[key]}
              </label>
            ))}
          </div>
        </fieldset>
      )}
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
      <div className="format-grid">
        {FORMATS.map((fmt) => (
          <a key={fmt} className="button" href={href(fmt)} download>
            <Download aria-hidden="true" size={16} /> {dict.export.formats[fmt]}
          </a>
        ))}
      </div>
      <p className="hint">{dict.export.subtitlesHint}</p>
    </div>
  );
}
