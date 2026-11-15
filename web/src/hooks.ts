import { useCallback, useEffect, useState } from "react";

import { describeError } from "./api";

export interface Loaded<T> {
  data: T | null;
  error: string | null;
  reload: () => void;
  setData: (data: T) => void;
}

/** Load once on mount; `reload` fetches again and keeps the old data visible meanwhile. */
export function useLoad<T>(load: () => Promise<T>): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let current = true;
    load().then(
      (result) => {
        if (current) {
          setData(result);
          setError(null);
        }
      },
      (reason: unknown) => {
        if (current) {
          setError(describeError(reason));
        }
      },
    );
    return () => {
      current = false;
    };
    // `load` is a stable api function at every call site.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version]);

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return { data, error, reload, setData };
}

const TIME = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});
const DAY = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", timeZone: "UTC" });

export const formatTime = (iso: string): string => TIME.format(new Date(iso));
export const formatDay = (isoDate: string): string => DAY.format(new Date(`${isoDate}T00:00:00Z`));
