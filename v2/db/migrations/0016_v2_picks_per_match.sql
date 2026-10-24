-- v2 runs daily; its picks were keyed as if it ran weekly.
--
-- Every v2 cards pick used the same slot, leg='leg2', under a unique key of
-- (system, week, leg). So the first pick recorded in a week occupied it and
-- every pick found afterwards was discarded by ON CONFLICT DO NOTHING. On
-- 2026-10-23 the picker found 70 candidates, 13 of them clearing the 5% edge
-- gate for fixtures playing that weekend, and stored none - while the job
-- printed "recorded 0 v2 picks ... nothing to add" and exited green.
--
-- The two systems have genuinely different shapes and now get keys to match:
--
--   v1 issues one slip a week with named legs (leg1/leg2/leg3/draw), so
--      (system, week, leg) is exactly right and is kept for it.
--   v2 issues picks per FIXTURE as it finds them, so it is keyed by match.
--      One pick per fixture: re-running the same day is still a clean no-op,
--      but a fixture found on a later day can now be recorded.
--
-- A weekly cap lives in the picker rather than here, because it is a betting
-- decision rather than a data-integrity rule.
-- The constraint owns the index, so the constraint goes first; dropping the
-- index directly fails with DependentObjectsStillExist.
alter table v1_live_record
    drop constraint if exists live_record_system_week_leg_key;

drop index if exists live_record_system_week_leg_key;

create unique index if not exists live_record_v1_week_leg_key
    on v1_live_record (system, week, leg)
    where system <> 'v2';

-- match_id is NOT NULL for every v2 row the picker writes; it comes straight
-- from the fixture it priced.
create unique index if not exists live_record_v2_match_key
    on v1_live_record (system, match_id)
    where system = 'v2';
