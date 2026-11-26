"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import type { Dictionary } from "@/i18n/client";

type PlayerState = {
  /** Posición actual de reproducción en ms (para resaltar la frase que suena). */
  currentMs: number;
  /** Salta a ese instante, reproduce y lleva la vista a la frase. */
  seek: (ms: number, segmentIdx?: number) => void;
  available: boolean;
  audio: React.RefObject<HTMLAudioElement | null>;
  recordingId: string;
  setCurrentMs: (ms: number) => void;
};

const PlayerContext = createContext<PlayerState>({
  currentMs: -1,
  seek: () => undefined,
  available: false,
  audio: { current: null },
  recordingId: "",
  setCurrentMs: () => undefined,
});

export function usePlayer(): PlayerState {
  return useContext(PlayerContext);
}

export function scrollToSegment(idx: number) {
  const el = document.getElementById(`seg-${idx}`);
  if (!el) return;
  el.scrollIntoView({ block: "center", behavior: "smooth" });
  history.replaceState(null, "", `#seg-${idx}`);
}

/** Estado del reproductor. `reveal` muestra la transcripción antes de ir a una frase. */
export function PlayerProvider({
  recordingId,
  available,
  reveal,
  children,
}: {
  recordingId: string;
  available: boolean;
  reveal?: () => void;
  children: React.ReactNode;
}) {
  const audio = useRef<HTMLAudioElement>(null);
  const [currentMs, setCurrentMs] = useState(-1);
  const revealRef = useRef(reveal);
  useEffect(() => {
    revealRef.current = reveal;
  });

  const seek = useCallback((ms: number, segmentIdx?: number) => {
    const el = audio.current;
    if (el) {
      el.currentTime = ms / 1000;
      void el.play().catch(() => undefined);
    }
    if (segmentIdx !== undefined) {
      revealRef.current?.();
      // Tras mostrar la pestaña de la transcripción.
      requestAnimationFrame(() => scrollToSegment(segmentIdx));
    }
  }, []);

  const value = useMemo(
    () => ({ currentMs, seek, available, audio, recordingId, setCurrentMs }),
    [currentMs, seek, available, recordingId],
  );

  return (
    <PlayerContext.Provider value={value}>{children}</PlayerContext.Provider>
  );
}

/** Reproductor fijo bajo la cabecera. El audio se sirve con Range: se salta sin descargarlo. */
export function PlayerBar({ dict }: { dict: Dictionary }) {
  const { available, audio, recordingId, setCurrentMs } = usePlayer();
  if (!available) return null;
  return (
    <div className="player">
      <audio
        ref={audio}
        controls
        preload="metadata"
        src={`/api/v1/recordings/${recordingId}/audio`}
        aria-label={dict.player.label}
        onTimeUpdate={(e) => setCurrentMs(e.currentTarget.currentTime * 1000)}
      />
    </div>
  );
}
