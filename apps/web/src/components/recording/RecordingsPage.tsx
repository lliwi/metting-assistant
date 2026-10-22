"use client";

import Link from "next/link";
import { useCallback, useState } from "react";

import { RequireAuth } from "@/components/auth/RequireAuth";
import { errorMessage, FormMessage } from "@/components/forms";
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
  const fetchRecordings = useCallback(
    async () => (await api.GET("/v1/recordings")).data ?? [],
    [],
  );
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
      <FormMessage error={error} />

      <section className="card">
        {recordings === undefined ? (
          <p role="status">{dict.common.loading}</p>
        ) : recordings.length === 0 ? (
          <p className="hint">{dict.recordings.empty}</p>
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
                      <strong>{r.title}</strong>
                    </div>
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
