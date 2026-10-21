"use client";

import { useEffect, useState, useSyncExternalStore } from "react";

import { getRecorderQueue, type UploaderStats } from "./index";

export function useUploaderStats(): UploaderStats | null {
  const [stats, setStats] = useState<UploaderStats | null>(null);
  useEffect(() => getRecorderQueue().uploader.subscribe(setStats), []);
  return stats;
}

function subscribeOnline(callback: () => void) {
  window.addEventListener("online", callback);
  window.addEventListener("offline", callback);
  return () => {
    window.removeEventListener("online", callback);
    window.removeEventListener("offline", callback);
  };
}

export function useOnline(): boolean {
  return useSyncExternalStore(
    subscribeOnline,
    () => navigator.onLine,
    () => true,
  );
}
