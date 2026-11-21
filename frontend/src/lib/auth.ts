"use client";

import { create } from "zustand";

import {
  apiGet,
  apiPost,
  registerAuthFailureHandler,
  registerRefreshHandler,
  setAccessToken,
} from "@/lib/api";

export type Role = "analyst" | "credit_officer" | "admin";

export interface CurrentUser {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  org_id: string;
}

interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

const STORAGE_KEY = "ic.auth.tokens";

interface StoredTokens {
  access: string;
  refresh: string;
}

function readStored(): StoredTokens | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as StoredTokens) : null;
  } catch {
    return null;
  }
}

function writeStored(tokens: StoredTokens | null): void {
  if (typeof window === "undefined") return;
  if (tokens) window.localStorage.setItem(STORAGE_KEY, JSON.stringify(tokens));
  else window.localStorage.removeItem(STORAGE_KEY);
}

export type AuthStatus = "loading" | "authenticated" | "anonymous";

interface AuthState {
  status: AuthStatus;
  user: CurrentUser | null;
  refreshToken: string | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  hydrate: () => Promise<void>;
}

export const useAuth = create<AuthState>()((set, get) => ({
  status: "loading",
  user: null,
  refreshToken: null,

  login: async (email, password) => {
    const tokens = await apiPost<TokenPair>(
      "/api/v1/auth/login",
      { email, password },
      { anonymous: true },
    );
    setAccessToken(tokens.access_token);
    writeStored({ access: tokens.access_token, refresh: tokens.refresh_token });
    const user = await apiGet<CurrentUser>("/api/v1/users/me");
    set({ status: "authenticated", user, refreshToken: tokens.refresh_token });
  },

  logout: () => {
    setAccessToken(null);
    writeStored(null);
    set({ status: "anonymous", user: null, refreshToken: null });
  },

  hydrate: async () => {
    const stored = readStored();
    if (!stored) {
      set({ status: "anonymous" });
      return;
    }
    setAccessToken(stored.access);
    set({ refreshToken: stored.refresh });
    try {
      const user = await apiGet<CurrentUser>("/api/v1/users/me");
      set({ status: "authenticated", user });
    } catch {
      get().logout();
    }
  },
}));

// Wire the API client's refresh + auth-failure hooks to this store exactly once.
registerRefreshHandler(async () => {
  const refresh = useAuth.getState().refreshToken;
  if (!refresh) return null;
  try {
    const tokens = await apiPost<TokenPair>(
      "/api/v1/auth/refresh",
      { refresh_token: refresh },
      { anonymous: true },
    );
    setAccessToken(tokens.access_token);
    writeStored({ access: tokens.access_token, refresh: tokens.refresh_token });
    useAuth.setState({ refreshToken: tokens.refresh_token });
    return tokens.access_token;
  } catch {
    return null;
  }
});

registerAuthFailureHandler(() => {
  useAuth.getState().logout();
});

/** Permission checks mirrored from the backend ROLE_PERMISSIONS (read-only UX gating). */
export function canStartRun(role: Role | undefined): boolean {
  return role === "analyst" || role === "admin";
}
export function canReview(role: Role | undefined): boolean {
  return role === "credit_officer" || role === "admin";
}
export function canWrite(role: Role | undefined): boolean {
  return role === "analyst" || role === "admin";
}
