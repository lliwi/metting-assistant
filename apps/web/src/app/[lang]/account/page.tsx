import type { Metadata } from "next";
import { Suspense } from "react";

import { AccountPage } from "@/components/account/AccountPage";
import { pageContext } from "@/i18n/page";

export async function generateMetadata({ params }: PageProps<"/[lang]/account">): Promise<Metadata> {
  const { dict } = await pageContext(params);
  return { title: dict.account.title };
}

export default async function Page({ params }: PageProps<"/[lang]/account">) {
  const { lang, dict } = await pageContext(params);
  return (
    <Suspense fallback={<p role="status">{dict.common.loading}</p>}>
      <AccountPage dict={dict} lang={lang} />
    </Suspense>
  );
}
