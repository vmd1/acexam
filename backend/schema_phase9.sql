-- Phase 9 Postgres Schema

-- UK GCSE specifications merge Higher and Foundation content into one
-- document and flag which sections are Higher-tier-only (e.g. "HT only") --
-- spec_topics didn't capture this, so a Foundation student could be served
-- content their tier never examines. NULL means the topic is common to all
-- tiers of the qualification; a value (e.g. 'Higher') restricts it to that
-- tier only, matching a name in qualifications.tiers.
ALTER TABLE spec_topics ADD COLUMN IF NOT EXISTS tier_only TEXT;
