import type { Opportunity } from "../types";
import { formatSignedPct } from "../lib/format";
import { visibleLines } from "../lib/text";
import { LABEL_COLOR, LABEL_HINT, normalizeLabel } from "../lib/opportunity";
import { ConfidenceBadge, HistoryBadge } from "./ui/Badge";
import { Drawer } from "./ui/Drawer";

/** What each stored component measures, and what 100 means (from the scoring method). */
export const COMPONENTS: Record<string, { label: string; meaning: string }> = {
  demand: { label: "Demand", meaning: "100 = search interest growing fast" },
  room: { label: "Shelf room", meaning: "100 = fragmented shelf, few new competing products" },
  incumbent_pressure: { label: "Weak incumbents", meaning: "100 = top listings have few reviews" },
  price_headroom: { label: "Price headroom", meaning: "100 = wide gap between median and upper-quartile price, little discounting" },
};

const FACTOR_TEXT: Record<string, (value: unknown) => string | null> = {
  data_volume: (v) => (v === "ok" ? "Enough listings and Trends weeks" : "Thin: few listings or Trends weeks"),
  scan_count: (v) => (typeof v === "number" ? `${v} scan${v === 1 ? "" : "s"} of history` : null),
  shelf_stability: (v) =>
    typeof v === "number" ? `${Math.round(v * 100)}% of products carried over between scans` : "Stability could not be measured yet",
};

/** Everything behind one score: the score itself, the separate confidence, its components, reasons and caveats. */
export function OpportunityDrawer({ opportunity, onClose }: { opportunity: Opportunity | null; onClose: () => void }) {
  const label = opportunity ? normalizeLabel(opportunity.label, opportunity.score) : null;
  const reasons = visibleLines(opportunity?.reasons ?? []);
  const caveats = visibleLines(opportunity?.caveats ?? []);
  const factors = (opportunity?.confidence?.factors ?? {}) as Record<string, unknown>;
  const scans = typeof factors.scan_count === "number" ? factors.scan_count : null;
  return (
    <Drawer
      open={opportunity !== null}
      title={opportunity?.keyword ?? "Opportunity"}
      subtitle={label ? LABEL_HINT[label] : undefined}
      onClose={onClose}
    >
      {opportunity && label && (
        <>
          <section className="evidence-block" aria-label="Score">
            <h4>Score</h4>
            <p className="big-number">
              {Math.round(opportunity.score)}
              <span className="muted-ink">/100</span>{" "}
              <span className="label-badge">
                <span className="label-dot" style={{ background: LABEL_COLOR[label] }} aria-hidden="true" />
                {label}
              </span>
            </p>
            <p className="data-note">
              Demand {formatSignedPct(opportunity.demand_growth)} · supply {formatSignedPct(opportunity.supply_growth)}
            </p>
          </section>

          <section className="evidence-block" aria-label="Confidence">
            <h4>Confidence (separate from the score)</h4>
            <div className="badge-row">
              <ConfidenceBadge level={String(opportunity.confidence?.level ?? "")} />
              {scans !== null && scans < 3 && <HistoryBadge scans={scans} />}
            </div>
            <ul className="bullet-list">
              {Object.entries(FACTOR_TEXT).map(([key, text]) => {
                const line = key in factors ? text(factors[key]) : null;
                return line ? <li key={key}>{line}</li> : null;
              })}
            </ul>
            <p className="data-note">Confidence says how much to trust the score. It never changes the score.</p>
          </section>

          {opportunity.components && Object.keys(opportunity.components).length > 0 && (
            <section className="evidence-block" aria-label="Score components">
              <h4>What makes up the score</h4>
              <ul className="component-list">
                {Object.entries(opportunity.components).map(([key, value]) => {
                  const meta = COMPONENTS[key] ?? { label: key.replace(/_/g, " "), meaning: "" };
                  return (
                    <li key={key}>
                      <div className="component-head">
                        <span>{meta.label}</span>
                        <strong>{Math.round(value)}</strong>
                      </div>
                      <span
                        className="score-meter"
                        role="meter"
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-valuenow={Math.round(value)}
                        aria-label={meta.label}
                      >
                        <span
                          className="score-fill"
                          style={{ width: `${Math.max(0, Math.min(100, value))}%`, background: "var(--viz-mine)" }}
                        />
                      </span>
                      {meta.meaning && <span className="data-note">{meta.meaning}</span>}
                    </li>
                  );
                })}
              </ul>
            </section>
          )}

          <section className="evidence-block" aria-label="Why">
            <h4>Why</h4>
            {reasons.length > 0 ? (
              <ul className="bullet-list">
                {reasons.map((reason, index) => (
                  <li key={index}>{reason}</li>
                ))}
              </ul>
            ) : (
              <p className="muted-ink">No reasons were recorded for this score.</p>
            )}
          </section>

          {caveats.length > 0 && (
            <section className="evidence-block" aria-label="Caveats">
              <h4>Caveats</h4>
              <ul className="bullet-list caveat-list">
                {caveats.map((caveat, index) => (
                  <li key={index}>{caveat}</li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </Drawer>
  );
}
