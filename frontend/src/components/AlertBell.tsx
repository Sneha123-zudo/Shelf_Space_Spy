import { Link, useMatch } from "react-router-dom";

import { useAlertsSummary } from "../api/hooks";

/** Header bell: unread alerts across your markets. Opens the current market's inbox, or the busiest one. */
export function AlertBell() {
  const summary = useAlertsSummary();
  const match = useMatch("/markets/:id/*");
  const current = match?.params.id && /^\d+$/.test(match.params.id) ? match.params.id : null;
  const unread = summary.data?.unread ?? 0;
  const busiest = Object.entries(summary.data?.by_market ?? {}).sort((a, b) => b[1] - a[1])[0]?.[0];
  const target = current ?? busiest;
  const label = unread > 0 ? `Alerts, ${unread} unread` : "Alerts";
  if (!target) return null;
  return (
    <Link to={`/markets/${target}/alerts`} className="alert-bell" aria-label={label} title={label}>
      <span aria-hidden="true">🔔</span>
      {unread > 0 && <span className="alert-count">{unread > 99 ? "99+" : unread}</span>}
    </Link>
  );
}
