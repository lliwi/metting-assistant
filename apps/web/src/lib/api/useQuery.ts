"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * Carga datos al montar y cuando cambia `fetcher` (memorízalo con useCallback).
 * Ignora respuestas de cargas anteriores si el componente cambió o se desmontó.
 */
export function useQuery<T>(fetcher: () => Promise<T>) {
  const [data, setData] = useState<T | undefined>(undefined);

  useEffect(() => {
    let active = true;
    void fetcher().then((result) => {
      if (active) setData(result);
    });
    return () => {
      active = false;
    };
  }, [fetcher]);

  const reload = useCallback(async () => {
    setData(await fetcher());
  }, [fetcher]);

  return { data, setData, reload };
}
