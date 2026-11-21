"use client";

import { create } from "zustand";

/**
 * Client-only UI state (Zustand v5).
 *
 * Deliberately tiny seam: TanStack Query owns ALL server state (ADR-0014);
 * this store holds only residual UI state that must survive across routes.
 * Phase 5 extends it with panel layout and document-viewer preferences when
 * the case workspace ships - server data never lands here.
 */
export interface UiState {
  /** Whether the (future) app-shell sidebar is expanded. */
  sidebarOpen: boolean;
  /** Flip the sidebar open/closed. */
  toggleSidebar: () => void;
}

export const useUiStore = create<UiState>()((set) => ({
  sidebarOpen: true,
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
}));
