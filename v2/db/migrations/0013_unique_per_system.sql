-- The unique key must include `system`.
--
-- It was UNIQUE (week, leg), which was correct while only v1 wrote to this
-- table. The moment v2 started recording, its picks collided with v1's legs for
-- the same week and `on conflict do nothing` discarded them in silence - the
-- job even reported success, because it counted attempts rather than rows.
-- Same class of bug as v1's ingest printing "0 rows updated" and exiting 0.
alter table v1_live_record drop constraint v1_live_record_week_leg_key;
alter table v1_live_record add constraint live_record_system_week_leg_key
    unique (system, week, leg);
