-- Criticality model for the supply-chain map: what they supply, how critical, how exclusive,
-- whether substitutable, and whether supply is constrained RIGHT NOW (the timing edge -
-- e.g. HBM memory sold out through 2027). 'chokepoint' and 'hot_chokepoint' are DERIVED in
-- supply.py from these inputs so they are never stale.
ALTER TABLE tbl_eb_supply_link ADD COLUMN IF NOT EXISTS supply_type   VARCHAR(16);   -- raw/component/equipment/service/ip
ALTER TABLE tbl_eb_supply_link ADD COLUMN IF NOT EXISTS criticality   VARCHAR(10);   -- essential/high/med/low
ALTER TABLE tbl_eb_supply_link ADD COLUMN IF NOT EXISTS exclusivity   VARCHAR(8);    -- sole/few/many
ALTER TABLE tbl_eb_supply_link ADD COLUMN IF NOT EXISTS competitors   VARCHAR(200);  -- alternatives (if not sole)
ALTER TABLE tbl_eb_supply_link ADD COLUMN IF NOT EXISTS substitutable BOOLEAN;        -- could downstream use something else entirely
ALTER TABLE tbl_eb_supply_link ADD COLUMN IF NOT EXISTS constrained_now BOOLEAN DEFAULT false;  -- supply sold out / backlogged NOW
ALTER TABLE tbl_eb_supply_link ADD COLUMN IF NOT EXISTS constraint_note VARCHAR(200); -- e.g. 'HBM sold out through 2027'
