/// <reference types="vite/client" />

// The three settings the app reads from apps/web/.env (or the host's environment variables).
interface ImportMetaEnv {
  readonly VITE_SUPABASE_URL: string;
  readonly VITE_SUPABASE_ANON_KEY: string;
  readonly VITE_STREAMLIT_WORKER_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
