// Layout and motion constants used from TypeScript; colours live in theme.css.

export const motionTokens = {
  reveal: { duration: 0.4, ease: [0.22, 1, 0.36, 1] as const },
  stagger: 0.06,
  swap: { duration: 0.2, ease: "easeOut" as const },
  marker: { type: "spring" as const, stiffness: 140, damping: 20 },
} as const;

export const laneTokens = {
  // the strip's byte axis, log scale
  minBytes: 1024,
  maxBytes: 200 * 1024 ** 3,
  height: 92,
  ticks: [1024, 1024 ** 2, 64 * 1024 ** 2, 1024 ** 3, 20 * 1024 ** 3],
} as const;

export const queryTokens = {
  dryRunDebounceMs: 350,
  previewRows: 200,
} as const;

export const STORAGE_USER_KEY = "apr.user";
