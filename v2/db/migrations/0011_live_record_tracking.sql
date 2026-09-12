-- Make the live record self-maintaining rather than hand-settled.
--
-- Until now every leg was inserted by hand after someone noticed a result. That
-- is how two loss weeks went missing from the first load - both excluded by
-- filters that happened to only drop losses. A pick should be recorded when it
-- is ISSUED and settled later by a scheduled job, so nothing depends on anyone
-- remembering.
--
-- Written with IF NOT EXISTS throughout: migration 0010 already created
-- settled_at, and a migration that half-applies is worse than one that is
-- simply safe to re-run.

-- Which system produced the pick. v1 still emails every Friday; v2 sends
-- nothing today, but if it ever does its picks belong in the same table,
-- clearly separated, so the two can never be silently pooled.
alter table v1_live_record
    add column if not exists system text not null default 'v1';
do $$ begin
    alter table v1_live_record add constraint live_record_system_chk
        check (system in ('v1','v2'));
exception when duplicate_object then null; end $$;

-- hit must be nullable: a pick recorded at issue time has no result yet.
-- NULL = pending, true/false = settled.
alter table v1_live_record alter column hit drop not null;

-- settled_at came from 0010 as NOT NULL DEFAULT now(), which is wrong once
-- pending rows exist - an unsettled leg has no settlement time.
alter table v1_live_record alter column settled_at drop not null;
alter table v1_live_record alter column settled_at drop default;

alter table v1_live_record add column if not exists kickoff_utc timestamptz;
alter table v1_live_record add column if not exists match_id    bigint references matches(id);
alter table v1_live_record add column if not exists issued_at   timestamptz;

create index if not exists live_record_pending_idx on v1_live_record (hit) where hit is null;
create index if not exists live_record_system_idx  on v1_live_record (system, week);

-- One row per week per system, with the slip resolved.
-- A slip counts only when EVERY non-draw leg is settled - a week with a leg
-- still pending is unresolved, not a win. Scoring a lone surviving leg as a
-- one-leg winner would inflate the record, which is exactly the failure this
-- table exists to prevent.
create or replace view live_record_weeks as
select
    system,
    week,
    count(*) filter (where leg <> 'draw')                 as slip_legs,
    count(*) filter (where leg <> 'draw' and hit is null) as slip_pending,
    bool_and(hit) filter (where leg <> 'draw')            as slip_won,
    exp(sum(ln(coalesce(odds_real, odds_quoted)))
        filter (where leg <> 'draw'))                     as slip_odds,
    max(odds_quoted) filter (where leg = 'draw')          as draw_odds,
    bool_or(hit)     filter (where leg = 'draw')          as draw_hit
from v1_live_record
group by system, week;
