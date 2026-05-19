/**
 * TypeScript mirror of `tokens.css` for places that need values in JS
 * (chart libs, inline styles, JS-driven theming).
 *
 * Keep this in sync with `tokens.css` by hand — there are few enough
 * tokens that a generator isn't worth the build-step weight.
 */

export const tokens = {
  color: {
    bg: "var(--hermes-bg)",
    bgElevated: "var(--hermes-bg-elevated)",
    bgInput: "var(--hermes-bg-input)",
    border: "var(--hermes-border)",
    borderStrong: "var(--hermes-border-strong)",
    fg: "var(--hermes-fg)",
    fgMuted: "var(--hermes-fg-muted)",
    fgSubtle: "var(--hermes-fg-subtle)",
    accent: "var(--hermes-accent)",
    accentFg: "var(--hermes-accent-fg)",
    danger: "var(--hermes-danger)",
    success: "var(--hermes-success)",
    warning: "var(--hermes-warning)",
  },
  font: {
    sans: "var(--hermes-font-sans)",
    mono: "var(--hermes-font-mono)",
    sizeBase: "var(--hermes-font-size-base)",
    lineHeight: "var(--hermes-line-height)",
  },
  space: {
    s1: "var(--hermes-space-1)",
    s2: "var(--hermes-space-2)",
    s3: "var(--hermes-space-3)",
    s4: "var(--hermes-space-4)",
    s6: "var(--hermes-space-6)",
    s8: "var(--hermes-space-8)",
  },
  radius: {
    sm: "var(--hermes-radius-sm)",
    md: "var(--hermes-radius-md)",
    lg: "var(--hermes-radius-lg)",
    full: "var(--hermes-radius-full)",
  },
  shadow: {
    sm: "var(--hermes-shadow-sm)",
    md: "var(--hermes-shadow-md)",
    xl: "var(--hermes-shadow-xl)",
  },
} as const;

export type DesignTokens = typeof tokens;
