import type { Metadata } from "next";
import { Suspense } from "react";

import { OrganizationsPage } from "@/components/orgs/OrganizationsPage";
import { pageContext } from "@/i18n/page";

export async function generateMetadata({ params }: PageProps<"/[lang]/organizations">): Promise<Metadata> {
  const { dict } = await pageContext(params);
  return { title: dict.orgs.title };
}

export default async function Page({ params }: PageProps<"/[lang]/organizations">) {
  const { lang, dict } = await pageContext(params);
  return (
    <Suspense fallback={<p role="status">{dict.common.loading}</p>}>
      <OrganizationsPage dict={dict} lang={lang} />
    </Suspense>
  );
}
