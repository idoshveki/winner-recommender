-- v1's live recommendations, graded on v2's data.
--
-- v1 records picks but has settled nothing since March: its results feeder
-- reads gitignored CSVs absent in CI, so matches_history froze and
-- update_pick_results.py has nothing to match against. Ten weeks sat with
-- hit=NULL. This table is the settled record, so a good run cannot be lost
-- to a broken settler - and cannot be inflated later either.
create table v1_live_record (
    id           bigserial primary key,
    week         text not null,
    leg          text not null,          -- leg1 / leg2 / draw
    market       text,
    match_text   text not null,
    pick         text,
    odds_quoted  numeric(8,3),           -- what v1 stated
    odds_real    numeric(8,3),           -- observed where known (v1 assumed 1.50 for YC)
    hit          boolean not null,
    home_goals   smallint,
    away_goals   smallint,
    settled_at   timestamptz not null default now(),
    note         text,
    unique (week, leg)
);
