import { describe, expect, it } from "vitest";

import { capturedMs, hasCaptureGaps } from "./captureCheck";

describe("hasCaptureGaps", () => {
  it("detecta la pérdida observada en un navegador real (4 s de 11 s)", () => {
    expect(hasCaptureGaps(4_080, 11_200)).toBe(true);
  });

  it("no avisa por el arranque de la grabadora en grabaciones cortas", () => {
    // Caso real: 6,8 s capturados de 8,5 s declarados; el audio estaba completo.
    expect(hasCaptureGaps(6_840, 8_462)).toBe(false);
  });

  it("tolera pequeñas diferencias en segmentos largos", () => {
    expect(hasCaptureGaps(29_100, 30_000)).toBe(false);
  });

  it("no juzga si no se pudo medir", () => {
    expect(hasCaptureGaps(null, 30_000)).toBe(false);
  });
});

describe("capturedMs", () => {
  it("devuelve null sin Web Audio (no se puede comprobar)", async () => {
    expect(await capturedMs(new Blob(["x"]))).toBeNull();
  });
});
