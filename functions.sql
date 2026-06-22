-- fn_eb_excluded - THE single exclusion list, honoured by the screen, the pumps scanner, and
-- the validator. One source of truth: tbl_eb_sector_keywords WHERE kind='exclude'. The match_on
-- column says HOW each rule matches, so one table covers text, sector, industry and name rules:
--   text     -> summary OR industry ILIKE (tobacco, crypto, cannabis) - SOFT
--   sector   -> GICS sector exact         (Healthcare = biotech, the structural fact) - HARD
--   industry -> GICS industry ILIKE       (drug manufacturer, pharmaceutical) - HARD
--   name     -> company name ILIKE        (tesla, spacex - Musk mandate) - HARD
-- Add a row here and every consumer honours it. No more drift between hardcoded lists.
--
-- p_hard_only=false -> any exclude rule matches (the full list).
-- p_hard_only=true  -> only the structural mandate rules (sector/industry/name), which block
-- EVEN a strong keyword match. 'text' rules are soft exclusion-by-mention that a tier-3 match
-- can override (e.g. a real chipmaker whose summary happens to say 'bitcoin'); the mandate
-- (biotech, Musk) is never overridable.
CREATE OR REPLACE FUNCTION fn_eb_excluded(p_ticker TEXT, p_hard_only BOOLEAN DEFAULT false)
RETURNS BOOLEAN LANGUAGE sql STABLE AS $$
  SELECT EXISTS (
    SELECT 1
    FROM tbl_eb_universe u
    JOIN tbl_eb_fundamentals f ON f.yf_ticker = u.yf_ticker
    JOIN tbl_eb_sector_keywords k ON k.kind='exclude' AND k.active
    WHERE u.yf_ticker = p_ticker
      AND (NOT p_hard_only OR k.match_on IN ('sector','industry','name'))
      AND (
        (k.match_on='text'     AND (f.summary ILIKE '%'||k.keyword||'%' OR f.industry ILIKE '%'||k.keyword||'%'))
        OR (k.match_on='sector'   AND lower(f.sector) = lower(k.keyword))
        OR (k.match_on='industry' AND f.industry ILIKE '%'||k.keyword||'%')
        OR (k.match_on='name'     AND u.name ILIKE '%'||k.keyword||'%')
      )
  );
$$;


-- fn_eb_screen ported from the T-SQL inline TVF. Case-insensitive keyword match (ILIKE,
-- matching SQL Server's default collation). Exclusions come from fn_eb_excluded (the single
-- list); a STRONG (tier 3) match still overrides exclusion-by-mention.
CREATE OR REPLACE FUNCTION fn_eb_screen(p_sector TEXT DEFAULT NULL)
RETURNS TABLE (sector TEXT, yf_ticker TEXT, name TEXT, country TEXT, gics_sector TEXT,
  market_cap BIGINT, range_pct DOUBLE PRECISION, fwd_pe DOUBLE PRECISION,
  revenue_growth DOUBLE PRECISION, price DOUBLE PRECISION, fit_score INT, matched TEXT, fit TEXT)
LANGUAGE sql STABLE AS $$
WITH matches AS (
  -- (a) the company's own description (summary / industry / name) - the primary signal
  SELECT u.yf_ticker, k.sector, k.keyword,
         CASE k.tier WHEN 'strong' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END AS tier_score
  FROM tbl_eb_universe u
  JOIN tbl_eb_fundamentals f ON f.yf_ticker=u.yf_ticker AND f.fetch_ok
  JOIN tbl_eb_sector_keywords k ON k.kind='include' AND k.active
    AND (f.summary ILIKE '%'||k.keyword||'%' OR f.industry ILIKE '%'||k.keyword||'%' OR u.name ILIKE '%'||k.keyword||'%')
  WHERE u.active AND (p_sector IS NULL OR k.sector=p_sector)
  UNION
  -- (b) recent catalyst-news headlines (last 30 days) - catches a pivot the stale Yahoo summary
  -- misses (e.g. RFIL moving into AI datacentre). Capped at tier 2 (medium): a headline is a
  -- softer signal than the company's own description, so news alone never scores 'strong'.
  SELECT u.yf_ticker, k.sector, k.keyword,
         LEAST(CASE k.tier WHEN 'strong' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END, 2) AS tier_score
  FROM tbl_eb_universe u
  JOIN tbl_eb_fundamentals f ON f.yf_ticker=u.yf_ticker AND f.fetch_ok
  JOIN tbl_eb_news n ON n.yf_ticker=u.yf_ticker AND n.catalyst=true
                    AND n.published >= now() - interval '30 days'
  JOIN tbl_eb_sector_keywords k ON k.kind='include' AND k.active
    AND n.title ILIKE '%'||k.keyword||'%'
  WHERE u.active AND (p_sector IS NULL OR k.sector=p_sector)
),
ranked AS (
  SELECT yf_ticker, sector, keyword, tier_score,
         ROW_NUMBER() OVER (PARTITION BY yf_ticker, sector ORDER BY tier_score DESC, LENGTH(keyword) DESC) rn
  FROM matches
)
SELECT r.sector::text, r.yf_ticker::text, u.name::text, u.country::text, f.sector::text,
       f.market_cap::bigint, f.range_pct::double precision, f.fwd_pe::double precision,
       f.revenue_growth::double precision, f.price::double precision,
       r.tier_score::int, r.keyword::text,
       (CASE r.tier_score WHEN 3 THEN 'strong' ELSE 'medium' END)::text
FROM ranked r
JOIN tbl_eb_universe u ON u.yf_ticker=r.yf_ticker
JOIN tbl_eb_fundamentals f ON f.yf_ticker=r.yf_ticker
WHERE r.rn=1 AND r.tier_score>=2
  AND NOT fn_eb_excluded(r.yf_ticker, true)                 -- hard mandate (biotech/Musk) always blocks
  AND (r.tier_score=3 OR NOT fn_eb_excluded(r.yf_ticker));  -- soft (mention) blocks unless strong match
$$;
