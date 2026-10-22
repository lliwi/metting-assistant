"use client";

import { useCallback, useState } from "react";

import type { Dictionary, Locale } from "@/i18n/client";
import { api, type Schemas } from "@/lib/api/client";
import { useQuery } from "@/lib/api/useQuery";

export type Language = Schemas["RecordingCreate"]["language"];

/** Organización, idioma y consentimiento: comunes a grabar e importar. */
export function useSetupState(lang: Locale) {
  const fetchOrgs = useCallback(
    async () => (await api.GET("/v1/organizations")).data ?? [],
    [],
  );
  const { data: orgs } = useQuery(fetchOrgs);
  const writable = (orgs ?? []).filter((o) => o.role !== "viewer");
  const [orgId, setOrgId] = useState("");
  const [language, setLanguage] = useState<Language>(
    lang === "ca" ? "ca" : "es",
  );
  const [consent, setConsent] = useState(false);
  return {
    writable,
    organizationId: orgId || writable[0]?.id || "",
    setOrgId,
    language,
    setLanguage,
    consent,
    setConsent,
  };
}

export function SetupFields({
  dict,
  state,
}: {
  dict: Dictionary;
  state: ReturnType<typeof useSetupState>;
}) {
  return (
    <>
      <div className="field">
        <label htmlFor="org">{dict.recording.organization}</label>
        <select
          id="org"
          required
          value={state.organizationId}
          onChange={(e) => state.setOrgId(e.target.value)}
        >
          {state.writable.map((o) => (
            <option key={o.id} value={o.id}>
              {o.is_personal ? dict.orgs.personal : o.name}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="language">{dict.recording.language}</label>
        <select
          id="language"
          value={state.language}
          onChange={(e) => state.setLanguage(e.target.value as Language)}
        >
          {(["es", "ca", "en"] as const).map((l) => (
            <option key={l} value={l}>
              {dict.recording.languages[l]}
            </option>
          ))}
        </select>
      </div>

      <fieldset className="consent">
        <legend>{dict.recording.consentTitle}</legend>
        <p>{dict.recording.consentText}</p>
        <label className="checkbox">
          <input
            type="checkbox"
            required
            checked={state.consent}
            onChange={(e) => state.setConsent(e.target.checked)}
          />
          {dict.recording.consentCheck}
        </label>
      </fieldset>
    </>
  );
}
