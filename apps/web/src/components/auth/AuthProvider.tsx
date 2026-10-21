"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { api, type User } from "@/lib/api/client";

type AuthState = {
  user: User | null;
  loading: boolean;
  refresh: () => Promise<User | null>;
  setUser: (user: User | null) => void;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const { data } = await api.GET("/v1/auth/me");
    const next = data ?? null;
    setUser(next);
    setLoading(false);
    return next;
  }, []);

  const logout = useCallback(async () => {
    await api.POST("/v1/auth/logout");
    setUser(null);
  }, []);

  useEffect(() => {
    let active = true;
    void api.GET("/v1/auth/me").then(({ data }) => {
      if (!active) return;
      setUser(data ?? null);
      setLoading(false);
    });
    return () => {
      active = false;
    };
  }, []);

  const value = useMemo(
    () => ({ user, loading, refresh, setUser, logout }),
    [user, loading, refresh, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth debe usarse dentro de <AuthProvider>");
  return ctx;
}
