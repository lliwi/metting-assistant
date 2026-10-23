"use client";

import { Download } from "lucide-react";
import { useState } from "react";

import type { Dictionary } from "@/i18n/client";

const FORMATS = ["pdf", "docx", "md", "txt", "srt", "vtt"] as const;
const TRANSCRIPT_ONLY = new Set(["txt", "srt", "vtt"]);

/** Descargas directas: el navegador envía la cookie de sesión y guarda el archivo. */
export function ExportPanel({
  dict,
  recordingId,
}: {
  dict: Dictionary;
  recordingId: string;
}) {
  const [parts, setParts] = useState({
    transcript: true,
    summary: true,
    minutes: true,
  });
  const toggle = (key: keyof typeof parts) => () =>
    setParts((p) => ({ ...p, [key]: !p[key] }));

  function href(fmt: (typeof FORMATS)[number]) {
    const params = new URLSearchParams(
      Object.entries(parts).map(([k, v]) => [k, String(v)]),
    );
    const query = TRANSCRIPT_ONLY.has(fmt) ? "" : `?${params}`;
    return `/api/v1/recordings/${recordingId}/exports/${fmt}${query}`;
  }

  return (
    <section className="card stack" aria-labelledby="export-heading">
      <h2 id="export-heading">{dict.export.title}</h2>
      <fieldset className="consent">
        <legend>{dict.export.include}</legend>
        <div className="row wrap">
          {(["transcript", "summary", "minutes"] as const).map((key) => (
            <label key={key} className="checkbox">
              <input
                type="checkbox"
                checked={parts[key]}
                onChange={toggle(key)}
              />
              {dict.export[key]}
            </label>
          ))}
        </div>
      </fieldset>
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
