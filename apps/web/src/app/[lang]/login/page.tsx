import type { Metadata } from "next";
import { Suspense } from "react";

import { LoginForm } from "@/components/auth/LoginForm";
import { pageContext } from "@/i18n/page";

export async function generateMetadata({ params }: PageProps<"/[lang]/login">): Promise<Metadata> {
  const { dict } = await pageContext(params);
  return { title: dict.auth.loginTitle };
}

export default async function Page({ params }: PageProps<"/[lang]/login">) {
  const { lang, dict } = await pageContext(params);
  return (
    <Suspense fallback={<p role="status">{dict.common.loading}</p>}>
      <LoginForm dict={dict} lang={lang} />
    </Suspense>
  );
}
