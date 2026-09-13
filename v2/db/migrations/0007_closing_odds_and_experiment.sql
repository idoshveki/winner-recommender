-- Correct the over/under columns. What migration 0003 stored as `avg_over25`
-- was football-data's "Avg>2.5" — the OPENING average, not the close. Stage 0
-- caught this: measured against openings our model looked mildly prescient,
-- because closing lines are far sharper. Renaming rather than adding makes the
-- mistake impossible to repeat silently.
alter table historical_odds rename column avg_over25  to avg_open_over25;
alter table historical_odds rename column avg_under25 to avg_open_under25;

alter table historical_odds add column pinnacle_close_over25  numeric(8,3);
alter table historical_odds add column pinnacle_close_under25 numeric(8,3);
alter table historical_odds add column avg_close_over25       numeric(8,3);
alter table historical_odds add column avg_close_under25      numeric(8,3);

-- Results of a pre-registered experiment run. Written once, never updated, so
-- a null result cannot quietly disappear when a later run looks better.
create table experiment_results (
    id             bigserial primary key,
    run_at         timestamptz not null default now(),
    git_sha        text,
    preregistered  boolean not null default true,
    market         text    not null,
    hypothesis     text    not null,
    n              integer not null,
    c              numeric(8,4),          -- incremental-information coefficient
    c_se           numeric(8,4),
    c_p            numeric(8,5),
    c_q            numeric(8,5),          -- FDR-adjusted
    brier_model    numeric(8,5),
    brier_market   numeric(8,5),
    roi_by_threshold jsonb,               -- {"0.00": ..., "0.02": ..., "0.05": ...}
    monotonic_pass boolean,               -- the gate that actually matters
    verdict        text not null,
    notes          text
);
