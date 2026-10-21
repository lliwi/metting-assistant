import type { Metadata } from "next";
import { Suspense } from "react";

import { VerifyEmail } from "@/components/auth/TokenForms";
import { pageContext } from "@/i18n/page";

export async function generateMetadata({ params }: PageProps<"/[lang]/verify-email">): Promise<Metadata> {
  const { dict } = await pageContext(params);
  return { title: dict.auth.verifyTitle };
}

export default async function Page({ params }: PageProps<"/[lang]/verify-email">) {
  const { lang, dict } = await pageContext(params);
  return (
    <Suspense fallback={<p role="status">{dict.common.loading}</p>}>
      <VerifyEmail dict={dict} lang={lang} />
    </Suspense>
  );
}
