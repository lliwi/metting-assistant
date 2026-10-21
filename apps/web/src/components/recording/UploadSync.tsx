"use client";

import { useEffect } from "react";

import { useAuth } from "@/components/auth/AuthProvider";
import { getRecorderQueue, tryComplete } from "@/lib/recorder";

/**
 * Mantiene la cola de subida activa en toda la aplicación: reanuda tras recargar,
 * reintenta al recuperar la red y cierra las capturas que el usuario dio por terminadas.
 */
export function UploadSync() {
  const { user } = useAuth();

  useEffect(() => {
    if (!user || typeof indexedDB === "undefined") return;
    const { store, uploader } = getRecorderQueue();
    void uploader.start();
    uploader.notify({ authRestored: true });

    let completing = false;
    const completeFinished = async () => {
      if (completing) return;
      completing = true;
      try {
        for (const capture of await store.listCaptures()) {
          if (capture.finishRequested) await tryComplete(capture.recordingId);
        }
      } catch {
        // sin red: se reintenta en el siguiente cambio de estado
      } finally {
        completing = false;
      }
    };

    const unsubscribe = uploader.subscribe((stats) => {
      if (!stats.uploading && stats.pending === 0) void completeFinished();
    });
    const wake = () => uploader.notify();
    const onVisible = () => {
      if (document.visibilityState === "visible") wake();
    };
    window.addEventListener("online", wake);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      unsubscribe();
      window.removeEventListener("online", wake);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [user]);

  return null;
}
