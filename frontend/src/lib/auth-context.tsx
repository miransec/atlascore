"use client";

/**
 * AuthContext — holds the authenticated user in React state.
 *
 * The access token lives in api.ts (_accessToken module variable).
 * This context only carries the decoded user profile so components can
 * read name / org / role without re-fetching.
 *
 * On mount (or after a page refresh) the provider calls /auth/me which
 * triggers a transparent token refresh via the HttpOnly refresh cookie if
 * the access token has been lost (as it always is after a hard reload).
 */

import {
  createContext,
  useContext,
  useEffect,
  useState,
  ReactNode,
  useCallback,
} from "react";
import { auth, MeResponse, ApiError, clearAccessToken } from "@/lib/api";

interface AuthState {
  user: MeResponse | null;
  /** true while the initial /auth/me probe is in flight */
  loading: boolean;
  /** call after successful login to hydrate user without another /me round-trip */
  setUser: (user: MeResponse | null) => void;
  /** clears user + access token; does NOT call the logout endpoint */
  signOut: () => void;
  /** re-runs /auth/me (e.g. after org switch) */
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUserState] = useState<MeResponse | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const me = await auth.me();
      setUserState(me);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setUserState(null);
        clearAccessToken();
      }
    }
  }, []);

  useEffect(() => {
    refresh().finally(() => setLoading(false));
  }, [refresh]);

  function setUser(u: MeResponse | null) {
    setUserState(u);
  }

  function signOut() {
    setUserState(null);
    clearAccessToken();
  }

  return (
    <AuthContext.Provider value={{ user, loading, setUser, signOut, refresh }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
