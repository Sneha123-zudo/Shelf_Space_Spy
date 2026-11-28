import type { MarketEvent } from "../types";
import { EVENT_TYPES, groupByDay } from "../lib/events";
import { formatPrice, humanize } from "../lib/format";
import { ConfidenceBadge, SeverityBadge } from "./ui/Badge";

const dayFormat = new Intl.DateTimeFormat("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric" });
const timeFormat = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" });

function dayLabel(key: string): string {
  const [year, month, day] = key.split("-").map(Number);
  return dayFormat.format(new Date(year, month - 1, day));
}

/** Events grouped by day; each row opens the evidence drawer. */
export function EventTimeline({
  events,
  country,
  onSelect,
}: {
  events: MarketEvent[];
  country: string;
  onSelect: (event: MarketEvent) => void;
}) {
  return (
    <div className="timeline">
      {groupByDay(events).map(({ day, events: items }) => (
        <section key={day} className="timeline-day" aria-label={dayLabel(day)}>
          <h4 className="timeline-heading">
            {dayLabel(day)} <span className="muted-ink">· {items.length}</span>
          </h4>
          <ul className="timeline-list">
            {items.map((event) => {
              const meta = EVENT_TYPES[event.type] ?? { label: humanize(event.type), kind: "none" as const };
              const change = meta.kind === "price" && event.current_value !== null ? formatPrice(event.current_value, country) : null;
              return (
                <li key={event.id}>
                  <button
                    type="button"
                    className="timeline-item"
                    onClick={() => onSelect(event)}
                    aria-label={`${meta.label}: ${event.summary}. Open evidence`}
                  >
                    <span className="timeline-time">{timeFormat.format(new Date(event.detected_at))}</span>
                    <span className="timeline-main">
                      <span className="badge-row">
                        <strong className="timeline-type">{meta.label}</strong>
                        <SeverityBadge level={event.severity} />
                        <ConfidenceBadge level={event.confidence} />
                      </span>
                      <span className="timeline-summary">{event.summary}</span>
                      {(event.keyword || change) && (
                        <span className="timeline-meta">
                          {event.keyword && <>Probe: {event.keyword}</>}
                          {event.keyword && change && " · "}
                          {change && <>Now {change}</>}
                        </span>
                      )}
                    </span>
                    <span className="timeline-chevron" aria-hidden="true">
                      ›
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}
