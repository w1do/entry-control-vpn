import { useCallback, useEffect, useRef, useState } from "react";
import { api, isMockMode, subscribeJob, type JobStreamEvent } from "./api";
import type { Job, JobEventData, JobStatus } from "./api-types";

export type StreamStatus = "idle" | "connecting" | "running" | "succeeded" | "failed" | "cancelled";

export interface JobLogEntry {
  key: string;
  seq?: number;
  step?: string;
  message: string;
  level: "debug" | "info" | "warning" | "error";
  progress?: number;
}

export interface JobStreamState {
  status: StreamStatus;
  progress: number;
  events: JobLogEntry[];
  result: unknown;
  error: string | null;
  connectionLost: boolean;
}

interface Options {
  onDone?: (result: unknown) => void;
  onError?: (message: string) => void;
}

const initialState: JobStreamState = {
  status: "idle",
  progress: 0,
  events: [],
  result: null,
  error: null,
  connectionLost: false,
};

const POLL_INTERVAL_MS = 1500;

function mapStatus(status: JobStatus): StreamStatus {
  if (status === "succeeded") return "succeeded";
  if (status === "failed") return "failed";
  if (status === "cancelled") return "cancelled";
  if (status === "queued") return "connecting";
  return "running";
}

/**
 * SSE-прогресс job'а. Параллельно опрашивает GET /jobs/{id}: SSE-события не содержат
 * progress, а job.progress в БД обновляется — так прогресс-бар живой. При обрыве SSE
 * остаётся только поллинг.
 */
export function useJobStream(jobId: number | string | null | undefined, options: Options = {}): JobStreamState {
  const [state, setState] = useState<JobStreamState>(initialState);
  const lastSeq = useRef<number>(0);
  const optionsRef = useRef(options);
  optionsRef.current = options;
  const finishedRef = useRef(false);

  const reset = useCallback(() => {
    lastSeq.current = 0;
    finishedRef.current = false;
    setState(initialState);
  }, []);

  useEffect(() => {
    if (jobId === null || jobId === undefined) {
      reset();
      return;
    }
    reset();
    setState((current) => ({ ...current, status: "connecting" }));

    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let cancelled = false;

    const stopPolling = () => {
      if (pollTimer !== null) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    };

    const applyDone = (result: unknown) => {
      if (cancelled || finishedRef.current) return;
      finishedRef.current = true;
      stopPolling();
      setState((current) => ({
        ...current,
        status: "succeeded",
        progress: 100,
        result,
        connectionLost: false,
      }));
      optionsRef.current.onDone?.(result);
    };

    const applyFailure = (message: string) => {
      if (cancelled || finishedRef.current) return;
      finishedRef.current = true;
      stopPolling();
      setState((current) => ({
        ...current,
        status: "failed",
        error: message,
        connectionLost: false,
      }));
      optionsRef.current.onError?.(message);
    };

    const applyJob = (job: Job) => {
      if (cancelled || finishedRef.current) return;
      const status = mapStatus(job.status);
      const progress = typeof job.progress === "number" ? job.progress : 0;
      const result = job.result ?? job.result_json ?? null;
      if (status === "succeeded") {
        applyDone(result);
        return;
      }
      if (status === "failed" || status === "cancelled") {
        applyFailure(job.error ?? "Job завершился ошибкой");
        return;
      }
      setState((current) => ({ ...current, status, progress: Math.max(current.progress, progress) }));
    };

    const pollOnce = async () => {
      try {
        const job = await api.jobs.get(jobId);
        applyJob(job);
      } catch {
        // временная ошибка — продолжаем опрашивать
      }
    };

    const startPolling = () => {
      if (pollTimer !== null || cancelled || finishedRef.current) return;
      void pollOnce();
      pollTimer = setInterval(() => void pollOnce(), POLL_INTERVAL_MS);
    };

    const processEvent = (event: JobStreamEvent) => {
      if (cancelled || finishedRef.current) return;
      const data: JobEventData = event.data ?? {};
      if (event.event === "progress") {
        if (typeof data.seq === "number" && data.seq > 0) {
          if (data.seq <= lastSeq.current) return;
          lastSeq.current = data.seq;
        }
        const entry: JobLogEntry = {
          key: `${data.seq ?? "e"}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
          seq: data.seq,
          step: data.step,
          message: data.message ?? (data.step ? `Шаг: ${data.step}` : "Выполняется…"),
          level: data.level ?? "info",
        };
        setState((current) => {
          const dedupe = current.events.some(
            (existing) => existing.message === entry.message && existing.step === entry.step,
          );
          return {
            ...current,
            status: "running",
            events: dedupe ? current.events : [...current.events, entry],
          };
        });
        return;
      }
      if (event.event === "done") {
        const status = data.status ? mapStatus(data.status) : "succeeded";
        if (status === "failed" || status === "cancelled") {
          applyFailure(data.error ?? "Job завершился ошибкой");
        } else {
          applyDone(data.result ?? data.data ?? null);
        }
        return;
      }
      if (event.event === "error") {
        if (data.error === "connection_lost") {
          setState((current) => ({ ...current, connectionLost: true }));
          return;
        }
        applyFailure(data.message ?? data.error ?? data.code ?? "Job завершился ошибкой");
      }
    };

    const unsubscribe = subscribeJob(jobId, processEvent);
    startPolling();

    return () => {
      cancelled = true;
      unsubscribe();
      stopPolling();
    };
  }, [jobId, reset]);

  return state;
}

export { isMockMode };
