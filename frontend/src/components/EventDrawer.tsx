import type { MarketEvent } from "../types";
import { EVENT_TYPES, evidenceEntries } from "../lib/events";
import { formatDateTime, formatPrice, formatSignedPct, humanize } from "../lib/format";
import { ConfidenceBadge, SeverityBadge, UnstableBadge } from "./ui/Badge";
import { Drawer } from "./ui/Drawer";

function value(v: number | null, kind: string, country: string): string | null {
  if (typeof v !== "number" || !Number.isFinite(v) || kind === "none") return null;
  return kind === "price" ? formatPrice(v, country) : `#${Math.round(v)}`;
}

/** Everything the detector recorded about one change, including why its confidence is what it is. */
export function EventDrawer({ event, country, onClose }: { event: MarketEvent | null; country: string; onClose: () => void }) {
  const meta = event ? (EVENT_TYPES[event.type] ?? { label: humanize(event.type), kind: "none" as const }) : null;
  const evidence = event?.evidence ?? {};
  const stability = typeof evidence.probe_stability === "number" ? evidence.probe_stability : null;
  const lowered = typeof evidence.confidence_lowered_from === "string" ? evidence.confidence_lowered_from : null;
  const note = typeof evidence.confidence_note === "string" ? evidence.confidence_note : null;
  const rows = event
    ? evidenceEntries(evidence).filter(([key]) => !["Probe stability", "Confidence lowered from", "Confidence note"].includes(key))
    : [];
  const from = event && meta ? value(event.previous_value, meta.kind, country) : null;
  const to = event && meta ? value(event.current_value, meta.kind, country) : null;

  return (
    <Drawer
      open={event !== null}
      title={meta?.label ?? "Change"}
      subtitle={event ? formatDateTime(event.detected_at) : undefined}
      onClose={onClose}
    >
      {event && (
        <>
          <div className="badge-row">
            <SeverityBadge level={event.severity} />
            <ConfidenceBadge level={event.confidence} />
            {stability !== null && stability < 0.3 && <UnstableBadge overlap={stability} />}
          </div>
          <p className="drawer-summary">{event.summary}</p>

          <h4>What changed</h4>
          <dl className="kv">
            {event.brand && (
              <>
                <dt>Brand</dt>
                <dd>{event.brand}</dd>
              </>
            )}
            {event.product && (
              <>
                <dt>Product</dt>
                <dd>{event.product}</dd>
              </>
            )}
            {event.keyword && (
              <>
                <dt>Probe</dt>
                <dd>{event.keyword}</dd>
              </>
            )}
            {(from || to) && (
              <>
                <dt>{meta?.kind === "price" ? "Price" : "Position"}</dt>
                <dd>
                  {from ?? "—"} → {to ?? "—"}
                  {event.change_pct !== null && ` (${formatSignedPct(event.change_pct)})`}
                </dd>
              </>
            )}
          </dl>

          <h4>Why this confidence</h4>
          <p className="drawer-text">
            {lowered ? `Lowered from ${lowered}. ` : ""}
            {note ??
              (event.confidence.toLowerCase() === "high"
                ? "The data supports this change well."
                : "The detector had limited history or stability for this change.")}
            {stability !== null && ` Shelf overlap between scans for this probe: ${Math.round(stability * 100)}%.`}
          </p>
          {event.caveat && <p className="drawer-text muted-ink">{event.caveat}</p>}

          {rows.length > 0 && (
            <>
              <h4>Evidence</h4>
              <dl className="kv">
                {rows.map(([key, text]) => (
                  <div key={key} className="kv-row">
                    <dt>{key}</dt>
                    <dd>{text}</dd>
                  </div>
                ))}
              </dl>
            </>
          )}
        </>
      )}
    </Drawer>
  );
}
