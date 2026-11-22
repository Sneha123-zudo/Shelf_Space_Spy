import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { applyThemePreference, readThemePreference } from "./lib/theme";
import "./styles.css";

// Apply a saved light/dark choice before the first paint.
applyThemePreference(readThemePreference());

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
