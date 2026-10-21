"use client";

import { useAuth } from "@/components/auth/AuthProvider";
import { format } from "@/i18n/format";

export function HomeGreeting({ template }: { template: string }) {
  const { user } = useAuth();
  if (!user) return null;
  return <p className="greeting">{format(template, { name: user.name })}</p>;
}
