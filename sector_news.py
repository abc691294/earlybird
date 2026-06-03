"""
sector_news.py - Tier 1 "rotation / next thing" feed: free per-SECTOR industry RSS.
Fetches each feed, stores articles in tbl_eb_sector_news (deduped). Free, no API.
"""
import time, datetime as dt
import feedparser
from eb_db import get_conn, dbex

FEEDS = {
 "Nuclear/power":        ["https://www.world-nuclear-news.org/rss"],
 "Semiconductors":       ["https://www.eetimes.com/feed/"],
 "Space":                ["https://spacenews.com/feed/"],
 "Defence":              ["https://breakingdefense.com/feed/"],
 "Rare earths/materials":["https://www.mining.com/feed/"],
 "Photonics/optical":    ["https://www.optica-opn.org/home/rssfeed/?rss=News"],
 "Energy storage":       ["https://www.energy-storage.news/feed/"],
 "Quantum":              ["https://thequantuminsider.com/feed/"],
 "Robotics/autonomy":    ["https://www.therobotreport.com/feed/"],
}

DDL = """
IF OBJECT_ID('tbl_eb_sector_news','U') IS NULL
CREATE TABLE tbl_eb_sector_news (
  id INT IDENTITY PRIMARY KEY, sector NVARCHAR(60) NOT NULL, source NVARCHAR(120) NULL,
  title NVARCHAR(400) NULL, link NVARCHAR(600) NULL, published DATETIME2 NULL,
  summary NVARCHAR(MAX) NULL, guid VARCHAR(300) NOT NULL,
  fetched_on DATETIME2 NOT NULL DEFAULT now(),
  CONSTRAINT UQ_eb_secnews UNIQUE (sector, guid));
"""
MERGE = """
INSERT INTO tbl_eb_sector_news (sector,source,title,link,published,summary,guid)
  VALUES (%s,%s,%s,%s,%s,%s,%s)
  ON CONFLICT (sector,guid) DO NOTHING
"""


def pub(e):
    p = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
    return dt.datetime(*p[:6]) if p else None


def main():
    conn = get_conn(); cur = conn.cursor()
    total = 0
    for sector, urls in FEEDS.items():
        got = 0; src = ""
        for url in urls:
            try:
                f = feedparser.parse(url)
                src = (f.feed.get("title") if f.feed else None) or url.split("/")[2]
                for e in f.entries:
                    guid = (getattr(e, "id", None) or getattr(e, "link", "") or "")[:300]
                    if not guid: continue
                    dbex(cur, MERGE, sector, src[:120], (getattr(e,"title","") or "")[:400],
                                (getattr(e,"link","") or "")[:600], pub(e),
                                (getattr(e,"summary","") or "")[:4000], guid)
                    got += 1
            except Exception as ex:
                print(f"  {sector:24} FEED ERROR {str(ex)[:50]}")
        conn.commit(); total += got
        print(f"  {sector:24} {got:3} articles  ({src[:40]})")
    dbex(cur, "SELECT COUNT(*) n FROM tbl_eb_sector_news")
    print(f"\ntbl_eb_sector_news: {cur.fetchone().n} rows total")
    conn.close()


if __name__ == "__main__":
    main()
