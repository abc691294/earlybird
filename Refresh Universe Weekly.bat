@echo off
REM Weekly universe refresh: pull the current T212 instrument catalogue, then rebuild
REM tbl_eb_universe (Supabase) from it. New listings (e.g. fresh IPOs) appear after this.
REM Registered as a weekly Windows scheduled task. Logs to refresh_universe.log.

set LOG="%~dp0refresh_universe.log"
echo ============================================================ >> %LOG%
echo Universe refresh started %DATE% %TIME% >> %LOG%

REM 1. refresh the T212 instrument cache (catalogue fetch, read-only, no trading)
cd /d "C:\Users\sbrow\OneDrive\Claude\projects\T212 Quant Pie"
python refresh_instruments.py >> %LOG% 2>&1
if errorlevel 1 (
  echo CACHE REFRESH FAILED - aborting, universe not rebuilt >> %LOG%
  exit /b 1
)

REM 2. rebuild the Supabase universe from the fresh cache (needs secrets.json present)
REM earlybird_repo now lives inside Stock Research, so secrets.json is one level up.
cd /d "C:\Users\sbrow\OneDrive\Claude\projects\Stock Research\earlybird_repo"
copy /Y "..\secrets.json" "secrets.json" >nul
python build_universe.py >> %LOG% 2>&1
set RC=%errorlevel%

REM 3. full-universe fundamentals refresh (batched yahooquery - ~15 min for the whole universe).
REM Keeps enriched non-watchlist names from going stale; also fills the new names just added.
python maintain.py --all >> %LOG% 2>&1
set RCE=%errorlevel%

REM 4. rebuild the pool off the fresh fundamentals
python pool.py >> %LOG% 2>&1

del /Q "secrets.json" >nul 2>&1

echo Universe refresh finished %DATE% %TIME% (build rc=%RC% enrich rc=%RCE%) >> %LOG%
exit /b %RC%
