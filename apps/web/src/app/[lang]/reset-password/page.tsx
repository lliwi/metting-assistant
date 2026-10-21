import type { Metadata } from "next";
import { Suspense } from "react";

import { ResetPasswordForm } from "@/components/auth/TokenForms";
import { pageContext } from "@/i18n/page";

export async function generateMetadata({ params }: PageProps<"/[lang]/reset-password">): Promise<Metadata> {
  const { dict } = await pageContext(params);
  return { title: dict.auth.resetTitle };
}

export default async function Page({ params }: PageProps<"/[lang]/reset-password">) {
  const { lang, dict } = await pageContext(params);
  return (
    <Suspense fallback={<p role="status">{dict.common.loading}</p>}>
      <ResetPasswordForm dict={dict} lang={lang} />
    </Suspense>
  );
}
