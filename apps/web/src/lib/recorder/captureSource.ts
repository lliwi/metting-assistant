/**
 * Fuentes de audio que alimentan la grabadora. Una reunión presencial usa solo el micrófono;
 * una reunión online (Meet, Teams, Zoom web…) mezcla el audio de la pestaña o pantalla
 * compartida con el micrófono, de modo que la grabadora sigue viendo un único MediaStream.
 */

export type CaptureMode = "microphone" | "meeting";

export type CaptureSource = {
  /** Lo que se graba. */
  stream: MediaStream;
  /** Pistas originales: si cualquiera termina (permiso revocado, se deja de compartir…), la
   *  captura se considera interrumpida. */
  inputs: MediaStreamTrack[];
  /** Libera dispositivos y el grafo de audio. Se puede llamar varias veces. */
  dispose: () => void;
};

export const MIC_CONSTRAINTS: MediaTrackConstraints = {
  echoCancellation: true,
  noiseSuppression: true,
  autoGainControl: true,
};

/** Audio de la reunión tal cual: el procesado pensado para micrófonos lo degradaría. */
const DISPLAY_AUDIO: MediaTrackConstraints & {
  suppressLocalAudioPlayback?: boolean;
} = {
  echoCancellation: false,
  noiseSuppression: false,
  autoGainControl: false,
  // Que el usuario siga oyendo la reunión mientras se captura.
  suppressLocalAudioPlayback: false,
};

/** Opciones de Chrome/Edge que aún no están en los tipos del DOM de TypeScript. */
type DisplayOptions = DisplayMediaStreamOptions & {
  systemAudio?: "include" | "exclude";
  selfBrowserSurface?: "include" | "exclude";
  surfaceSwitching?: "include" | "exclude";
  preferCurrentTab?: boolean;
};

export function supportsMeetingCapture(): boolean {
  if (typeof navigator === "undefined") return false;
  if (!navigator.mediaDevices?.getDisplayMedia) return false;
  // Los navegadores móviles no comparten pestañas ni audio del sistema.
  return !/Android|iPad|iPhone|iPod/.test(navigator.userAgent);
}

export function microphoneSource(stream: MediaStream): CaptureSource {
  return {
    stream,
    inputs: stream.getAudioTracks(),
    dispose: () => stream.getTracks().forEach((t) => t.stop()),
  };
}

/** Error al compartir sin audio: el navegador no lo trata como fallo, así que se lanza aquí. */
export class NoDisplayAudioError extends Error {
  constructor() {
    super("no_display_audio");
    this.name = "NoDisplayAudioError";
  }
}

/**
 * Pide al usuario la pestaña o pantalla de la reunión. Debe llamarse sin ningún `await`
 * previo dentro del gesto del usuario: el navegador exige activación reciente.
 */
export async function requestDisplayAudio(): Promise<MediaStream> {
  const options: DisplayOptions = {
    // Chrome solo ofrece compartir audio si también se pide vídeo; se descarta enseguida.
    video: true,
    audio: DISPLAY_AUDIO,
    systemAudio: "include",
    selfBrowserSurface: "exclude",
    surfaceSwitching: "include",
    preferCurrentTab: false,
  };
  const display = await navigator.mediaDevices.getDisplayMedia(options);
  display.getVideoTracks().forEach((t) => {
    t.stop();
    display.removeTrack(t);
  });
  if (display.getAudioTracks().length === 0) {
    throw new NoDisplayAudioError();
  }
  return display;
}

/** Mezcla el audio de la reunión con el micrófono (si lo hay) en una única pista. */
export function meetingSource(
  display: MediaStream,
  mic: MediaStream | null,
): CaptureSource {
  const context = new AudioContext();
  const destination = context.createMediaStreamDestination();
  context.createMediaStreamSource(display).connect(destination);
  if (mic) context.createMediaStreamSource(mic).connect(destination);
  // Puede crearse suspendido si la activación del usuario ya ha caducado.
  void context.resume();

  let disposed = false;
  return {
    stream: destination.stream,
    inputs: [
      ...display.getAudioTracks(),
      ...(mic ? mic.getAudioTracks() : []),
    ],
    dispose: () => {
      if (disposed) return;
      disposed = true;
      display.getTracks().forEach((t) => t.stop());
      mic?.getTracks().forEach((t) => t.stop());
      destination.stream.getTracks().forEach((t) => t.stop());
      void context.close();
    },
  };
}
