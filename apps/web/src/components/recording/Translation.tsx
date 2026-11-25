"use client";

import { FormMessage, errorMessage, useAction } from "@/components/forms";
import type { Dictionary } from "@/i18n/client";
import { format } from "@/i18n/format";
import { api, type Schemas } from "@/lib/api/client";

import { type Language, LANGUAGES } from "./SetupFields";

export type Translation = Schemas["TranslationOut"];
export type TranslatedSegments = Map<
  number,
  Schemas["TranslatedSegmentOut"]
>;

export function languageName(dict: Dictionary, language: string): string {
  const names = dict.recording.languages as Record<string, string>;
  return names[language] ?? language;
}

/** Idioma en que se muestra la transcripción y estado de su traducción. */
export function TranslationBar({
  dict,
  recordingId,
  original,
  language,
  translation,
  onLanguage,
  onRequested,
}: {
  dict: Dictionary;
  recordingId: string;
  /** Idioma de la transcripción. */
  original: string;
  /** Idioma elegido; null = original. */
  language: Language | null;
  /** Traducción al idioma elegido, si se ha pedido. */
  translation: Translation | undefined;
  onLanguage: (language: Language | null) => void;
  onRequested: (t: Translation) => void;
}) {
  const { pending, error, setError, run } = useAction(dict);
  const t = dict.translation;
  const translating = translation?.status === "pending";
  const stale =
    translation?.status === "ready"
      ? translation.total - translation.translated
      : 0;

  async function request() {
    if (!language) return;
    const data = await run(() =>
      api.POST("/v1/recordings/{recording_id}/translations", {
        params: { path: { recording_id: recordingId } },
        body: { language },
      }),
    );
    if (data) onRequested(data);
  }

  const action = !translation
    ? t.translate
    : translation.status === "failed" || stale > 0
      ? t.update
      : t.again;

  return (
    <div className="stack" aria-busy={translating}>
      <div className="row wrap">
        <label htmlFor="transcript-language">{t.label}</label>
        <select
          id="transcript-language"
          className="transcript-language"
          value={language ?? ""}
          onChange={(e) => {
            setError(null);
            onLanguage((e.target.value || null) as Language | null);
          }}
        >
          <option value="">
            {format(t.original, { language: languageName(dict, original) })}
          </option>
          {LANGUAGES.filter((l) => l !== original).map((l) => (
            <option key={l} value={l}>
              {languageName(dict, l)}
            </option>
          ))}
        </select>
        {language && !translating && (
          <button
            type="button"
            className={
              translation?.status === "ready" && stale === 0
                ? "button small"
                : "button small primary"
            }
            onClick={request}
            disabled={pending}
          >
            {action}
          </button>
        )}
      </div>
      {language && !translation && <p className="hint">{t.hint}</p>}
      {translating && (
        <p role="status">
          {format(t.translating, {
            done: translation.translated,
            total: translation.total,
          })}{" "}
          <progress
            aria-label={t.label}
            max={translation.total}
            value={translation.translated}
          />
        </p>
      )}
      {translation?.status === "failed" && (
        <p className="alert error">
          {errorMessage(dict, translation.error_code ?? "translation_failed")}
        </p>
      )}
      {stale > 0 && (
        <p className="hint">{format(t.pendingLines, { count: stale })}</p>
      )}
      {translation?.status === "ready" && (
        <p className="notice" role="note">
          {t.notice}{" "}
          {translation.model &&
            format(dict.ai.generatedWith, { model: translation.model })}
          {". "}
          {t.editOriginal}
        </p>
      )}
      <FormMessage error={error} />
    </div>
  );
}
