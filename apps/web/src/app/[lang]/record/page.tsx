import type { Metadata } from "next";
import { Suspense } from "react";

import { RecordPage } from "@/components/recording/RecordPage";
import { pageContext } from "@/i18n/page";

export async function generateMetadata({ params }: PageProps<"/[lang]/record">): Promise<Metadata> {
  const { dict } = await pageContext(params);
  return { title: dict.recording.newTitle };
}

export default async function Page({ params }: PageProps<"/[lang]/record">) {
  const { lang, dict } = await pageContext(params);
  return (
    <Suspense fallback={<p role="status">{dict.common.loading}</p>}>
      <RecordPage dict={dict} lang={lang} />
    </Suspense>
  );
}
