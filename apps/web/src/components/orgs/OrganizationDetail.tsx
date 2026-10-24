"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useState } from "react";

import { useAuth } from "@/components/auth/AuthProvider";
import { RequireAuth } from "@/components/auth/RequireAuth";
import {
  errorMessage,
  Field,
  FormMessage,
  useAction,
} from "@/components/forms";
import type { Dictionary, Locale } from "@/i18n/client";
import { format } from "@/i18n/format";
import { api, errorCode, type Schemas, type User } from "@/lib/api/client";
import { useQuery } from "@/lib/api/useQuery";

import { DeleteOrganization } from "./DeleteOrganization";
import { UsageSection } from "./UsageSection";

type Props = { dict: Dictionary; lang: Locale };
type Role = Schemas["Role"];
type Org = Schemas["OrganizationOut"];

const ROLES: Role[] = ["owner", "admin", "member", "viewer"];
const canManage = (role: Role) => role === "owner" || role === "admin";

export function OrganizationDetail({ dict, lang }: Props) {
  return (
    <RequireAuth lang={lang} loadingLabel={dict.common.loading}>
      {(user) => <Detail dict={dict} lang={lang} user={user} />}
    </RequireAuth>
  );
}

function Detail({ dict, lang, user }: Props & { user: User }) {
  const orgId = useParams<{ orgId: string }>().orgId;
  const fetchOrg = useCallback(async () => {
    const { data, error } = await api.GET("/v1/organizations/{org_id}", {
      params: { path: { org_id: orgId } },
    });
    return data
      ? { org: data }
      : { error: errorMessage(dict, errorCode(error)) };
  }, [orgId, dict]);
  const { data: result, setData } = useQuery<{ org?: Org; error?: string }>(
    fetchOrg,
  );
  const org = result?.org;
  const setOrg = (next: Org) => setData({ org: next });

  if (result?.error) return <p className="alert error">{result.error}</p>;
  if (!org) return <p role="status">{dict.common.loading}</p>;

  return (
    <div className="stack-lg">
      <h1>{org.is_personal ? dict.orgs.personal : org.name}</h1>
      <p>
        {dict.common.role}:{" "}
        <span className="badge">{dict.roles[org.role]}</span>
      </p>
      {canManage(org.role) && (
        <Settings dict={dict} org={org} onSaved={setOrg} />
      )}
      <Members dict={dict} lang={lang} org={org} user={user} />
      {!org.is_personal && canManage(org.role) && (
        <Invitations dict={dict} org={org} />
      )}
      {org.is_personal && <p className="hint">{dict.orgs.personalNoMembers}</p>}
      {canManage(org.role) && (
        <UsageSection dict={dict} lang={lang} orgId={org.id} />
      )}
      {org.role === "owner" && !org.is_personal && (
        <DeleteOrganization dict={dict} lang={lang} org={org} />
      )}
    </div>
  );
}

function Settings({
  dict,
  org,
  onSaved,
}: {
  dict: Dictionary;
  org: Org;
  onSaved: (o: Org) => void;
}) {
  const { refresh } = useAuth();
  const { pending, error, run } = useAction(dict);
  const [name, setName] = useState(org.name);
  const [retention, setRetention] = useState(String(org.retention_days ?? 0));
  const [saved, setSaved] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSaved(false);
    const data = await run(() =>
      api.PATCH("/v1/organizations/{org_id}", {
        params: { path: { org_id: org.id } },
        body: { name, retention_days: Number(retention) },
      }),
    );
    if (data) {
      onSaved(data);
      setSaved(true);
      void refresh();
    }
  }

  return (
    <form onSubmit={submit} className="card stack">
      <h2>{dict.orgs.settings}</h2>
      {!org.is_personal && (
        <Field
          label={dict.common.name}
          required
          maxLength={200}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      )}
      <Field
        label={dict.orgs.retentionDays}
        type="number"
        min={0}
        max={3650}
        required
        value={retention}
        onChange={(e) => setRetention(e.target.value)}
      />
      <FormMessage error={error} success={saved ? dict.common.saved : null} />
      <button type="submit" className="button" disabled={pending}>
        {dict.common.save}
      </button>
    </form>
  );
}

function Members({ dict, lang, org, user }: Props & { org: Org; user: User }) {
  const router = useRouter();
  const { refresh } = useAuth();
  const { error, run } = useAction(dict);
  const manage = canManage(org.role) && !org.is_personal;
  const fetchMembers = useCallback(
    async () =>
      (
        await api.GET("/v1/organizations/{org_id}/members", {
          params: { path: { org_id: org.id } },
        })
      ).data ?? [],
    [org.id],
  );
  const { data: members = [], reload: load } = useQuery(fetchMembers);

  async function changeRole(memberId: string, role: Role) {
    await run(() =>
      api.PATCH("/v1/organizations/{org_id}/members/{member_id}", {
        params: { path: { org_id: org.id, member_id: memberId } },
        body: { role },
      }),
    );
    await load();
  }

  async function remove(member: Schemas["MemberOut"]) {
    const self = member.user_id === user.id;
    const question = self
      ? dict.orgs.leaveConfirm
      : format(dict.orgs.removeConfirm, { name: member.name });
    if (!window.confirm(question)) return;
    const res = await run(() =>
      api.DELETE("/v1/organizations/{org_id}/members/{member_id}", {
        params: { path: { org_id: org.id, member_id: member.user_id } },
      }),
    );
    if (res === undefined) return;
    if (self) {
      await refresh();
      router.push(`/${lang}/organizations`);
    } else {
      await load();
    }
  }

  // Un admin no puede asignar ni modificar propietarios.
  const assignable =
    org.role === "owner" ? ROLES : ROLES.filter((r) => r !== "owner");

  return (
    <section className="card" aria-labelledby="members-heading">
      <h2 id="members-heading">{dict.orgs.members}</h2>
      <ul className="list">
        {members.map((m) => {
          const self = m.user_id === user.id;
          const editable =
            manage && !self && (org.role === "owner" || m.role !== "owner");
          return (
            <li key={m.user_id} className="list-row">
              <div>
                <div>
                  {m.name}{" "}
                  {self && <span className="hint">({dict.orgs.you})</span>}
                </div>
                <div className="hint">{m.email}</div>
              </div>
              <div className="row">
                {editable ? (
                  <select
                    aria-label={`${dict.common.role}: ${m.name}`}
                    value={m.role}
                    onChange={(e) =>
                      changeRole(m.user_id, e.target.value as Role)
                    }
                  >
                    {assignable.map((r) => (
                      <option key={r} value={r}>
                        {dict.roles[r]}
                      </option>
                    ))}
                  </select>
                ) : (
                  <span className="badge">{dict.roles[m.role]}</span>
                )}
                {editable && (
                  <button
                    type="button"
                    className="button small"
                    onClick={() => remove(m)}
                  >
                    {dict.common.remove}
                  </button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      {!org.is_personal && (
        <button
          type="button"
          className="button small danger"
          onClick={() => {
            const me = members.find((m) => m.user_id === user.id);
            if (me) void remove(me);
          }}
        >
          {dict.orgs.leave}
        </button>
      )}
      <FormMessage error={error} />
    </section>
  );
}

function Invitations({ dict, org }: { dict: Dictionary; org: Org }) {
  const { pending, error, run } = useAction(dict);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [sentTo, setSentTo] = useState<string | null>(null);

  const fetchInvitations = useCallback(
    async () =>
      (
        await api.GET("/v1/organizations/{org_id}/invitations", {
          params: { path: { org_id: org.id } },
        })
      ).data ?? [],
    [org.id],
  );
  const { data: items = [], reload: load } = useQuery(fetchInvitations);

  async function invite(e: React.FormEvent) {
    e.preventDefault();
    setSentTo(null);
    const data = await run(() =>
      api.POST("/v1/organizations/{org_id}/invitations", {
        params: { path: { org_id: org.id } },
        body: { email, role },
      }),
    );
    if (data) {
      setSentTo(data.email);
      setEmail("");
      await load();
    }
  }

  async function revoke(id: string) {
    await run(() =>
      api.DELETE("/v1/organizations/{org_id}/invitations/{invitation_id}", {
        params: { path: { org_id: org.id, invitation_id: id } },
      }),
    );
    await load();
  }

  const roles =
    org.role === "owner" ? ROLES : ROLES.filter((r) => r !== "owner");

  return (
    <section className="card stack" aria-labelledby="invitations-heading">
      <h2 id="invitations-heading">{dict.orgs.invitations}</h2>
      {items.length > 0 && (
        <ul className="list">
          {items.map((i) => (
            <li key={i.id} className="list-row">
              <div>
                {i.email} <span className="badge">{dict.roles[i.role]}</span>
              </div>
              <button
                type="button"
                className="button small"
                onClick={() => revoke(i.id)}
              >
                {dict.orgs.revoke}
              </button>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={invite} className="stack">
        <Field
          label={dict.orgs.inviteEmail}
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <div className="field">
          <label htmlFor="invite-role">{dict.common.role}</label>
          <select
            id="invite-role"
            value={role}
            onChange={(e) => setRole(e.target.value as Role)}
          >
            {roles.map((r) => (
              <option key={r} value={r}>
                {dict.roles[r]}
              </option>
            ))}
          </select>
        </div>
        <FormMessage
          error={error}
          success={sentTo ? format(dict.orgs.invited, { email: sentTo }) : null}
        />
        <button type="submit" className="button primary" disabled={pending}>
          {dict.orgs.invite}
        </button>
      </form>
    </section>
  );
}
