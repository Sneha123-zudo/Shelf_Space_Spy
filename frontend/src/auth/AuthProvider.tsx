import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { api, type AuthedApi } from "../api/client";
import { ApiError } from "../api/errors";
import type { User } from "../types";

// JWT lives in sessionStorage (per tab, cleared on close), never in cookies.
const TOKEN_KEY = "shelf-space-spy-token";
const USER_KEY = "shelf-space-spy-user";

type AuthContextValue = {
  token: string | null;
  user: User | null;
  /** Message shown on the login page after a forced logout (e.g. expired session). */
  notice: string;
  login: (token: string, user: User) => void;
  logout: (notice?: string) => void;
  /** api() with the bearer token bound; a 401 logs the user out. */
  request: AuthedApi;
};

const AuthContext = createContext<AuthContextValue | null>(null);

function readSession(): { token: string | null; user: User | null } {
  try {
    const token = sessionStorage.getItem(TOKEN_KEY);
    const saved = sessionStorage.getItem(USER_KEY);
    const user = saved ? (JSON.parse(saved) as User) : null;
    return token && user ? { token, user } : { token: null, user: null };
  } catch {
    return { token: null, user: null };
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [session, setSession] = useState(readSession);
  const [notice, setNotice] = useState("");

  const login = useCallback((token: string, user: User) => {
    try {
      sessionStorage.setItem(TOKEN_KEY, token);
      sessionStorage.setItem(USER_KEY, JSON.stringify(user));
    } catch {
      // Storage unavailable (private mode): the session still works for this page load.
    }
    setNotice("");
    setSession({ token, user });
  }, []);

  const logout = useCallback(
    (nextNotice = "") => {
      try {
        sessionStorage.clear();
      } catch {
        // ignore
      }
      queryClient.clear();
      setNotice(nextNotice);
      setSession({ token: null, user: null });
    },
    [queryClient],
  );

  const token = session.token;
  const request = useCallback<AuthedApi>(
    async <T,>(path: string, options?: RequestInit): Promise<T> => {
      try {
        return await api<T>(path, options, token ?? undefined);
      } catch (error) {
        if (error instanceof ApiError && error.status === 401 && token) logout("Your session expired. Please sign in again.");
        throw error;
      }
    },
    [token, logout],
  );

  const value = useMemo<AuthContextValue>(
    () => ({ token, user: session.user, notice, login, logout, request }),
    [token, session.user, notice, login, logout, request],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside <AuthProvider>");
  return context;
}
