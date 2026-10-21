// Comprueba que todos los diccionarios tienen exactamente las mismas claves que el español.
import { readFileSync } from "node:fs";

const load = (locale) =>
  JSON.parse(readFileSync(new URL(`../src/i18n/dictionaries/${locale}.json`, import.meta.url)));

function keys(obj, prefix = "") {
  return Object.entries(obj).flatMap(([k, v]) =>
    v && typeof v === "object" ? keys(v, `${prefix}${k}.`) : [`${prefix}${k}`],
  );
}

const reference = new Set(keys(load("es")));
let failed = false;
for (const locale of ["ca"]) {
  const current = new Set(keys(load(locale)));
  const missing = [...reference].filter((k) => !current.has(k));
  const extra = [...current].filter((k) => !reference.has(k));
  if (missing.length || extra.length) {
    failed = true;
    console.error(`${locale}: faltan [${missing.join(", ")}] sobran [${extra.join(", ")}]`);
  }
}
if (failed) process.exit(1);
console.log("Diccionarios coherentes");
