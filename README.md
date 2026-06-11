# EarlyBird - cloud

The EarlyBird research engine, running entirely in the cloud: **Supabase Postgres** for
data + **GitHub Actions** cron for compute. No local machine required - it runs 24/7,
which matters most for the hourly stock-pumps scan (catalysts break overnight/at weekends).

## Schedules
| Workflow | Cron | Does |
|---|---|---|
| **Stock Pumps Hourly Scan** | `0 * * * *` | `stock_pumps.py` - Trump (holdings, Truth Social, White House, policy beneficiaries) PLUS Huang, hyperscaler CEOs, Altman, Su - all graded + email alerts |
| **Daily Refresh** | `0 22 * * *` | pool refresh → sector RSS → ticker news → move detector → stock-pumps scan → digest |
| **Weekly Refresh** | `0 6 * * 0` | enrich fundamentals (~1hr) → 13F holdings |

## Architecture
- **Data:** Supabase (Postgres 17). `schema.sql` (tables, RLS deny-all) + `functions.sql`
  (`fn_eb_screen`). Jobs connect via the Session-pooler role, which bypasses RLS; the
  public Data API is sealed.
- **Compute:** GitHub Actions, Linux runners. Public repo = unlimited free minutes, and
  isolated from any private app-build quota.
- **No LLM, no T212 key** - yfinance + feedparser + pandas + psycopg only.
- `eb_db.py` reads env vars (cloud Secrets) first, falling back to a gitignored
  `secrets.json` (local), so identical code runs in both. `dbex()` wraps psycopg execute
  to accept positional params.

## Secrets (repository secrets)
`SUPABASE_HOST/PORT/DB/USER/PASSWORD`, `GMAIL_USER`, `GMAIL_APP_PASSWORD`,
`GMAIL_RECIPIENT` (optional - defaults to `GMAIL_USER`).

## Scripts
`stock_pumps` · `pool` (+`functions.sql`) · `sector_news` · `news_scrape` · `move_scan` ·
`suggest` · `enrich_fundamentals` · `sa_13f`. Each ported from the original SQL Server
build to Postgres (MERGE→ON CONFLICT, `dbex`, dialect) and verified against Supabase.

## Local development
Put `supabase` (host/port/dbname/user/password) + `gmail_user`/`gmail_app_password` in a
gitignored `secrets.json`, then run any script directly.

## Notes
- `build_universe.py` (T212 instrument universe) stays local - it needs the T212 source;
  the universe is otherwise static in Supabase.
- The daily digest prints to the workflow log; wire it to email (like the stock-pumps alert)
  if you want it delivered.
