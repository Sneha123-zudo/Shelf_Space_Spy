import { lazy, Suspense, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { ApiError } from "./api/errors";
import { AuthProvider } from "./auth/AuthProvider";
import { AppShell } from "./components/AppShell";
import MarketLayout from "./pages/MarketLayout";
import { RequireAuth } from "./components/RequireAuth";
import { Loading } from "./components/states";
import LoginPage from "./pages/LoginPage";
import LandingPage from "./pages/LandingPage";
import NotFoundPage from "./pages/NotFoundPage";

// The dashboard pulls in recharts; load it only when a market is opened.
const DashboardPage = lazy(() => import("./pages/DashboardPage"));
const ChangesPage = lazy(() => import("./pages/sections/ChangesPage"));
const ScannerPage = lazy(() => import("./pages/sections/ScannerPage"));
const DemandPage = lazy(() => import("./pages/sections/DemandPage"));
const ShelfPage = lazy(() => import("./pages/sections/ShelfPage"));
const OpportunitiesPage = lazy(() => import("./pages/sections/OpportunitiesPage"));
const BriefPage = lazy(() => import("./pages/sections/BriefPage"));
const AlertsPage = lazy(() => import("./pages/sections/AlertsPage"));
const ReplayPage = lazy(() => import("./pages/sections/ReplayPage"));
const withFallback = (page: ReactNode) => <Suspense fallback={<Loading label="Loading…" rows={6} />}>{page}</Suspense>;

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      // Retry once for network/5xx errors; 4xx answers (auth, not found, validation) won't change.
      retry: (failureCount, error) => !(error instanceof ApiError && error.status >= 400 && error.status < 500) && failureCount < 1,
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route
              path="/"
              element={
                <RequireAuth>
                  <LandingPage />
                </RequireAuth>
              }
            />
            <Route path="/markets" element={<Navigate to="/" replace />} />
            <Route path="/markets/new" element={<Navigate to="/#add" replace />} />
            <Route
              element={
                <RequireAuth>
                  <AppShell />
                </RequireAuth>
              }
            >
              <Route path="/markets/:id" element={<MarketLayout />}>
                <Route index element={withFallback(<DashboardPage />)} />
                <Route path="changes" element={withFallback(<ChangesPage />)} />
                <Route path="scanner" element={withFallback(<ScannerPage />)} />
                <Route path="demand" element={withFallback(<DemandPage />)} />
                <Route path="shelf" element={withFallback(<ShelfPage />)} />
                <Route path="opportunities" element={withFallback(<OpportunitiesPage />)} />
                <Route path="brief" element={withFallback(<BriefPage />)} />
                <Route path="alerts" element={withFallback(<AlertsPage />)} />
                <Route path="replay" element={withFallback(<ReplayPage />)} />
              </Route>
              <Route path="*" element={<NotFoundPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}
