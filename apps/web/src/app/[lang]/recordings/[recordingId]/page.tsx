import type { Metadata } from "next";
import { Suspense } from "react";

import { RecordingDetail } from "@/components/recording/RecordingDetail";
import { pageContext } from "@/i18n/page";

export async function generateMetadata({
  params,
}: PageProps<"/[lang]/recordings/[recordingId]">): Promise<Metadata> {
  const { dict } = await pageContext(params);
  return { title: dict.recordings.title };
}

// El id de la grabación es dinámico: todo lo que lo lee queda dentro de Suspense.
async function Content({ params }: { params: PageProps<"/[lang]/recordings/[recordingId]">["params"] }) {
  const { lang, dict } = await pageContext(params);
  return <RecordingDetail dict={dict} lang={lang} />;
}

export default function Page({ params }: PageProps<"/[lang]/recordings/[recordingId]">) {
  return (
    <Suspense fallback={<p role="status">…</p>}>
      <Content params={params} />
    </Suspense>
  );
}
