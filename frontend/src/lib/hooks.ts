"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

import { api, ApiError } from "./api";

export interface Resource<T> {
  data: T | undefined;
  error: ApiError | undefined;
  loading: boolean;
  reload: () => void;
}

/** Fetch a JSON resource; refetches when `path` changes. `null` path = skip. Optional polling. */
export function useApi<T>(path: string | null, opts: { pollMs?: number; pollWhile?: (d: T | undefined) => boolean } = {}): Resource<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<ApiError>();
  const [loading, setLoading] = useState(path !== null);
  const [tick, setTick] = useState(0);
  const pollWhile = useRef(opts.pollWhile);
  useEffect(() => {
    pollWhile.current = opts.pollWhile;
  });

  useEffect(() => {
    if (path === null) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async (first: boolean) => {
      if (first) setLoading(true);
      try {
        const result = await api<T>(path);
        if (cancelled) return;
        setData(result);
        setError(undefined);
        if (opts.pollMs && (!pollWhile.current || pollWhile.current(result))) {
          timer = setTimeout(() => load(false), opts.pollMs);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof ApiError ? e : new ApiError(0, "network_error", "Network error"));
      } finally {
        if (!cancelled && first) setLoading(false);
      }
    };
    load(true);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [path, tick, opts.pollMs]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

function subscribeReducedMotion(onChange: () => void) {
  const mq = window.matchMedia(REDUCED_MOTION);
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}

export function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(subscribeReducedMotion, () => window.matchMedia(REDUCED_MOTION).matches, () => false);
}
