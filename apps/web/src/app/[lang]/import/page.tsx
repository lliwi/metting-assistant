import type { Metadata } from "next";
import { Suspense } from "react";

import { ImportPage } from "@/components/recording/ImportPage";
import { pageContext } from "@/i18n/page";

export async function generateMetadata({ params }: PageProps<"/[lang]/import">): Promise<Metadata> {
  const { dict } = await pageContext(params);
  return { title: dict.import.title };
}

export default async function Page({ params }: PageProps<"/[lang]/import">) {
  const { lang, dict } = await pageContext(params);
  return (
    <Suspense fallback={<p role="status">{dict.common.loading}</p>}>
      <ImportPage dict={dict} lang={lang} />
    </Suspense>
  );
}
