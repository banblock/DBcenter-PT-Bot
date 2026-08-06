/**
 * 비동기 조회 상태 훅 — loading / error / data 세 가지를 한 번에.
 *
 * React Query 를 넣지 않은 이유: 이 앱의 실시간 데이터는 전부 WS 로 오고,
 * REST 조회는 화면 진입 시 1회 + 수동 새로고침 수준이다. 캐시·무효화 계층을
 * 얹을 만큼 복잡하지 않다. 폴링·캐시가 필요해지면 그때 도입한다.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../services/http/httpClient";

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

export function useAsync<T>(
  loader: () => Promise<T>,
  deps: unknown[] = [],
  options: { immediate?: boolean } = {},
): AsyncState<T> {
  const { immediate = true } = options;
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(immediate);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (!immediate && nonce === 0) return;
    let cancelled = false;
    setLoading(true);
    setError(null);

    loader()
      .then((result) => {
        if (cancelled || !mounted.current) return;
        setData(result);
      })
      .catch((cause) => {
        if (cancelled || !mounted.current) return;
        // 조회 실패는 화면 안에서 보여준다. 토스트는 httpClient 가 이미 띄웠다.
        setError(cause instanceof ApiError ? cause.message : String(cause));
      })
      .finally(() => {
        if (cancelled || !mounted.current) return;
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  return { data, loading, error, reload };
}
