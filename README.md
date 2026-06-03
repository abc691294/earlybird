# EarlyBird - cloud

Cloud-hosted slice of the EarlyBird research engine. This repo runs the **Trump scan**
(holdings + posts + policy-beneficiary detection + Wave grading + email alerts) on a
**24/7 hourly schedule** via GitHub Actions, against a **Supabase Postgres** database.

It exists in the cloud because the highest-value Trump catalysts break overnight and at
weekends - when a laptop-bound scheduler would be off. GitHub Actions runs regardless.

## What runs
- **`trump_news.py`** - hourly. Scrapes Google News + White House actions + Truth Social,
  maps mentions to tickers, detects policy->beneficiary crosses, grades each name on the
  weekly+daily Wave (computed from price data, no TradingView dependency), and emails an
  alert on first-seen BUY-tier mentions of established names (3-day per-ticker cooldown).

## Architecture
- **Data:** Supabase (Postgres 17). Schema in `schema.sql`. RLS deny-all on every table;
  jobs connect via the Session-pooler role which bypasses RLS. The public Data API is sealed.
- **Compute:** GitHub Actions cron (`.github/workflows/trump-hourly.yml`), Linux runners.
- **No LLM, no T212 key** - this pipeline is yfinance + feedparser + pandas only.

## Secrets (repository secrets, set in Settings -> Secrets -> Actions)
| Secret | Value |
|---|---|
| `SUPABASE_HOST` / `SUPABASE_PORT` / `SUPABASE_DB` / `SUPABASE_USER` / `SUPABASE_PASSWORD` | Supabase Session-pooler connection fields |
| `GMAIL_USER` / `GMAIL_APP_PASSWORD` | Gmail SMTP for alert emails |
| `GMAIL_RECIPIENT` *(optional)* | where alerts go; defaults to `GMAIL_USER` if unset |

## Local development
Put a `supabase` block (host/port/dbname/user/password) plus `gmail_user`/`gmail_app_password`
in a gitignored `secrets.json`, then `python trump_news.py`. `eb_db.py` reads env vars first
(cloud), falling back to `secrets.json` (local), so the same code runs in both.

## Not yet here (next migration phase)
The daily engine (universe/pool/enrichment/news/move-detector/digest) still runs locally
against SQL Server. Porting those scripts to this Postgres stack is the next phase.
