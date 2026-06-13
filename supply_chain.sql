-- Supply-chain link map: who feeds whom, per theme. The picks-and-shovels layer the
-- method cares about most. Populated automatically as chains are mapped; read-only facts.
CREATE TABLE IF NOT EXISTS tbl_eb_supply_link (
  id            SERIAL PRIMARY KEY,
  theme         VARCHAR(40)  NOT NULL,          -- AI, Quantum, Space, Defence...
  downstream    VARCHAR(60)  NOT NULL,          -- who is fed (a leader ticker, or a theme/leader name)
  upstream      VARCHAR(20)  NOT NULL,          -- the supplier ticker (the pick-and-shovel)
  upstream_name VARCHAR(120),
  layer         SMALLINT     NOT NULL DEFAULT 1,-- 1 = direct supplier, 2 = supplier-of-supplier
  role          VARCHAR(80),                    -- what they supply (etch tools, HBM, InP substrate)
  listed        BOOLEAN      DEFAULT true,       -- buyable? false = private, info only
  note          VARCHAR(300),
  source        VARCHAR(40)  DEFAULT 'auto',
  added_on      TIMESTAMPTZ  DEFAULT now(),
  CONSTRAINT uq_supply UNIQUE (theme, downstream, upstream, role)
);
