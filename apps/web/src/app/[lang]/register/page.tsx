import type { Metadata } from "next";
import { Suspense } from "react";

import { RegisterForm } from "@/components/auth/RegisterForm";
import { pageContext } from "@/i18n/page";

export async function generateMetadata({ params }: PageProps<"/[lang]/register">): Promise<Metadata> {
  const { dict } = await pageContext(params);
  return { title: dict.auth.registerTitle };
}

export default async function Page({ params }: PageProps<"/[lang]/register">) {
  const { lang, dict } = await pageContext(params);
  return (
    <Suspense fallback={<p role="status">{dict.common.loading}</p>}>
      <RegisterForm dict={dict} lang={lang} />
    </Suspense>
  );
}
