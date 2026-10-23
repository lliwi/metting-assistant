"use client";

import Link from "next/link";
import { useCallback, useState } from "react";

import { RequireAuth } from "@/components/auth/RequireAuth";
import { errorMessage, Field, FormMessage } from "@/components/forms";
import type { Dictionary, Locale } from "@/i18n/client";
import { format } from "@/i18n/format";
import { api, type User } from "@/lib/api/client";
import { useQuery } from "@/lib/api/useQuery";
import { getRecorderQueue, tryComplete } from "@/lib/recorder";
import { formatDuration } from "@/lib/recorder/useCapture";
import { useUploaderStats } from "@/lib/recorder/useUploaderStats";

import { UploadStatus } from "./UploadStatus";

type Props = { dict: Dictionary; lang: Locale };

export function RecordingsPage({ dict, lang }: Props) {
  return (
    <RequireAuth lang={lang} loadingLabel={dict.common.loading}>
      {(user) => <List dict={dict} lang={lang} user={user} />}
    </RequireAuth>
  );
}

function List({ dict, lang, user }: Props & { user: User }) {
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const fetchRecordings = useCallback(async () => {
    const query = {
      q: filters.q || undefined,
      speaker: filters.speaker || undefined,
      date_from: filters.from ? `${filters.from}T00:00:00` : undefined,
      date_to: filters.to ? `${filters.to}T23:59:59` : undefined,
    };
    return (await api.GET("/v1/recordings", { params: { query } })).data ?? [];
  }, [filters]);
  const { data: recordings, reload } = useQuery(fetchRecordings);
  const stats = useUploaderStats();
  const [error, setError] = useState<string | null>(null);
  const dateFormat = new Intl.DateTimeFormat(lang, {
    dateStyle: "medium",
    timeStyle: "short",
  });

  async function finish(recordingId: string) {
    setError(null);
    const { store, uploader } = getRecorderQueue();
    await store.requestFinish(recordingId);
    uploader.notify();
    const result = await tryComplete(recordingId);
    if (result.kind === "missing") setError(dict.recording.missing);
    else if (result.kind === "error") setError(errorMessage(dict, result.code));
    await reload();
  }

  return (
    <div className="stack-lg">
      <div className="row spread">
        <h1>{dict.recordings.title}</h1>
        <Link className="button primary" href={`/${lang}/record`}>
          {dict.nav.newRecording}
        </Link>
      </div>
      <UploadStatus dict={dict} />
      <SearchForm dict={dict} onSearch={setFilters} />
      <FormMessage error={error} />

      <section className="card">
        {recordings === undefined ? (
          <p role="status">{dict.common.loading}</p>
        ) : recordings.length === 0 ? (
          <p className="hint">
            {filters === EMPTY_FILTERS
              ? dict.recordings.empty
              : dict.search.noResults}
          </p>
        ) : (
          <ul className="list">
            {recordings.map((r) => {
              const local = stats?.localByRecording[r.id] ?? 0;
              const resumable =
                r.status === "uploading" && r.owner_id === user.id;
              return (
                <li key={r.id} className="list-row">
                  <div>
                    <div>
                      <Link href={`/${lang}/recordings/${r.id}`}>
                        <strong>{r.title}</strong>
                      </Link>
                    </div>
                    {r.matches.length > 0 && (
                      <ul className="matches">
                        {r.matches.map((m) => (
                          <li key={m.idx}>
                            <Link
                              href={`/${lang}/recordings/${r.id}#seg-${m.idx}`}
                            >
                              <span className="hint">
                                {formatDuration(m.start_ms)}
                              </span>{" "}
                              <Highlighted text={m.highlighted} />
                            </Link>
                          </li>
                        ))}
                      </ul>
                    )}
                    <div className="hint">
                      {dateFormat.format(new Date(r.created_at))} ·{" "}
                      {formatDuration(r.duration_ms)}
                    </div>
                    {r.status === "failed" && r.error_code && (
                      <div className="alert error">
                        {format(dict.recordings.failedReason, {
                          reason: errorMessage(dict, r.error_code),
                        })}
                      </div>
                    )}
                    {local > 0 && (
                      <div className="hint">
                        {format(dict.recordings.localPending, { count: local })}
                      </div>
                    )}
                  </div>
                  <div className="row">
                    <span className="badge">
                      {dict.recordings.status[r.status]}
                    </span>
                    {r.audio_ready && r.status === "pending" && (
                      <span className="badge ok">
                        {dict.recordings.audioReady}
                      </span>
                    )}
                    {resumable && (
                      <>
                        <Link
                          className="button small"
                          href={`/${lang}/record?recording=${r.id}`}
                        >
                          {dict.recording.continue}
                        </Link>
                        <button
                          type="button"
                          className="button small"
                          onClick={() => void finish(r.id)}
                        >
                          {dict.recordings.finishNow}
                        </button>
                      </>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}

type Filters = { q: string; speaker: string; from: string; to: string };
const EMPTY_FILTERS: Filters = { q: "", speaker: "", from: "", to: "" };

/** Resalta los términos marcados por la API entre ⟦ y ⟧ (sin interpretar HTML). */
function Highlighted({ text }: { text: string }) {
  return (
    <>
      {text
        .split(/(⟦[^⟧]*⟧)/)
        .map((part, i) =>
          part.startsWith("⟦") ? (
            <mark key={i}>{part.slice(1, -1)}</mark>
          ) : (
            part
          ),
        )}
    </>
  );
}

function SearchForm({
  dict,
  onSearch,
}: {
  dict: Dictionary;
  onSearch: (f: Filters) => void;
}) {
  const [form, setForm] = useState<Filters>(EMPTY_FILTERS);
  const set =
    (key: keyof Filters) => (e: React.ChangeEvent<HTMLInputElement>) =>
      setForm((f) => ({ ...f, [key]: e.target.value }));
  const empty = Object.values(form).every((v) => !v);

  return (
    <form
      role="search"
      aria-label={dict.search.label}
      className="card search"
      onSubmit={(e) => {
        e.preventDefault();
        onSearch(empty ? EMPTY_FILTERS : { ...form });
      }}
    >
      <Field
        label={dict.search.label}
        type="search"
        placeholder={dict.search.placeholder}
        maxLength={200}
        value={form.q}
        onChange={set("q")}
      />
      <Field
        label={dict.search.speaker}
        maxLength={100}
        value={form.speaker}
        onChange={set("speaker")}
      />
      <Field
        label={dict.search.from}
        type="date"
        value={form.from}
        onChange={set("from")}
      />
      <Field
        label={dict.search.to}
        type="date"
        value={form.to}
        onChange={set("to")}
      />
      <div className="row">
        <button type="submit" className="button primary">
          {dict.search.submit}
        </button>
        {!empty && (
          <button
            type="button"
            className="button"
            onClick={() => {
              setForm(EMPTY_FILTERS);
              onSearch(EMPTY_FILTERS);
            }}
          >
            {dict.search.clear}
          </button>
        )}
      </div>
    </form>
  );
}
