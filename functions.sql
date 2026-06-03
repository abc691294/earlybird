-- fn_eb_screen ported from the T-SQL inline TVF. Case-insensitive keyword match (ILIKE,
-- matching SQL Server's default collation). Biotech excluded by GICS Healthcare, not
-- free-text; a STRONG (tier 3) match overrides exclusion-by-mention.
CREATE OR REPLACE FUNCTION fn_eb_screen(p_sector TEXT DEFAULT NULL)
RETURNS TABLE (sector TEXT, yf_ticker TEXT, name TEXT, country TEXT, gics_sector TEXT,
  market_cap BIGINT, range_pct DOUBLE PRECISION, fwd_pe DOUBLE PRECISION,
  revenue_growth DOUBLE PRECISION, price DOUBLE PRECISION, fit_score INT, matched TEXT, fit TEXT)
LANGUAGE sql STABLE AS $$
WITH matches AS (
  SELECT u.yf_ticker, k.sector, k.keyword,
         CASE k.tier WHEN 'strong' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END AS tier_score
  FROM tbl_eb_universe u
  JOIN tbl_eb_fundamentals f ON f.yf_ticker=u.yf_ticker AND f.fetch_ok
  JOIN tbl_eb_sector_keywords k ON k.kind='include' AND k.active
    AND (f.summary ILIKE '%'||k.keyword||'%' OR f.industry ILIKE '%'||k.keyword||'%' OR u.name ILIKE '%'||k.keyword||'%')
  WHERE u.active AND (p_sector IS NULL OR k.sector=p_sector)
),
ranked AS (
  SELECT yf_ticker, sector, keyword, tier_score,
         ROW_NUMBER() OVER (PARTITION BY yf_ticker, sector ORDER BY tier_score DESC, LENGTH(keyword) DESC) rn
  FROM matches
),
excluded AS (
  SELECT u.yf_ticker FROM tbl_eb_universe u
  JOIN tbl_eb_fundamentals f ON f.yf_ticker=u.yf_ticker WHERE f.sector='Healthcare'
  UNION
  SELECT u.yf_ticker FROM tbl_eb_universe u
  JOIN tbl_eb_fundamentals f ON f.yf_ticker=u.yf_ticker
  JOIN tbl_eb_sector_keywords k ON k.kind='exclude' AND k.active
    AND (f.summary ILIKE '%'||k.keyword||'%' OR f.industry ILIKE '%'||k.keyword||'%')
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
  AND (r.tier_score=3 OR r.yf_ticker NOT IN (SELECT yf_ticker FROM excluded));
$$;
