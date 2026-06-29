import React, { ReactNode } from 'react';

/**
 * Minimal line icons for the Key Features grid — stroke-based, inherit
 * currentColor, no emoji. Deliberately quiet so the Scoreboard stays the
 * page's one bold element (frontend-design skill: spend the boldness budget once).
 */

const base = {
  width: 24,
  height: 24,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.75,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
};

export const StandardizedIcon: ReactNode = (
  <svg {...base} aria-hidden="true">
    <rect x="6" y="4" width="12" height="17" rx="2" />
    <path d="M9 4V3a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v1" />
    <path d="m9 13 2 2 4-4" />
  </svg>
);

export const AgentIcon: ReactNode = (
  <svg {...base} aria-hidden="true">
    <rect x="7" y="7" width="10" height="10" rx="1.5" />
    <rect x="10" y="10" width="4" height="4" />
    <path d="M10 2v3M14 2v3M10 19v3M14 19v3M2 10h3M2 14h3M19 10h3M19 14h3" />
  </svg>
);

export const SandboxIcon: ReactNode = (
  <svg {...base} aria-hidden="true">
    <path d="M12 2 4 6.5v9L12 20l8-4.5v-9L12 2Z" />
    <path d="m4 6.5 8 4.5 8-4.5" />
    <path d="M12 11v9" />
  </svg>
);

export const BalanceIcon: ReactNode = (
  <svg {...base} aria-hidden="true">
    <path d="M12 3v18M7 21h10M5 7h14" />
    <path d="m5 7-3 6a3 3 0 0 0 6 0L5 7Z" />
    <path d="m19 7-3 6a3 3 0 0 0 6 0l-3-6Z" />
  </svg>
);

export const TargetIcon: ReactNode = (
  <svg {...base} aria-hidden="true">
    <circle cx="12" cy="12" r="8" />
    <circle cx="12" cy="12" r="4" />
    <circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" />
  </svg>
);

export const TraceIcon: ReactNode = (
  <svg {...base} aria-hidden="true">
    <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z" />
    <path d="M14 3v5h5" />
    <path d="M8 13h8M8 17h6" />
  </svg>
);

export const DashboardIcon: ReactNode = (
  <svg {...base} aria-hidden="true">
    <rect x="3" y="3" width="8" height="9" rx="1" />
    <rect x="13" y="3" width="8" height="5" rx="1" />
    <rect x="13" y="10" width="8" height="11" rx="1" />
    <rect x="3" y="14" width="8" height="7" rx="1" />
  </svg>
);

export const ConcurrencyIcon: ReactNode = (
  <svg {...base} aria-hidden="true">
    <path d="m12 3 9 5-9 5-9-5 9-5Z" />
    <path d="m3 13 9 5 9-5" />
  </svg>
);
