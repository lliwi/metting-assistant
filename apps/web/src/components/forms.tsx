"use client";

import { useCallback, useId, useState } from "react";

import type { Dictionary } from "@/i18n/client";
import { errorCode } from "@/lib/api/client";

export function errorMessage(dict: Dictionary, code: string): string {
  const errors = dict.errors as Record<string, string>;
  return errors[code] ?? errors.unknown;
}

/** Ejecuta una llamada a la API gestionando estado de envío y error traducido. */
export function useAction(dict: Dictionary) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(
    async <T,>(
      call: () => Promise<{ data?: T; error?: unknown; response: Response }>,
    ): Promise<T | undefined> => {
      setPending(true);
      setError(null);
      try {
        const { data, error: apiError, response } = await call();
        if (!response.ok) {
          setError(errorMessage(dict, errorCode(apiError)));
          return undefined;
        }
        return (data ?? (null as T)) as T;
      } catch {
        setError(dict.errors.network);
        return undefined;
      } finally {
        setPending(false);
      }
    },
    [dict],
  );

  return { pending, error, setError, run };
}

export function Field({
  label,
  hint,
  ...input
}: { label: string; hint?: string } & React.ComponentProps<"input">) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input id={id} aria-describedby={hintId} {...input} />
      {hint && (
        <p id={hintId} className="hint">
          {hint}
        </p>
      )}
    </div>
  );
}

/** Región anunciada por lectores de pantalla para errores y confirmaciones. */
export function FormMessage({
  error,
  success,
}: {
  error?: string | null;
  success?: string | null;
}) {
  return (
    <div aria-live="polite" className="form-message">
      {error && (
        <p className="alert error" role="alert">
          {error}
        </p>
      )}
      {success && !error && <p className="alert success">{success}</p>}
    </div>
  );
}

export function safeNextPath(value: string | null, fallback: string): string {
  if (
    !value ||
    !value.startsWith("/") ||
    value.startsWith("//") ||
    value.includes("\\")
  ) {
    return fallback;
  }
  return value;
}
