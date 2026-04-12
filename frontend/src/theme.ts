// Single dark theme. Light variants can land later — Hermeece's
// review queue UI looks great in dark mode and the operator typically
// stares at it from a NUC sitting next to the qBit dashboard.
export const theme = {
  bg: "#0e0f13",
  bg2: "#161821",
  bg3: "#1f2230",
  bg4: "#2a2e3e",
  border: "#2e3242",
  borderL: "#1c1f2c",
  text: "#f0f0f4",
  text2: "#d6d8df",
  textDim: "#8a8e9b",
  accent: "#6fa8ff",
  accentDim: "#4f7fcc",
  ok: "#4ec995",
  warn: "#e8c14a",
  err: "#ef6464",
};

export type Theme = typeof theme;
