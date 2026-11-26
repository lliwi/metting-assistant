"use client";

import {
  ArrowLeft,
  AudioLines,
  ClipboardList,
  FileText,
  Folder,
  GraduationCap,
  Mic,
  Search,
  SlidersHorizontal,
  Sparkles,
  Upload,
} from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useState } from "react";

import { RequireAuth } from "@/components/auth/RequireAuth";
import { errorMessage, Field, FormMessage } from "@/components/forms";
import type { Dictionary, Locale } from "@/i18n/client";
import { format } from "@/i18n/format";
import { api, type Schemas, type User } from "@/lib/api/client";
import { useQuery } from "@/lib/api/useQuery";
import { getRecorderQueue, tryComplete } from "@/lib/recorder";
import { formatDuration } from "@/lib/recorder/useCapture";
import { useUploaderStats } from "@/lib/recorder/useUploaderStats";

import { kindTab } from "./AiOutputs";
import { UploadStatus } from "./UploadStatus";

type Props = { dict: Dictionary; lang: Locale };

export function RecordingsPage({ dict, lang }: Props) {
  // Carpeta abierta (?org=…). Al cambiar de carpeta, la búsqueda empieza de cero.
  const orgId = useSearchParams().get("org");
  return (
    <RequireAuth lang={lang} loadingLabel={dict.common.loading}>
      {(user) => (
        <List
          key={orgId ?? "all"}
          dict={dict}
          lang={lang}
          user={user}
          orgId={orgId}
        />
      )}
    </RequireAuth>
  );
}

type Organization = Schemas["OrganizationOut"];

async function fetchOrganizations() {
  return (await api.GET("/v1/organizations")).data ?? [];
}

function List({
  dict,
  lang,
  user,
  orgId,
}: Props & { user: User; orgId: string | null }) {
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const { data: organizations } = useQuery(fetchOrganizations);
  const folder = organizations?.find((o) => o.id === orgId);
  // Con una sola organización no hay carpetas: se ven sus grabaciones directamente.
  const scope =
    folder ?? (organizations?.length === 1 ? organizations[0] : undefined);
  const searching = filters !== EMPTY_FILTERS;
  const showFolders =
    organizations !== undefined && !scope && !searching && organizations.length > 1;
  const orgName = (o: Organization) =>
    o.is_personal ? dict.orgs.personal : o.name;
  const orgNames = Object.fromEntries(
    (organizations ?? []).map((o) => [o.id, orgName(o)]),
  );

  const fetchRecordings = useCallback(async () => {
    if (organizations === undefined || showFolders) return [];
    const query = {
      organization_id: scope?.id,
      q: filters.q || undefined,
      speaker: filters.speaker || undefined,
      date_from: filters.from ? `${filters.from}T00:00:00` : undefined,
      date_to: filters.to ? `${filters.to}T23:59:59` : undefined,
    };
    return (await api.GET("/v1/recordings", { params: { query } })).data ?? [];
  }, [filters, organizations, showFolders, scope?.id]);
  const { data: recordings, reload } = useQuery(fetchRecordings);
  const stats = useUploaderStats();
  const [error, setError] = useState<string | null>(null);
  const dateFormat = new Intl.DateTimeFormat(lang, {
    dateStyle: "medium",
    timeStyle: "short",
  });
  const shortDate = new Intl.DateTimeFormat(lang, { dateStyle: "medium" });

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
      <div className="page-head">
        {folder ? (
          <div className="detail-head">
            <Link href={`/${lang}/recordings`} className="back-link">
              <ArrowLeft aria-hidden="true" size={18} />{" "}
              {dict.recordings.title}
            </Link>
            <h1>{orgName(folder)}</h1>
          </div>
        ) : (
          <h1>{dict.recordings.title}</h1>
        )}
        <div className="row">
          <Link className="button" href={`/${lang}/import`}>
            <Upload aria-hidden="true" size={18} />
            <span className="label-wide">{dict.recordings.import}</span>
          </Link>
          <Link className="button primary" href={`/${lang}/record`}>
            <Mic aria-hidden="true" size={18} /> {dict.nav.newRecording}
          </Link>
        </div>
      </div>
      <UploadStatus dict={dict} />
      <SearchForm dict={dict} onSearch={setFilters} />
      <FormMessage error={error} />

      {showFolders ? (
        <section aria-labelledby="folders-heading">
          <h2 id="folders-heading" className="visually-hidden">
            {dict.recordings.folders}
          </h2>
          <ul className="folder-grid">
            {organizations.map((o) => (
              <li key={o.id}>
                <Link
                  href={`/${lang}/recordings?org=${o.id}`}
                  className="folder"
                >
                  <Folder
                    aria-hidden="true"
                    size={28}
                    className="folder-icon"
                  />
                  <span className="folder-name">{orgName(o)}</span>
                  <span className="hint">
                    {o.recordings === 0
                      ? dict.recordings.folderEmpty
                      : o.recordings === 1
                        ? dict.recordings.folderOne
                        : format(dict.recordings.folderMany, {
                            count: o.recordings,
                          })}
                    {o.last_recording_at &&
                      ` · ${format(dict.recordings.folderLast, {
                        date: shortDate.format(new Date(o.last_recording_at)),
                      })}`}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : recordings === undefined ? (
        <p role="status">{dict.common.loading}</p>
      ) : recordings.length === 0 ? (
        <div className="card empty-state">
          <AudioLines aria-hidden="true" size={28} className="muted-icon" />
          <p>
            {filters === EMPTY_FILTERS
              ? dict.recordings.empty
              : dict.search.noResults}
          </p>
        </div>
      ) : (
        <>
        {searching && !scope && (
          <p className="hint">{dict.recordings.searchAll}</p>
        )}
        <ul className="rec-list">
          {recordings.map((r) => {
            const local = stats?.localByRecording[r.id] ?? 0;
            const resumable =
              r.status === "uploading" && r.owner_id === user.id;
            const href = `/${lang}/recordings/${r.id}`;
            return (
              <li key={r.id} className="rec-card">
                <div className="rec-main">
                  <Link href={href} className="rec-title" id={`title-${r.id}`}>
                    {r.title}
                  </Link>
                  <p className="meta">
                    <span>{dateFormat.format(new Date(r.created_at))}</span>
                    <span>{formatDuration(r.duration_ms)}</span>
                    {searching && !scope && orgNames[r.organization_id] && (
                      <span className="org-tag">
                        <Folder aria-hidden="true" size={13} />
                        {orgNames[r.organization_id]}
                      </span>
                    )}
                    {r.status !== "transcribed" && (
                      <span className={`badge status-${r.status}`}>
                        {dict.recordings.status[r.status]}
                      </span>
                    )}
                    {r.audio_ready && r.status === "pending" && (
                      <span className="badge ok">
                        {dict.recordings.audioReady}
                      </span>
                    )}
                  </p>
                  {r.status === "transcribed" && (
                    <ul className="doc-links" aria-label={dict.recordings.documents}>
                      <li>
                        <Link
                          href={href}
                          className="doc-link"
                          aria-describedby={`title-${r.id}`}
                        >
                          <FileText aria-hidden="true" size={15} />
                          {dict.detail.transcript}
                        </Link>
                      </li>
                      {r.documents.map((kind) => {
                        const Icon = DOC_ICONS[kind];
                        return (
                          <li key={kind}>
                            <Link
                              href={`${href}?tab=${kind}`}
                              className="doc-link"
                              aria-describedby={`title-${r.id}`}
                            >
                              <Icon aria-hidden="true" size={15} />
                              {kindTab(dict, kind)}
                            </Link>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                  {r.matches.length > 0 && (
                    <ul className="matches">
                      {r.matches.map((m) => (
                        <li key={m.idx}>
                          <Link href={`${href}#seg-${m.idx}`}>
                            <span className="timestamp">
                              {formatDuration(m.start_ms)}
                            </span>{" "}
                            <Highlighted text={m.highlighted} />
                          </Link>
                        </li>
                      ))}
                    </ul>
                  )}
                  {r.status === "failed" && r.error_code && (
                    <p className="alert error">
                      {format(dict.recordings.failedReason, {
                        reason: errorMessage(dict, r.error_code),
                      })}
                    </p>
                  )}
                  {local > 0 && (
                    <p className="hint">
                      {format(dict.recordings.localPending, { count: local })}
                    </p>
                  )}
                </div>
                {resumable && (
                  <div className="row wrap">
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
                  </div>
                )}
              </li>
            );
          })}
        </ul>
        </>
      )}
    </div>
  );
}

const DOC_ICONS = {
  summary: Sparkles,
  minutes: ClipboardList,
  class_notes: GraduationCap,
} as const;

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

  const filtered = Boolean(form.speaker || form.from || form.to);

  return (
    <form
      role="search"
      aria-label={dict.search.label}
      className="search"
      onSubmit={(e) => {
        e.preventDefault();
        onSearch(empty ? EMPTY_FILTERS : { ...form });
      }}
    >
      <div className="search-bar">
        <Search aria-hidden="true" size={18} className="muted-icon" />
        <label htmlFor="search-q" className="visually-hidden">
          {dict.search.label}
        </label>
        <input
          id="search-q"
          type="search"
          placeholder={dict.search.placeholder}
          maxLength={200}
          value={form.q}
          onChange={set("q")}
        />
        <button type="submit" className="button primary">
          {dict.search.submit}
        </button>
      </div>
      <details className="filters" open={filtered || undefined}>
        <summary>
          <SlidersHorizontal aria-hidden="true" size={16} />{" "}
          {dict.recordings.filters}
        </summary>
        <div className="filter-grid">
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
        </div>
      </details>
      {!empty && (
        <div>
          <button
            type="button"
            className="link-button subtle"
            onClick={() => {
              setForm(EMPTY_FILTERS);
              onSearch(EMPTY_FILTERS);
            }}
          >
            {dict.search.clear}
          </button>
        </div>
      )}
    </form>
  );
}
