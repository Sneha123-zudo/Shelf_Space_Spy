import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useAuth } from "../auth/AuthProvider";
import type {
  AlertRuleUpdate,
  ScanJob,
  Dashboard,
  EventsQuery,
  Market,
  MarketCreate,
  ObservationsQuery,
  Report,
  SuggestRequest,
} from "../types";
import {
  getRecommendations,
  getAlertRules,
  getAlerts,
  getAlertsSummary,
  getLatestScanJob,
  getReplay,
  getScanJob,
  markAlertsRead,
  startScanJob,
  updateAlertRule,
  getDemand,
  getEvents,
  getObservations,
  getOpportunities,
  getPricing,
  getProduct,
  getReports,
  getShelf,
  getSupply,
  createMarket,
  generateWeeklyReport,
  getDashboard,
  getLatestReport,
  getMarket,
  listMarkets,
  scanMarket,
  suggestMarket,
} from "./endpoints";

export const queryKeys = {
  markets: ["markets"] as const,
  market: (id: number) => ["market", id] as const,
  dashboard: (id: number) => ["dashboard", id] as const,
  latestReport: (id: number) => ["report", id] as const,
  section: (id: number, name: string, params?: unknown) => ["section", id, name, params ?? null] as const,
};

/** Everything a scan can affect, so section pages refetch after a scan. */
const sectionKey = (id: number) => ["section", id] as const;

const isValidId = (id: number) => Number.isInteger(id) && id > 0;

export function useMarkets() {
  const { request } = useAuth();
  return useQuery({ queryKey: queryKeys.markets, queryFn: () => listMarkets(request) });
}

export function useMarket(id: number) {
  const { request } = useAuth();
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: queryKeys.market(id),
    queryFn: () => getMarket(request, id),
    enabled: isValidId(id),
    // Show the cached list entry instantly while the single-market request runs.
    placeholderData: () => queryClient.getQueryData<Market[]>(queryKeys.markets)?.find((market) => market.id === id),
  });
}

export function useDashboard(id: number) {
  const { request } = useAuth();
  return useQuery({ queryKey: queryKeys.dashboard(id), queryFn: () => getDashboard(request, id), enabled: isValidId(id) });
}

export function useLatestReport(id: number) {
  const { request } = useAuth();
  return useQuery({ queryKey: queryKeys.latestReport(id), queryFn: () => getLatestReport(request, id), enabled: isValidId(id) });
}

export function useSuggestMarket() {
  const { request } = useAuth();
  return useMutation({ mutationFn: (body: SuggestRequest) => suggestMarket(request, body) });
}

export function useCreateMarket() {
  const { request } = useAuth();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: MarketCreate) => createMarket(request, body),
    onSuccess: (market) => {
      queryClient.setQueryData(queryKeys.market(market.id), market);
      queryClient.setQueryData<Market[]>(queryKeys.markets, (current) =>
        current ? [...current.filter((item) => item.id !== market.id), market] : current,
      );
      void queryClient.invalidateQueries({ queryKey: queryKeys.markets, exact: true });
    },
  });
}

export function useScanMarket(id: number) {
  const { request } = useAuth();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => scanMarket(request, id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.dashboard(id) });
      void queryClient.invalidateQueries({ queryKey: sectionKey(id) });
    },
  });
}

export function useGenerateWeeklyReport(id: number) {
  const { request } = useAuth();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => generateWeeklyReport(request, id),
    onSuccess: (report: Report) => {
      queryClient.setQueryData(queryKeys.latestReport(id), report);
      void queryClient.invalidateQueries({ queryKey: queryKeys.section(id, "reports") });
      // Patch the dashboard's copy instead of refetching (a refetch would dim every panel).
      queryClient.setQueryData<Dashboard>(queryKeys.dashboard(id), (current) => (current ? { ...current, report } : current));
    },
  });
}

// ---------------------------------------------------------------- section queries (read-only)

export function useEvents(id: number, params: EventsQuery) {
  const { request } = useAuth();
  return useQuery({
    queryKey: queryKeys.section(id, "events", params),
    queryFn: () => getEvents(request, id, params),
    enabled: isValidId(id),
    placeholderData: keepPreviousData,
  });
}

export function useObservations(id: number, params: ObservationsQuery) {
  const { request } = useAuth();
  return useQuery({
    queryKey: queryKeys.section(id, "observations", params),
    queryFn: () => getObservations(request, id, params),
    enabled: isValidId(id),
    placeholderData: keepPreviousData,
  });
}

export function useProduct(id: number, productId: number | null) {
  const { request } = useAuth();
  return useQuery({
    queryKey: queryKeys.section(id, "product", productId),
    queryFn: () => getProduct(request, id, productId as number),
    enabled: isValidId(id) && productId !== null,
  });
}

function sectionQuery<T>(
  name: string,
  fetcher: (request: ReturnType<typeof useAuth>["request"], id: number, scanId?: number | null) => Promise<T>,
) {
  /** `scanId` shows a stored scan (Market Replay); omit it for the latest. */
  return function useSection(id: number, scanId: number | null = null) {
    const { request } = useAuth();
    return useQuery({
      queryKey: queryKeys.section(id, name, scanId),
      queryFn: () => fetcher(request, id, scanId),
      enabled: isValidId(id),
      placeholderData: scanId === null ? undefined : keepPreviousData,
    });
  };
}

export const useDemand = sectionQuery("demand", getDemand);
export const useShelf = sectionQuery("shelf", getShelf);
export const useSupply = sectionQuery("supply", getSupply);
export const usePricing = sectionQuery("pricing", getPricing);
export const useOpportunities = sectionQuery("opportunities", getOpportunities);
export const useRecommendations = sectionQuery("recommendations", getRecommendations);
export function useReports(id: number) {
  const { request } = useAuth();
  return useQuery({ queryKey: queryKeys.section(id, "reports"), queryFn: () => getReports(request, id), enabled: isValidId(id) });
}

// ---------------------------------------------------------------- operations

const ACTIVE = new Set(["queued", "running"]);
export const isJobActive = (job: ScanJob | null | undefined) => Boolean(job && ACTIVE.has(job.status));

/** The market's most recent background scan, polled every second while it is active. */
export function useLatestScanJob(id: number) {
  const { request } = useAuth();
  return useQuery({
    queryKey: queryKeys.section(id, "scan-job"),
    queryFn: () => getLatestScanJob(request, id),
    enabled: isValidId(id),
    refetchInterval: (query) => (isJobActive(query.state.data) ? 1000 : false),
    staleTime: 0,
  });
}

export function useStartScanJob(id: number) {
  const { request } = useAuth();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (options: { force?: boolean; refresh?: boolean } = {}) => startScanJob(request, id, options),
    onSuccess: (job) => {
      queryClient.setQueryData(queryKeys.section(id, "scan-job"), job);
    },
  });
}

/** Call when a job reaches a terminal state so every page refetches the new scan. */
export function useRefreshAfterScan(id: number) {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.dashboard(id) });
    void queryClient.invalidateQueries({ queryKey: ["section", id] });
    void queryClient.invalidateQueries({ queryKey: ["alerts-summary"] });
  };
}

export function useScanJobById(id: number, jobId: number | null) {
  const { request } = useAuth();
  return useQuery({
    queryKey: queryKeys.section(id, "scan-job", jobId),
    queryFn: () => getScanJob(request, id, jobId as number),
    enabled: isValidId(id) && jobId !== null,
  });
}

export function useAlertsSummary() {
  const { request } = useAuth();
  return useQuery({
    queryKey: ["alerts-summary"] as const,
    queryFn: () => getAlertsSummary(request),
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
}

export function useAlerts(id: number, unreadOnly: boolean, limit: number, offset: number) {
  const { request } = useAuth();
  return useQuery({
    queryKey: queryKeys.section(id, "alerts", { unreadOnly, limit, offset }),
    queryFn: () => getAlerts(request, id, unreadOnly, limit, offset),
    enabled: isValidId(id),
    placeholderData: keepPreviousData,
  });
}

export function useMarkAlertsRead(id: number) {
  const { request } = useAuth();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (ids?: number[]) => markAlertsRead(request, id, ids),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["section", id, "alerts"] });
      void queryClient.invalidateQueries({ queryKey: ["alerts-summary"] });
    },
  });
}

export function useAlertRules(id: number) {
  const { request } = useAuth();
  return useQuery({ queryKey: queryKeys.section(id, "alert-rules"), queryFn: () => getAlertRules(request, id), enabled: isValidId(id) });
}

export function useUpdateAlertRule(id: number) {
  const { request } = useAuth();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ ruleId, body }: { ruleId: number; body: AlertRuleUpdate }) => updateAlertRule(request, id, ruleId, body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.section(id, "alert-rules") }),
  });
}

export function useReplay(id: number) {
  const { request } = useAuth();
  return useQuery({ queryKey: queryKeys.section(id, "replay"), queryFn: () => getReplay(request, id), enabled: isValidId(id) });
}
