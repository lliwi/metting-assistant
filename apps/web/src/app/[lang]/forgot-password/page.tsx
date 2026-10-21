import type { Metadata } from "next";
import { Suspense } from "react";

import { ForgotPasswordForm } from "@/components/auth/TokenForms";
import { pageContext } from "@/i18n/page";

export async function generateMetadata({ params }: PageProps<"/[lang]/forgot-password">): Promise<Metadata> {
  const { dict } = await pageContext(params);
  return { title: dict.auth.forgotTitle };
}

export default async function Page({ params }: PageProps<"/[lang]/forgot-password">) {
  const { lang, dict } = await pageContext(params);
  return (
    <Suspense fallback={<p role="status">{dict.common.loading}</p>}>
      <ForgotPasswordForm dict={dict} lang={lang} />
    </Suspense>
  );
}
