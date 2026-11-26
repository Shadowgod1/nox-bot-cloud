/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_MOCK?: string
  readonly VITE_MOCK_SETUP?: string
  readonly VITE_MOCK_BRAND?: string
  readonly VITE_MOCK_WA?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
