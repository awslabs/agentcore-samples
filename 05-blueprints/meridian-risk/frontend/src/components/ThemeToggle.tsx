// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Dark/light switch. Shown on both the login screen and the app masthead. */

import type { Theme } from "../lib/theme"

interface Props {
  theme: Theme
  onChange: (theme: Theme) => void
}

export function ThemeToggle({ theme, onChange }: Props) {
  const next: Theme = theme === "dark" ? "light" : "dark"

  return (
    <button
      className="theme-toggle"
      onClick={() => onChange(next)}
      // The control is icon-only, so it needs an accessible name; `pressed`
      // conveys which state is active to screen readers.
      aria-label={`Switch to ${next} theme`}
      aria-pressed={theme === "light"}
      title={`Switch to ${next} theme`}
      type="button"
    >
      <span className="theme-toggle-track" aria-hidden="true">
        <span className="theme-toggle-knob" />
      </span>
      <span className="theme-toggle-label mono" aria-hidden="true">
        {theme === "dark" ? "DARK" : "LIGHT"}
      </span>
    </button>
  )
}
