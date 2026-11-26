import { useState } from "react";
import { Outlet, useMatch } from "react-router-dom";

import { errorMessage } from "../api/errors";
import { useScanController } from "../hooks/useScanController";
import { parseMarketId } from "../lib/ids";
import { shouldAnnounce } from "../lib/scanJob";
import { ScanProgress } from "./dashboard/ScanProgress";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

/** Sidebar + top bar around every signed-in page. Scan state lives here so progress follows you between pages. */
export function AppShell() {
  const match = useMatch("/markets/:id/*");
  const marketId = parseMarketId(match?.params.id);
  const scan = useScanController(marketId);
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <Sidebar marketId={marketId} open={menuOpen} onNavigate={() => setMenuOpen(false)} />
      {menuOpen && (
        <button type="button" className="sidebar-scrim" aria-label="Close menu" tabIndex={-1} onClick={() => setMenuOpen(false)} />
      )}
      <div className="app-body">
        <TopBar marketId={marketId} scan={scan} menuOpen={menuOpen} onMenu={() => setMenuOpen((open) => !open)} />
        <main id="main" className="app-main">
          {scan.startError != null && (
            <p className="notice error" role="alert">
              <strong>Couldn't start the scan.</strong> {errorMessage(scan.startError)}
              <button type="button" className="text-button" onClick={scan.clearStartError}>
                Dismiss
              </button>
            </p>
          )}
          {scan.job && shouldAnnounce(scan.job, scan.dismissedJob) && <ScanProgress job={scan.job} onDismiss={scan.dismiss} />}
          <Outlet />
        </main>
      </div>
    </div>
  );
}
