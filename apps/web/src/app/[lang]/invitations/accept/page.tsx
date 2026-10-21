import type { Metadata } from "next";
import { Suspense } from "react";

import { AcceptInvitation } from "@/components/orgs/AcceptInvitation";
import { pageContext } from "@/i18n/page";

export async function generateMetadata({ params }: PageProps<"/[lang]/invitations/accept">): Promise<Metadata> {
  const { dict } = await pageContext(params);
  return { title: dict.orgs.acceptTitle };
}

export default async function Page({ params }: PageProps<"/[lang]/invitations/accept">) {
  const { lang, dict } = await pageContext(params);
  return (
    <Suspense fallback={<p role="status">{dict.common.loading}</p>}>
      <AcceptInvitation dict={dict} lang={lang} />
    </Suspense>
  );
}
