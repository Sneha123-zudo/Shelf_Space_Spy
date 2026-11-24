// In-memory stand-in for the FastAPI backend, enabled with VITE_USE_MOCK=1.
// It follows the same contract as the real API (see src/types) so the UI can be
// built and demoed without a backend or any SerpAPI credits. Brand names are fictional.
// State lives in memory and resets on page reload.

import type {
  CompetitorRow,
  Dashboard,
  LoginResponse,
  Market,
  MarketCreate,
  MarketEvent,
  Opportunity,
  Report,
  ScanResult,
  ShelfShareEntry,
  SuggestRequest,
  SuggestResponse,
  User,
} from "../types";
import { ApiError } from "./errors";

const DEMO_EMAIL = "demo@shelfspacespy.com";
const DEMO_PASSWORD = "Demo@123";
const MOCK_TOKEN = "mock-jwt-token";
const DEMO_USER: User = { id: 1, email: DEMO_EMAIL, name: "Demo Founder" };

type MarketState = { market: Market; scans: number; seed: number; reports: Report[]; demoHistory: boolean };

// ---------------------------------------------------------------- utilities

function mulberry32(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function hashText(text: string): number {
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

const round1 = (value: number) => Math.round(value * 10) / 10;
const round2 = (value: number) => Math.round(value * 100) / 100;
const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));
const signed = (value: number) => `${value > 0 ? "+" : ""}${round1(value)}%`;
const clone = <T>(value: T): T => structuredClone(value);
const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function labelFor(score: number): string {
  if (score >= 75) return "Golden";
  if (score >= 55) return "Attractive";
  if (score >= 35) return "Monitor";
  return "Crowded";
}

function dedupe(values: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const raw of values) {
    const value = raw.trim();
    if (value && !seen.has(value.toLowerCase())) {
      seen.add(value.toLowerCase());
      result.push(value);
    }
  }
  return result;
}

// ---------------------------------------------------------------- store

let nextMarketId = 3;
let nextScanId = 41;
let nextReportId = 1;

const store = new Map<number, MarketState>([
  [1, {
    market: {
      id: 1,
      name: "Clean skincare serums",
      country: "us",
      category: "skincare serums",
      brand_name: "Glow Theory",
      keywords: ["vitamin c serum", "niacinamide serum", "hyaluronic acid serum", "retinol serum", "barrier repair serum"],
      competitors: ["Dermaflux", "Lumiere Lab", "Purely Basic", "Oak & Aloe", "Velvet Theory"],
    },
    scans: 5,
    seed: 11,
    reports: [],
    demoHistory: true,
  }],
  [2, {
    market: {
      id: 2,
      name: "Running shoes",
      country: "us",
      category: "running shoes",
      brand_name: null,
      keywords: ["running shoes", "trail running shoes", "carbon plate running shoes", "wide toe box running shoes"],
      competitors: ["Stridewell", "Apex Run", "Northpace", "Kinetica", "Trailborn"],
    },
    scans: 4,
    seed: 23,
    reports: [],
    demoHistory: true,
  }],
]);

// ---------------------------------------------------------------- dashboard generation

const EMPTY_DASHBOARD: Dashboard = {
  kpis: { observed_skus: 0, new_products: 0, demand_change_pct: null, top_opportunity: null },
  events: [],
  shelf_share: [],
  competitors: [],
  opportunities: [],
  has_demo_history: false,
  report: null,
  latest_scan_at: null,
  scan_count: 0,
};

const PRICE_CAVEAT = "Same product at the same retailer; the listed price may include a temporary promotion.";
const DISCOUNT_CAVEAT = "Based on the strike-through price the retailer shows on Google Shopping.";
const RANK_CAVEAT = "Google Shopping ordering varies between requests; small moves are often noise.";
const PRESENCE_CAVEAT = "A sampled search shelf: absence from earlier scans does not prove a launch date.";

const PRODUCT_WORDS = ["Daily", "Pro", "Advanced", "Ultra", "Essential", "Plus", "Max", "Lite"];

function buildDashboard(state: MarketState): Dashboard {
  const { market, scans } = state;
  if (scans === 0) return { ...clone(EMPTY_DASHBOARD), report: state.reports.at(-1) ?? null };

  const random = mulberry32(state.seed * 7919 + scans * 104729 + hashText(market.name));
  const pick = <T>(items: T[]): T => items[Math.floor(random() * items.length)];
  const now = Date.now();
  const basePrice = 12 + random() * 90;

  // Shelf share: position-weighted visibility, normalised to 100%.
  const brands = dedupe([...(market.brand_name ? [market.brand_name] : []), ...market.competitors]);
  const weights = brands.map((brand) => (brand === market.brand_name ? 0.6 + random() : 0.5 + random() * 2.5));
  const unbranded = 0.3 + random() * 0.8;
  const total = weights.reduce((sum, weight) => sum + weight, unbranded);
  const listingsFor = (weight: number) => Math.max(1, Math.round(weight * 6));
  // Same shape as the backend: one row per brand plus an aggregate "Unbranded" row, largest first.
  const shelf_share: ShelfShareEntry[] = [
    ...brands.map((brand, index) => ({
      brand,
      share_pct: round1((weights[index] / total) * 100),
      is_mine: brand === market.brand_name,
      listings: listingsFor(weights[index]),
    })),
    { brand: "Unbranded", share_pct: round1((unbranded / total) * 100), is_mine: false, listings: listingsFor(unbranded) },
  ].sort((a, b) => b.share_pct - a.share_pct);
  const top3 = round1(shelf_share.filter((row) => row.brand !== "Unbranded").slice(0, 3).reduce((sum, row) => sum + row.share_pct, 0));

  const tracked = new Set(market.competitors.map((brand) => brand.toLowerCase()));
  const competitors: CompetitorRow[] = shelf_share
    .filter((row) => row.brand !== "Unbranded")
    .map((row) => {
      const best = 1 + Math.floor(random() * 8);
      return {
        brand: row.brand,
        is_mine: row.is_mine,
        is_tracked: tracked.has(row.brand.toLowerCase()),
        listings: row.listings,
        share_pct: row.share_pct,
        best_position: best,
        avg_position: round1(best + random() * 12),
        median_price: round2(basePrice * (0.7 + random() * 0.6)),
        discount_rate_pct: round1(random() * 35),
        avg_rating: round2(3.8 + random() * 1.1),
        total_reviews: Math.floor(random() * 9000),
        new_products: Math.floor(random() * 5),
        price_moves: Math.floor(random() * 4),
      };
    });

  // Opportunities: demand 40 / room 25 / incumbent pressure 20 / price headroom 15 (approximated).
  const opportunities: Opportunity[] = market.keywords.map((keyword, index) => {
    const demand = index === 0 ? round1(22 + random() * 18) : round1(-12 + random() * 45);
    const supply = index === 0 ? round1(-8 + random() * 6) : round1(-10 + random() * 35);
    const score = Math.round(clamp(52 + demand * 1.05 - supply * 0.9 + (random() - 0.5) * 12, 6, 96));
    const headroom = round1(4 + random() * 18);
    const caveats = [
      "Google Trends values are relative within this keyword batch, not absolute search volume.",
      "Shelf share comes from a sampled Google Shopping shelf, not total market sales.",
    ];
    if (scans < 3) caveats.push(`Only ${scans} scan${scans === 1 ? "" : "s"} of history, so confidence is limited.`);
    if (Math.abs(supply) > 20) caveats.push("Supply moved sharply; part of the change may be shelf volatility between scans.");
    return {
      keyword,
      score,
      label: labelFor(score),
      demand_growth: demand,
      supply_growth: supply,
      reasons: [
        `Search interest is ${signed(demand)} over the last 4 weeks vs the prior 8 weeks.`,
        `Observed shelf supply changed ${signed(supply)} across recent scans.`,
        `The top 3 brands hold ${top3}% of visible shelf positions.`,
        `Median price leaves about ${headroom}% headroom below the top price quartile.`,
      ],
      caveats,
    };
  }).sort((a, b) => b.score - a.score);

  // Events need two scans of history (presence changes are shelf-volatile).
  const events: MarketEvent[] = [];
  if (scans >= 2) {
    const count = 5 + Math.floor(random() * 4);
    const types = ["NEW_PRODUCT", "PRICE_DECREASE", "DISCOUNT_STARTED", "RANK_GAIN", "PRICE_INCREASE", "RANK_LOSS", "NEW_PRODUCT", "NEW_COMPETITOR"];
    for (let index = 0; index < count; index += 1) {
      const type = types[index % types.length];
      const brand = type === "NEW_COMPETITOR" ? pick(["Brightleaf", "Kova Labs", "Solstice & Co"]) : pick(market.competitors);
      const product = type === "NEW_COMPETITOR" ? null : `${brand} ${pick(PRODUCT_WORDS)} ${pick(market.keywords)}`;
      const price = round2(basePrice * (0.7 + random() * 0.6));
      const keyword = pick(market.keywords);
      let previous_value: number | null = null;
      let current_value: number | null = null;
      let change_pct: number | null = null;
      let summary = "";
      let caveat = PRICE_CAVEAT;
      let confidence = "high";
      let evidence: Record<string, unknown> = {};
      // Values follow the backend: presence events carry a shelf position (new product) or a
      // listing count (new competitor) in current_value, never a price.
      if (type === "NEW_PRODUCT") {
        const position = 1 + Math.floor(random() * 20);
        current_value = position;
        summary = `New on the shelf: ${product} by ${brand}, best position ${position}.`;
        caveat = PRESENCE_CAVEAT;
        confidence = "medium";
        evidence = { listings: 1, keywords: [keyword], scans_checked: Math.min(scans - 1, 4), price };
      }
      if (type === "NEW_COMPETITOR") {
        const listings = 1 + Math.floor(random() * 3);
        current_value = listings;
        summary = `New brand on the shelf: ${brand} with ${listings} listing${listings === 1 ? "" : "s"}, best position ${3 + Math.floor(random() * 10)}.`;
        caveat = PRESENCE_CAVEAT;
        confidence = "medium";
        evidence = { listings, brand_inferred: false, scans_checked: Math.min(scans - 1, 4) };
      }
      if (type === "PRICE_DECREASE" || type === "PRICE_INCREASE") {
        change_pct = type === "PRICE_DECREASE" ? -round1(5 + random() * 25) : round1(5 + random() * 15);
        previous_value = price;
        current_value = round2(price * (1 + change_pct / 100));
        summary = `${product} at Shop A ${change_pct < 0 ? "dropped" : "rose"} ${Math.abs(change_pct)}% (${previous_value} to ${current_value}).`;
        evidence = { change_pct, offer_key: `${product}|shop a`, source: "Shop A" };
      }
      if (type === "DISCOUNT_STARTED") {
        const depth = round1(5 + random() * 25);
        change_pct = -depth;
        previous_value = price;
        current_value = round2(price * (1 - depth / 100));
        summary = `${product} at Shop A is now ${depth}% off (${previous_value} to ${current_value}).`;
        caveat = DISCOUNT_CAVEAT;
        evidence = { discount_pct: depth, offer_key: `${product}|shop a`, source: "Shop A" };
      }
      if (type === "RANK_GAIN" || type === "RANK_LOSS") {
        const from = 3 + Math.floor(random() * 12);
        const move = 3 + Math.floor(random() * 5);
        previous_value = from;
        current_value = type === "RANK_GAIN" ? Math.max(1, from - move) : from + move;
        summary = `${product} ${type === "RANK_GAIN" ? "climbed" : "fell"} from position ${from} to ${current_value} for "${keyword}".`;
        caveat = RANK_CAVEAT;
        confidence = "medium";
        evidence = { positions_moved: from - current_value };
      }
      const magnitude = Math.abs(change_pct ?? 0);
      const severity = type === "NEW_COMPETITOR" || magnitude >= 15 ? "high" : magnitude >= 8 || type.startsWith("RANK") ? "medium" : "low";
      events.push({
        id: index + 1,
        type,
        severity,
        confidence,
        brand,
        product,
        keyword,
        previous_value,
        current_value,
        change_pct,
        detected_at: new Date(now - (index * 19 + random() * 14) * 3600 * 1000).toISOString(),
        is_demo: state.demoHistory,
        summary,
        caveat,
        evidence: { ...evidence, is_mine: brand === market.brand_name, compared_with_demo: state.demoHistory },
      });
    }
  }

  const demandValues = opportunities.map((item) => item.demand_growth ?? 0);
  const top = opportunities[0];
  return {
    kpis: {
      observed_skus: market.keywords.length * 38 + Math.floor(random() * 12),
      new_products: events.filter((event) => event.type === "NEW_PRODUCT").length,
      demand_change_pct: demandValues.length ? round1(demandValues.reduce((sum, value) => sum + value, 0) / demandValues.length) : null,
      top_opportunity: top ? { keyword: top.keyword, score: top.score, label: top.label } : null,
    },
    events,
    shelf_share,
    competitors,
    opportunities,
    has_demo_history: state.demoHistory,
    report: state.reports.at(-1) ?? null,
    latest_scan_at: new Date(now).toISOString(),
    scan_count: scans,
  };
}

function buildReport(state: MarketState): Report {
  const dashboard = buildDashboard(state);
  const { kpis, events, opportunities } = dashboard;
  const lines = [`# Weekly brief: ${state.market.name}`, ""];
  if (kpis.observed_skus === 0) {
    lines.push("No scans yet, so there is nothing to report. Run a scan to collect the first shelf snapshot.");
  } else {
    lines.push("## What changed");
    if (events.length === 0) lines.push("- No changes detected yet. Changes need at least two scans.");
    for (const event of events.slice(0, 4)) {
      const change = event.change_pct === null ? "" : ` (${signed(event.change_pct)})`;
      lines.push(`- ${event.brand ?? "A brand"}: ${event.type.replace(/_/g, " ").toLowerCase()}${change}${event.product ? ` on ${event.product}` : ""}.`);
    }
    lines.push("", "## Where the opportunity is");
    for (const item of opportunities.slice(0, 2)) {
      lines.push(`- **${item.keyword}** scores ${item.score}/100 (${item.label}): demand ${signed(item.demand_growth ?? 0)}, supply ${signed(item.supply_growth ?? 0)}.`);
    }
    lines.push("", "## Watch-outs", `- ${opportunities[0]?.caveats[0] ?? "Data volume is still low."}`);
    lines.push(`- ${kpis.observed_skus} SKUs observed this week; ${kpis.new_products} ${kpis.new_products === 1 ? "was" : "were"} new.`);
  }
  return { id: nextReportId++, market_id: state.market.id, content: lines.join("\n"), created_at: new Date().toISOString(), source: "template" };
}

// Seed one report so the brief panel has content for the first market and an empty state for the second.
store.get(1)?.reports.push({ ...buildReport(store.get(1)!), created_at: new Date(Date.now() - 2 * 86400 * 1000).toISOString() });

// ---------------------------------------------------------------- suggestions

const FICTIONAL_BRANDS = ["Northwind", "Brightleaf", "Kova Labs", "Solstice & Co", "Everline", "Mosswood", "Halcyon", "Tidewater"];

function suggest(body: SuggestRequest): SuggestResponse {
  const category = body.category.trim().toLowerCase();
  if (!category) throw new ApiError("Category is required.", 422);
  if (category.includes("fail")) throw new ApiError("SerpAPI error: mock failure (type a different category to continue).", 502);
  const random = mulberry32(hashText(`${category}|${body.country}`));
  const keywords = dedupe([category, `best ${category}`, `${category} for women`, `${category} for men`, `cheap ${category}`, `${category} sale`]).slice(0, 6);
  const brands = FICTIONAL_BRANDS.map((name) => ({ name, count: 1 + Math.floor(random() * 14), inferred: random() < 0.3 }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 6);
  return { keywords, brands };
}

// ---------------------------------------------------------------- router

function parseBody<T>(options: RequestInit): T {
  try {
    return (typeof options.body === "string" ? JSON.parse(options.body) : {}) as T;
  } catch {
    throw new ApiError("Invalid JSON body.", 400);
  }
}

function owned(id: number): MarketState {
  const state = store.get(id);
  if (!state) throw new ApiError("Market not found", 404);
  return state;
}

function createMarket(body: MarketCreate): Market {
  const name = body.name?.trim();
  const category = body.category?.trim();
  const country = body.country?.trim().toLowerCase();
  const keywords = dedupe(body.keywords ?? []);
  if (!name || !category || !country) throw new ApiError("Name, category and country are required.", 422);
  if (keywords.length < 1 || keywords.length > 5) throw new ApiError("Provide between 1 and 5 keywords (one Google Trends batch).", 422);
  const brand = body.brand_name?.trim() || null;
  const market: Market = {
    id: nextMarketId,
    name,
    country,
    category,
    brand_name: brand,
    keywords,
    competitors: dedupe(body.competitors ?? []).filter((item) => item.toLowerCase() !== brand?.toLowerCase()),
  };
  store.set(market.id, { market, scans: 0, seed: nextMarketId * 31, reports: [], demoHistory: false });
  nextMarketId += 1;
  return market;
}

function scan(state: MarketState): ScanResult {
  state.scans += 1;
  const dashboard = buildDashboard(state);
  const keywordCount = state.market.keywords.length;
  return {
    market_id: state.market.id,
    scan_id: nextScanId++,
    scanned_at: new Date().toISOString(),
    keyword_count: keywordCount,
    products_observed: dashboard.kpis.observed_skus,
    products: dashboard.kpis.observed_skus,
    trend_points: keywordCount * 52,
    events: dashboard.events.length,
    opportunities: dashboard.opportunities.length,
    credits_used: 0,
    cached_calls: 0,
    estimated_credits: keywordCount + 1,
    shopping_calls: keywordCount,
    trends_calls: 1,
    dry_run: false,
    guard_active: false,
    minutes_since_last_scan: null,
    warnings: [],
    message: state.scans === 1
      ? "Mock scan complete (no SerpAPI credits used). Change events appear after a second scan."
      : "Mock scan complete (no SerpAPI credits used).",
  };
}

// ---------------------------------------------------------------- scan jobs, budget, alerts, replay (mock)

type MockJob = { id: number; market_id: number; created: number; result: ScanResult };
const mockJobs = new Map<number, MockJob>();
let nextJobId = 1;
const JOB_MS = 1400;
const JOB_STEPS = 4;

function jobView(job: MockJob) {
  const elapsed = Date.now() - job.created;
  const done = elapsed >= JOB_MS;
  return {
    id: job.id,
    market_id: job.market_id,
    status: done ? "succeeded" : "running",
    force: false,
    refresh: false,
    progress_done: done ? JOB_STEPS : Math.min(JOB_STEPS - 1, Math.floor(elapsed / (JOB_MS / JOB_STEPS))),
    progress_total: JOB_STEPS,
    message: done ? job.result.message : "Searching Google Shopping",
    error: null,
    error_code: null,
    result: done ? job.result : null,
    created_at: new Date(job.created).toISOString(),
    started_at: new Date(job.created).toISOString(),
    finished_at: done ? new Date(job.created + JOB_MS).toISOString() : null,
  };
}

export async function mockApi<T>(path: string, options: RequestInit = {}, token?: string): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase();
  const [pathname] = path.split("?");
  const isScan = pathname.endsWith("/scan");
  const isReport = pathname.endsWith("/reports/weekly");
  await delay(isScan ? 1400 : isReport ? 1000 : 250 + Math.random() * 350);

  if (method === "POST" && pathname === "/auth/login") {
    const body = parseBody<{ email?: string; password?: string }>(options);
    if (body.email?.toLowerCase() !== DEMO_EMAIL || body.password !== DEMO_PASSWORD) throw new ApiError("Incorrect email or password", 401);
    return clone<LoginResponse>({ access_token: MOCK_TOKEN, token_type: "bearer", user: DEMO_USER }) as T;
  }

  if (token !== MOCK_TOKEN) throw new ApiError("Could not validate credentials", 401);

  if (method === "GET" && pathname === "/auth/me") return clone(DEMO_USER) as T;
  if (method === "GET" && pathname === "/markets") return clone([...store.values()].map((state) => state.market)) as T;
  if (method === "POST" && pathname === "/markets") return clone(createMarket(parseBody<MarketCreate>(options))) as T;
  if (method === "POST" && pathname === "/markets/suggest") return clone(suggest(parseBody<SuggestRequest>(options))) as T;

  if (method === "GET" && pathname === "/alerts/summary") return clone({ unread: 0, by_market: {} }) as T;

  const match = /^\/markets\/(\d+)(\/.*)?$/.exec(pathname);
  if (match) {
    const state = owned(Number(match[1]));
    const rest = match[2] ?? "";
    if (method === "GET" && rest === "") return clone(state.market) as T;
    if (method === "GET" && rest === "/dashboard") return clone(buildDashboard(state)) as T;
    if (method === "GET" && rest === "/events") return clone({ items: [], total: 0, limit: 50, offset: 0, hidden_low_confidence: 0, include_low_confidence: false }) as T;
    if (method === "GET" && rest === "/observations") return clone({ has_data: false, scan: null, items: [], total: 0, limit: 50, offset: 0, keywords: state.market.keywords, brands: [] }) as T;
    if (method === "GET" && rest === "/opportunities") {
      const items = buildDashboard(state).opportunities;
      return clone({ has_data: items.length > 0, scan: null, trends_scan: null, items }) as T;
    }
    if (method === "GET" && rest === "/reports") return clone({ items: [...state.reports].reverse() }) as T;
    if (method === "GET" && ["/demand", "/shelf", "/supply", "/pricing"].includes(rest)) {
      return clone({ has_data: false, scan: null, keywords: [], overall: rest === "/pricing" ? null : [], by_probe: {}, stability: {}, scans_compared: 0, method: "", probes: [], brands: [], compared_with: null }) as T;
    }
    if (method === "POST" && rest === "/scan-jobs") {
      const running = [...mockJobs.values()].find((job) => job.market_id === state.market.id && Date.now() - job.created < JOB_MS);
      const job = running ?? { id: nextJobId++, market_id: state.market.id, created: Date.now(), result: scan(state) };
      mockJobs.set(job.id, job);
      return clone(jobView(job)) as T;
    }
    if (method === "GET" && rest === "/scan-jobs/latest") {
      const latest = [...mockJobs.values()].filter((job) => job.market_id === state.market.id).at(-1);
      return clone(latest ? jobView(latest) : null) as T;
    }
    if (method === "GET" && rest === "/alerts") return clone({ items: [], total: 0, unread: 0, limit: 20, offset: 0 }) as T;
    if (method === "GET" && rest === "/alert-rules") return clone([]) as T;
    if (method === "GET" && rest === "/recommendations") return clone({ has_data: false, scan: null, items: [], notes: [] }) as T;
    if (method === "GET" && rest === "/replay") return clone({ scans: [] }) as T;
    if (method === "POST" && rest === "/scan") return clone(scan(state)) as T;
    if (method === "POST" && rest === "/reports/weekly") {
      const report = buildReport(state);
      state.reports.push(report);
      return clone(report) as T;
    }
    if (method === "GET" && rest === "/reports/latest") {
      const latest = state.reports.at(-1);
      if (!latest) throw new ApiError("No report yet", 404);
      return clone(latest) as T;
    }
  }
  throw new ApiError("Not Found", 404);
}
