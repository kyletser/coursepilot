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
  ApiError,
  apiRequest,
  jsonBody,
  publicApiRequest,
} from "@/lib/api";
import type { User, UserRole } from "@/lib/types";

const AUTH_REJECTED = (error: unknown): boolean =>
  error instanceof ApiError && error.status === 401;

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
    try {
      const nextUser = await apiRequest<User>("/auth/me");
      setUser(nextUser);
      return nextUser;
    } catch (error) {
      if (AUTH_REJECTED(error)) {
        setUser(null);
      } else {
        setUser(null);
      }
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
    await publicApiRequest<{ authenticated: boolean }>("/auth/login", {
      method: "POST",
      ...jsonBody({ email, password }),
    });
    try {
      const nextUser = await apiRequest<User>("/auth/me");
      setUser(nextUser);
      return nextUser;
    } catch (error) {
      if (AUTH_REJECTED(error)) {
        setUser(null);
      }
      setUser(null);
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
    try {
      await apiRequest<{ revoked: boolean }>("/auth/logout", {
        method: "POST",
        ...jsonBody({}),
      });
    } finally {
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
