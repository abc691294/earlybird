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
cd /d "C:\Users\sbrow\OneDrive\Claude\projects\earlybird_repo"
copy /Y "..\Stock Research\secrets.json" "secrets.json" >nul
python build_universe.py >> %LOG% 2>&1
set RC=%errorlevel%
del /Q "secrets.json" >nul 2>&1

echo Universe refresh finished %DATE% %TIME% (build rc=%RC%) >> %LOG%
exit /b %RC%
