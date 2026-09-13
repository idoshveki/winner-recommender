-- Retrospective v2 picks: what v2 WOULD have chosen for weeks v1 actually bet.
--
-- These are backtest. They live in `research`, which PostgREST does not expose,
-- so the app cannot render them as a live record even by accident. v1 put 27
-- backtest weeks into its live picks table and its UI reported them as a 67%
-- track record; that is the failure this schema separation exists to prevent.
--
-- They are computed with information available before each match, but priced at
-- CLOSING odds, which were not knowable at v1's Friday pick time. That makes
-- them a fair benchmark, not a replayable strategy.
create table research.v2_backfill (
    id          bigserial primary key,
    week        text not null,
    market      text not null,
    match_text  text not null,
    pick        text not null,
    match_id    bigint,
    kickoff_utc timestamptz,
    price       numeric(8,3) not null,
    model_prob  numeric(6,5),
    book_prob   numeric(6,5),
    edge        numeric(7,4),
    qualifies   boolean not null,
    hit         boolean,
    computed_at timestamptz not null default now(),
    unique (week, market)
);
