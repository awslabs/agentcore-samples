// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * Theme selection.
 *
 * Applied as `data-theme` on <html>, which styles.css keys its token overrides
 * off. Persisted to localStorage (not sessionStorage) because a display
 * preference should survive closing the tab, unlike the auth session.
 *
 * With no stored choice the OS preference wins, so the console matches the rest
 * of the operator's desktop on first load.
 */

export type Theme = "dark" | "light"

const STORAGE_KEY = "meridian.theme"

function systemTheme(): Theme {
  return window.matchMedia?.("(prefers-color-scheme: light)").matches
    ? "light"
    : "dark"
}

export function storedTheme(): Theme | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    return value === "dark" || value === "light" ? value : null
  } catch {
    // localStorage throws in private-mode Safari; fall back to the OS setting.
    return null
  }
}

export function initialTheme(): Theme {
  return storedTheme() ?? systemTheme()
}

/** Write the theme to the document root. */
export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute("data-theme", theme)
}

export function persistTheme(theme: Theme): void {
  try {
    localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    /* Non-fatal: the theme still applies for this page load. */
  }
}

/**
 * Watch the OS preference and follow it — but only while the user has not made
 * an explicit choice, so a manual selection is never overridden.
 */
export function watchSystemTheme(onChange: (theme: Theme) => void): () => void {
  const query = window.matchMedia?.("(prefers-color-scheme: light)")
  if (!query) return () => {}

  const handler = (event: MediaQueryListEvent) => {
    if (storedTheme() === null) onChange(event.matches ? "light" : "dark")
  }
  query.addEventListener("change", handler)
  return () => query.removeEventListener("change", handler)
}
