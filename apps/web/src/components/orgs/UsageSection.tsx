"use client";

import { useCallback, useState } from "react";

import type { Dictionary, Locale } from "@/i18n/client";
import { api } from "@/lib/api/client";
import { useQuery } from "@/lib/api/useQuery";

function currentMonth(): string {
  return new Date().toISOString().slice(0, 7);
}

/** Consumo de IA del mes: trabajos, audio, tokens y coste estimado (solo administradores). */
export function UsageSection({
  dict,
  lang,
  orgId,
}: {
  dict: Dictionary;
  lang: Locale;
  orgId: string;
}) {
  const [month, setMonth] = useState(currentMonth);
  const fetchUsage = useCallback(async () => {
    const { data } = await api.GET("/v1/organizations/{org_id}/usage", {
      params: { path: { org_id: orgId }, query: { month } },
    });
    return data ?? null;
  }, [orgId, month]);
  const { data: usage } = useQuery(fetchUsage);

  const number = new Intl.NumberFormat(lang);
  const minutes = new Intl.NumberFormat(lang, { maximumFractionDigits: 1 });
  const usd = new Intl.NumberFormat(lang, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 4,
  });
  const kindLabel = (kind: string) =>
    dict.usage.kinds[kind as keyof typeof dict.usage.kinds] ?? kind;

  return (
    <section className="card stack" aria-labelledby="usage-heading">
      <h2 id="usage-heading">{dict.usage.title}</h2>
      <div className="field">
        <label htmlFor="usage-month">{dict.usage.month}</label>
        <input
          id="usage-month"
          type="month"
          value={month}
          max={currentMonth()}
          onChange={(e) => e.target.value && setMonth(e.target.value)}
        />
      </div>
      {usage === undefined ? (
        <p role="status">{dict.common.loading}</p>
      ) : !usage || usage.items.length === 0 ? (
        <p className="hint">{dict.usage.empty}</p>
      ) : (
        <>
          <p className="usage-total">
            {dict.usage.total}:{" "}
            <strong>{usd.format(Number(usage.total_cost_usd))}</strong>
          </p>
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">{dict.usage.kind}</th>
                  <th scope="col" className="num">
                    {dict.usage.cost}
                  </th>
                  <th scope="col" className="num">
                    {dict.usage.jobs}
                  </th>
                  <th scope="col" className="num">
                    {dict.usage.audioMinutes}
                  </th>
                  <th scope="col" className="num">
                    {dict.usage.tokens}
                  </th>
                </tr>
              </thead>
              <tbody>
                {usage.items.map((i) => (
                  <tr key={`${i.kind}-${i.provider}-${i.model}`}>
                    <td>
                      {kindLabel(i.kind)}
                      <span className="hint block">
                        {i.provider} · {i.model}
                      </span>
                    </td>
                    <td className="num">
                      {i.cost_usd == null
                        ? dict.usage.noPrice
                        : usd.format(Number(i.cost_usd))}
                    </td>
                    <td className="num">{number.format(i.jobs)}</td>
                    <td className="num">
                      {i.audio_ms ? minutes.format(i.audio_ms / 60_000) : "—"}
                    </td>
                    <td className="num">
                      {i.input_tokens || i.output_tokens ? (
                        <>
                          {number.format(i.input_tokens)}
                          <span className="hint block">
                            {number.format(i.output_tokens)}
                          </span>
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="hint">
            {dict.usage.tokensHint}
            {usage.unpriced && ` ${dict.usage.unpricedHint}`}
          </p>
        </>
      )}
    </section>
  );
}
