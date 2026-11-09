# Web terminal (Vite + React + TypeScript)

A read-only terminal-style front end. It reads the prepared results straight from Supabase with the public
"anon" key (what the key may read is fixed by `database/supabase_rls.sql`) and starts live runs through the
Streamlit worker (`apps/dashboard`), then polls the `screening_runs` table for the result.

```
cp .env.example .env          # fill in VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY, VITE_STREAMLIT_WORKER_URL
npm install
npm run dev                   # http://localhost:5173
npm run build                 # type-check and build into dist/
```

Deploy on Vercel: root directory `apps/web`, framework "Vite", the three `VITE_*` variables.
Without `VITE_STREAMLIT_WORKER_URL` the terminal shows the prepared data only (the RUN button does nothing live).

What to know:

| Item | Detail |
|---|---|
| Company list and metric catalog | hard-coded in `src/lib/supabase.ts`. A new company in `config/companies.yaml` must be added there as well. |
| Waiting for a worker | the page polls every 2.5 s and gives up after 4 minutes (a sleeping Streamlit app needs about 30 to 45 s to wake) |
| Security | the anon key is public by design. It must never be a service key. Row-level security makes the browser read-only. |
