"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  apiRequest,
  getStoredSession,
  jsonBody,
  publicApiRequest,
  setStoredSession,
} from "@/lib/api";
import type { AuthTokens, User, UserRole } from "@/lib/types";

type AuthContextValue = {
  ready: boolean;
  user: User | null;
  login: (email: string, password: string) => Promise<User>;
  register: (
    email: string,
    password: string,
    role: UserRole,
  ) => Promise<User>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<User | null>(null);

  const syncCurrentUser = useCallback(async () => {
    const current = getStoredSession();
    if (!current) {
      setUser(null);
      setReady(true);
      return null;
    }
    try {
      const nextUser = await apiRequest<User>("/auth/me");
      const refreshed = getStoredSession();
      if (refreshed) {
        setStoredSession({ ...refreshed, user: nextUser });
      }
      setUser(nextUser);
      return nextUser;
    } catch {
      setStoredSession(null);
      setUser(null);
      return null;
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => {
    // Initial authentication is synchronized from browser storage on mount.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void syncCurrentUser();
  }, [syncCurrentUser]);

  const login = useCallback(async (email: string, password: string) => {
    const tokens = await publicApiRequest<AuthTokens>("/auth/login", {
      method: "POST",
      ...jsonBody({ email, password }),
    });
    setStoredSession({ tokens });
    try {
      const nextUser = await apiRequest<User>("/auth/me");
      setStoredSession({ tokens: getStoredSession()?.tokens ?? tokens, user: nextUser });
      setUser(nextUser);
      return nextUser;
    } catch (error) {
      setStoredSession(null);
      throw error;
    }
  }, []);

  const register = useCallback(
    async (email: string, password: string, role: UserRole) => {
      await publicApiRequest<User>("/auth/register", {
        method: "POST",
        ...jsonBody({ email, password, role }),
      });
      return login(email, password);
    },
    [login],
  );

  const logout = useCallback(async () => {
    const refreshToken = getStoredSession()?.tokens.refresh_token;
    try {
      if (refreshToken) {
        await apiRequest<{ revoked: boolean }>("/auth/logout", {
          method: "POST",
          ...jsonBody({ refresh_token: refreshToken }),
        });
      }
    } finally {
      setStoredSession(null);
      setUser(null);
    }
  }, []);

  const value = useMemo(
    () => ({ ready, user, login, register, logout }),
    [ready, user, login, register, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
