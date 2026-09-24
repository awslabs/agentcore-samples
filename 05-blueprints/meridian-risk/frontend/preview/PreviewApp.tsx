// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * Palette preview harness.
 *
 * Renders the real App with a fixed switcher bar, so each candidate palette is
 * judged on the actual UI — real panels, real verdict colours, real density —
 * rather than on isolated swatches.
 */

import { useEffect, useState } from "react"
import App from "../src/App"

type Palette = "shipped" | "slate" | "ledger"
type Mode = "light" | "dark"

const PALETTES: {
  id: Palette
  label: string
  blurb: string
  /* ground, panel, border, accent — the four values that carry the character. */
  swatches: string[]
}[] = [
  {
    id: "shipped",
    label: "Shipped · Stone & Copper",
    blurb: "warm stone ground, ivory panels, copper accent — the live theme",
    swatches: ["#e9e4d8", "#f7f4ec", "#cabfa6", "#a3611a"],
  },
  {
    id: "slate",
    label: "Alt · Slate & Brass",
    blurb: "cool blueprint grey, brass accent — crisper, more corporate",
    swatches: ["#dde3e9", "#f1f5f8", "#b6c2ce", "#8f6018"],
  },
  {
    id: "ledger",
    label: "Alt · Ledger & Oxblood",
    blurb: "aged accounting cream, oxblood verdicts, antique gold",
    swatches: ["#e7e3d3", "#f6f3e8", "#c7bfa4", "#8a6a14"],
  },
]

export default function PreviewApp() {
  const [palette, setPalette] = useState<Palette>("shipped")
  const [mode, setMode] = useState<Mode>("light")

  // Drive both attributes on <html>; palettes.css keys off the pair.
  useEffect(() => {
    const root = document.documentElement
    root.setAttribute("data-theme", mode)
    if (mode === "light" && palette !== "shipped") {
      root.setAttribute("data-palette", palette)
    } else {
      // No data-palette means the real light theme from styles.css applies.
      root.removeAttribute("data-palette")
    }
  }, [palette, mode])

  const active = PALETTES.find((entry) => entry.id === palette)!

  return (
    <>
      <App />

      <div className="pv-bar">
        <strong>Palette</strong>
        <div className="pv-group">
          {PALETTES.map((entry) => (
            <button
              key={entry.id}
              className="pv-btn"
              aria-pressed={palette === entry.id}
              onClick={() => {
                setPalette(entry.id)
                // Palettes only apply to light mode, so switch there
                // automatically rather than appearing to do nothing.
                setMode("light")
              }}
            >
              {entry.label}
            </button>
          ))}
        </div>

        <div className="pv-swatches" aria-hidden="true">
          {active.swatches.map((colour) => (
            <span
              key={colour}
              className="pv-swatch"
              style={{ background: colour }}
              title={colour}
            />
          ))}
        </div>

        <strong>Mode</strong>
        <div className="pv-group">
          {(["light", "dark"] as Mode[]).map((entry) => (
            <button
              key={entry}
              className="pv-btn"
              aria-pressed={mode === entry}
              onClick={() => setMode(entry)}
            >
              {entry.toUpperCase()}
            </button>
          ))}
        </div>

        <span className="pv-note">
          {mode === "dark"
            ? "dark theme — unchanged by palette choice"
            : active.blurb}
        </span>
      </div>
    </>
  )
}
