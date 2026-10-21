import type { Metadata } from "next";
import { Suspense } from "react";

import { OrganizationDetail } from "@/components/orgs/OrganizationDetail";
import { pageContext } from "@/i18n/page";

export async function generateMetadata({
  params,
}: PageProps<"/[lang]/organizations/[orgId]">): Promise<Metadata> {
  const { dict } = await pageContext(params);
  return { title: dict.orgs.title };
}

// El id de la organización es dinámico: todo lo que lo lee queda dentro de Suspense.
async function Content({ params }: { params: PageProps<"/[lang]/organizations/[orgId]">["params"] }) {
  const { lang, dict } = await pageContext(params);
  return <OrganizationDetail dict={dict} lang={lang} />;
}

export default function Page({ params }: PageProps<"/[lang]/organizations/[orgId]">) {
  return (
    <Suspense fallback={<p role="status">…</p>}>
      <Content params={params} />
    </Suspense>
  );
}
