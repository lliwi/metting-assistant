import { notFound } from "next/navigation";

import { HomeGreeting } from "@/components/HomeGreeting";
import { isLocale } from "@/i18n/config";
import { getDictionary } from "@/i18n/dictionaries";

export default async function HomePage({ params }: PageProps<"/[lang]">) {
  const { lang } = await params;
  if (!isLocale(lang)) notFound();
  const dict = await getDictionary(lang);

  return (
    <div className="home">
      <h1>{dict.home.heading}</h1>
      <HomeGreeting template={dict.home.greeting} />
      <p className="lead">{dict.home.lead}</p>

      <div className="actions">
        <button type="button" className="button primary" disabled aria-describedby="soon">
          {dict.home.newRecording}
        </button>
        <button type="button" className="button" disabled aria-describedby="soon">
          {dict.home.importAudio}
        </button>
      </div>
      <p id="soon" className="hint">
        {dict.home.comingSoon}
      </p>

      <p className="notice" role="note">
        {dict.home.aiNotice}
      </p>
    </div>
  );
}
