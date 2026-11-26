"use client";

import { Check, Copy, Share2 } from "lucide-react";
import { useEffect, useState, useSyncExternalStore } from "react";

import type { Dictionary } from "@/i18n/client";

/** El navegador puede abrir el menú de compartir del sistema (sobre todo en el móvil). */
const canShare = () =>
  typeof navigator !== "undefined" && typeof navigator.share === "function";

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Sin API del portapapeles (contexto no seguro): el método antiguo.
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.append(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  }
}

/** Copiar y compartir el contenido de la pestaña. `text` se calcula al pulsar. */
export function ShareActions({
  dict,
  title,
  text,
}: {
  dict: Dictionary;
  title: string;
  text: () => string;
}) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const shareable = useSyncExternalStore(
    () => () => undefined,
    canShare,
    () => false,
  );

  useEffect(() => {
    if (state === "idle") return;
    const timer = setTimeout(() => setState("idle"), 2500);
    return () => clearTimeout(timer);
  }, [state]);

  async function copy() {
    setState((await copyText(text())) ? "copied" : "failed");
  }

  async function share() {
    try {
      await navigator.share({ title, text: text() });
    } catch {
      // Cancelado por la persona o no admitido: no es un error que mostrar.
    }
  }

  return (
    <>
      <button
        type="button"
        className="button small ghost"
        title={dict.share.copy}
        onClick={() => void copy()}
      >
        {state === "copied" ? (
          <Check aria-hidden="true" size={16} />
        ) : (
          <Copy aria-hidden="true" size={16} />
        )}
        <span className="label-wide">
          {state === "copied" ? dict.share.copied : dict.share.copy}
        </span>
      </button>
      {shareable && (
        <button
          type="button"
          className="button small ghost"
          title={dict.share.share}
          onClick={() => void share()}
        >
          <Share2 aria-hidden="true" size={16} />
          <span className="label-wide">{dict.share.share}</span>
        </button>
      )}
      <span role="status" className="visually-hidden">
        {state === "copied"
          ? dict.share.copied
          : state === "failed"
            ? dict.share.copyFailed
            : ""}
      </span>
      {state === "failed" && (
        <span className="hint inline-error">{dict.share.copyFailed}</span>
      )}
    </>
  );
}
