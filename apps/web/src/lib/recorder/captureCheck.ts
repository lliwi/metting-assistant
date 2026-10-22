/**
 * Comprueba que un segmento contiene el audio que dice contener. Algunos navegadores o
 * dispositivos pierden muestras al capturar: el contenedor dice 12 s y solo hay 4 s.
 */

export const MIN_CAPTURED_RATIO = 0.85;
// La duración que mide la grabadora incluye su arranque: en segmentos cortos el desfase
// relativo es grande sin que falte audio. Solo se avisa si además faltan más de 2 s.
export const MIN_MISSING_MS = 2_000;

let decoder: AudioContext | null = null;

/** Milisegundos de audio realmente presentes en el segmento, o null si no se puede medir. */
export async function capturedMs(blob: Blob): Promise<number | null> {
  if (typeof AudioContext === "undefined") return null;
  try {
    decoder ??= new AudioContext();
    const audio = await decoder.decodeAudioData(await blob.arrayBuffer());
    return audio.duration * 1000;
  } catch {
    return null; // formato no decodificable aquí: no se puede juzgar
  }
}

export function hasCaptureGaps(
  captured: number | null,
  recordedMs: number,
): boolean {
  if (captured === null) return false;
  return (
    recordedMs - captured > MIN_MISSING_MS &&
    captured < recordedMs * MIN_CAPTURED_RATIO
  );
}
