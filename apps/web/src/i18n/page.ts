import "server-only";

import { notFound } from "next/navigation";

import { isLocale, type Locale } from "./config";
import { getDictionary, type Dictionary } from "./dictionaries";

/** Valida el idioma de la ruta y carga su diccionario. */
export async function pageContext(
  params: Promise<{ lang: string }>,
): Promise<{ lang: Locale; dict: Dictionary }> {
  const { lang } = await params;
  if (!isLocale(lang)) notFound();
  return { lang, dict: await getDictionary(lang) };
}
