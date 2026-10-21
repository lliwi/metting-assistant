import "server-only";

import type { Locale } from "./config";

const dictionaries = {
  es: () => import("./dictionaries/es.json").then((m) => m.default),
  ca: () => import("./dictionaries/ca.json").then((m) => m.default),
};

export type Dictionary = Awaited<ReturnType<(typeof dictionaries)["es"]>>;

export const getDictionary = async (locale: Locale): Promise<Dictionary> =>
  dictionaries[locale]();
