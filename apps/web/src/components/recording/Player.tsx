"use client";

import {
  createContext,
  useCallback,
  useContext,
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
};

const PlayerContext = createContext<PlayerState>({
  currentMs: -1,
  seek: () => undefined,
  available: false,
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

/** Reproductor de la grabación. El audio se sirve con Range: se puede saltar sin descargarlo. */
export function PlayerProvider({
  dict,
  recordingId,
  available,
  children,
}: {
  dict: Dictionary;
  recordingId: string;
  available: boolean;
  children: React.ReactNode;
}) {
  const audio = useRef<HTMLAudioElement>(null);
  const [currentMs, setCurrentMs] = useState(-1);

  const seek = useCallback((ms: number, segmentIdx?: number) => {
    const el = audio.current;
    if (el) {
      el.currentTime = ms / 1000;
      void el.play().catch(() => undefined);
    }
    if (segmentIdx !== undefined) scrollToSegment(segmentIdx);
  }, []);

  const value = useMemo(
    () => ({ currentMs, seek, available }),
    [currentMs, seek, available],
  );

  return (
    <PlayerContext.Provider value={value}>
      {available && (
        <div className="player">
          <audio
            ref={audio}
            controls
            preload="metadata"
            src={`/api/v1/recordings/${recordingId}/audio`}
            aria-label={dict.player.label}
            onTimeUpdate={(e) =>
              setCurrentMs(e.currentTarget.currentTime * 1000)
            }
          />
        </div>
      )}
      {children}
    </PlayerContext.Provider>
  );
}
