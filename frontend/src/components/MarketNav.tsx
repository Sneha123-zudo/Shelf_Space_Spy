import { NavLink } from "react-router-dom";

const SECTIONS = [
  { path: "", label: "Overview", end: true },
  { path: "changes", label: "Changes" },
  { path: "scanner", label: "Scanner" },
  { path: "demand", label: "Demand" },
  { path: "shelf", label: "Shelf" },
  { path: "opportunities", label: "Opportunities" },
  { path: "brief", label: "Weekly brief" },
  { path: "alerts", label: "Alerts" },
  { path: "replay", label: "Replay" },
];

/** Section tabs for one market; links stay under /markets/:id so switching markets keeps the section. */
export function MarketNav({ id, unreadAlerts = 0 }: { id: number; unreadAlerts?: number }) {
  return (
    <nav className="market-nav" aria-label="Market sections">
      {SECTIONS.map((section) => (
        <NavLink
          key={section.label}
          to={`/markets/${id}${section.path ? `/${section.path}` : ""}`}
          end={section.end}
          className={({ isActive }) => (isActive ? "market-tab active" : "market-tab")}
        >
          {section.label}
          {section.label === "Alerts" && unreadAlerts > 0 && <span className="nav-count">{unreadAlerts > 99 ? "99+" : unreadAlerts}</span>}
        </NavLink>
      ))}
    </nav>
  );
}
