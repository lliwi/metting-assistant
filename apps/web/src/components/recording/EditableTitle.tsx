"use client";

import { Pencil } from "lucide-react";
import { useState } from "react";

import { FormMessage, useAction } from "@/components/forms";
import type { Dictionary } from "@/i18n/client";
import { api, type Schemas } from "@/lib/api/client";

type Recording = Schemas["RecordingDetail"];

export function EditableTitle({
  dict,
  recording,
  onSaved,
}: {
  dict: Dictionary;
  recording: Recording;
  onSaved: (r: Recording) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(recording.title);
  const { pending, error, run } = useAction(dict);

  if (!editing) {
    return (
      <div className="row">
        <h1>{recording.title}</h1>
        <button
          type="button"
          className="icon-link"
          onClick={() => {
            setTitle(recording.title);
            setEditing(true);
          }}
          aria-label={dict.edit.editTitle}
          title={dict.edit.editTitle}
        >
          <Pencil aria-hidden="true" size={18} />
        </button>
      </div>
    );
  }

  return (
    <form
      className="stack"
      onSubmit={async (e) => {
        e.preventDefault();
        const data = await run(() =>
          api.PATCH("/v1/recordings/{recording_id}", {
            params: { path: { recording_id: recording.id } },
            body: { title },
          }),
        );
        if (data) {
          onSaved(data);
          setEditing(false);
        }
      }}
    >
      <label className="visually-hidden" htmlFor="title-input">
        {dict.edit.title}
      </label>
      <input
        id="title-input"
        className="title-input"
        required
        maxLength={300}
        value={title}
        autoFocus
        onChange={(e) => setTitle(e.target.value)}
      />
      <FormMessage error={error} />
      <div className="row">
        <button
          type="submit"
          className="button primary small"
          disabled={pending}
        >
          {dict.edit.save}
        </button>
        <button
          type="button"
          className="button small"
          onClick={() => setEditing(false)}
        >
          {dict.edit.cancel}
        </button>
      </div>
    </form>
  );
}
