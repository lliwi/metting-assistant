import type { Metadata } from "next";
import { Suspense } from "react";

import { RecordingsPage } from "@/components/recording/RecordingsPage";
import { pageContext } from "@/i18n/page";

export async function generateMetadata({ params }: PageProps<"/[lang]/recordings">): Promise<Metadata> {
  const { dict } = await pageContext(params);
  return { title: dict.recordings.title };
}

export default async function Page({ params }: PageProps<"/[lang]/recordings">) {
  const { lang, dict } = await pageContext(params);
  return (
    <Suspense fallback={<p role="status">{dict.common.loading}</p>}>
      <RecordingsPage dict={dict} lang={lang} />
    </Suspense>
  );
}
