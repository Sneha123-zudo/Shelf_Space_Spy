/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Set to "1" to serve every API call from src/api/mock.ts (no backend needed). */
  readonly VITE_USE_MOCK?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
