import type {
  RecommendationsSection,
  AlertRule,
  AlertRuleUpdate,
  AlertsPage,
  AlertsSummary,
  ReplaySection,
  ScanJob,
  DemandSection,
  EventsPage,
  EventsQuery,
  ObservationsPage,
  ObservationsQuery,
  OpportunitiesSection,
  PricingSection,
  ProductHistory,
  ReportsSection,
  ShelfSection,
  SupplySection,
  Dashboard,
  LoginResponse,
  Market,
  MarketCreate,
  Report,
  ScanResult,
  SuggestRequest,
  SuggestResponse,
} from "../types";
import { api, type AuthedApi } from "./client";
import { isNotFound } from "./errors";

const post = (body?: unknown): RequestInit => ({ method: "POST", ...(body === undefined ? {} : { body: JSON.stringify(body) }) });

export const login = (email: string, password: string) => api<LoginResponse>("/auth/login", post({ email, password }));

export const listMarkets = (request: AuthedApi) => request<Market[]>("/markets");

export const getMarket = (request: AuthedApi, id: number) => request<Market>(`/markets/${id}`);

export const createMarket = (request: AuthedApi, body: MarketCreate) => request<Market>("/markets", post(body));

export const suggestMarket = (request: AuthedApi, body: SuggestRequest) => request<SuggestResponse>("/markets/suggest", post(body));

export const getDashboard = (request: AuthedApi, id: number) => request<Dashboard>(`/markets/${id}/dashboard`);

export const scanMarket = (request: AuthedApi, id: number) => request<ScanResult>(`/markets/${id}/scan`, post());

export const generateWeeklyReport = (request: AuthedApi, id: number) => request<Report>(`/markets/${id}/reports/weekly`, post());

/** Resolves to null when the market has no report yet (404). */
export async function getLatestReport(request: AuthedApi, id: number): Promise<Report | null> {
  try {
    return await request<Report | null>(`/markets/${id}/reports/latest`);
  } catch (error) {
    if (isNotFound(error)) return null;
    throw error;
  }
}

// ---------------------------------------------------------------- section endpoints (read-only)

function query(params: Record<string, string | number | boolean | string[] | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "" || value === false) continue;
    if (Array.isArray(value)) value.forEach((item) => search.append(key, item));
    else search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const getEvents = (request: AuthedApi, id: number, q: EventsQuery) =>
  request<EventsPage>(
    `/markets/${id}/events${query({ type: q.types, include_low_confidence: q.includeLow, limit: q.limit, offset: q.offset, scan_id: q.scanId ?? undefined })}`,
  );

export const getObservations = (request: AuthedApi, id: number, q: ObservationsQuery) => {
  const { scanId, ...filters } = q;
  return request<ObservationsPage>(`/markets/${id}/observations${query({ ...filters, scan_id: scanId ?? undefined })}`);
};

export const getProduct = (request: AuthedApi, id: number, productId: number) =>
  request<ProductHistory>(`/markets/${id}/products/${productId}`);
export const getDemand = (request: AuthedApi, id: number, scanId?: number | null) =>
  request<DemandSection>(`/markets/${id}/demand${query({ scan_id: scanId ?? undefined })}`);
export const getShelf = (request: AuthedApi, id: number, scanId?: number | null) =>
  request<ShelfSection>(`/markets/${id}/shelf${query({ scan_id: scanId ?? undefined })}`);
export const getSupply = (request: AuthedApi, id: number, scanId?: number | null) =>
  request<SupplySection>(`/markets/${id}/supply${query({ scan_id: scanId ?? undefined })}`);
export const getPricing = (request: AuthedApi, id: number, scanId?: number | null) =>
  request<PricingSection>(`/markets/${id}/pricing${query({ scan_id: scanId ?? undefined })}`);
export const getOpportunities = (request: AuthedApi, id: number, scanId?: number | null) =>
  request<OpportunitiesSection>(`/markets/${id}/opportunities${query({ scan_id: scanId ?? undefined })}`);
export const getReports = (request: AuthedApi, id: number) => request<ReportsSection>(`/markets/${id}/reports`);

// ---------------------------------------------------------------- operations

export const startScanJob = (request: AuthedApi, id: number, options: { force?: boolean; refresh?: boolean } = {}) =>
  request<ScanJob>(`/markets/${id}/scan-jobs`, post(options));

export const getScanJob = (request: AuthedApi, id: number, jobId: number) => request<ScanJob>(`/markets/${id}/scan-jobs/${jobId}`);

/** Null when the market has never had a background scan. */
export const getLatestScanJob = (request: AuthedApi, id: number) => request<ScanJob | null>(`/markets/${id}/scan-jobs/latest`);

export const getAlerts = (request: AuthedApi, id: number, unreadOnly: boolean, limit: number, offset: number) =>
  request<AlertsPage>(`/markets/${id}/alerts${query({ unread_only: unreadOnly, limit, offset })}`);

export const getAlertsSummary = (request: AuthedApi) => request<AlertsSummary>("/alerts/summary");

export const markAlertsRead = (request: AuthedApi, id: number, ids?: number[]) =>
  request<{ updated: number }>(`/markets/${id}/alerts/read`, post(ids ? { ids } : undefined));

export const getAlertRules = (request: AuthedApi, id: number) => request<AlertRule[]>(`/markets/${id}/alert-rules`);

export const updateAlertRule = (request: AuthedApi, id: number, ruleId: number, body: AlertRuleUpdate) =>
  request<AlertRule>(`/markets/${id}/alert-rules/${ruleId}`, { method: "PATCH", body: JSON.stringify(body) });

export const getReplay = (request: AuthedApi, id: number) => request<ReplaySection>(`/markets/${id}/replay`);

export const getRecommendations = (request: AuthedApi, id: number, scanId?: number | null) =>
  request<RecommendationsSection>(`/markets/${id}/recommendations${query({ scan_id: scanId ?? undefined })}`);
