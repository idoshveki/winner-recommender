-- v2 schema — cards & corners betting engine
-- Design rules encoded here (each prevents a specific v1 failure):
--   * every bet-bearing record carries a `mode` so backtest/paper/live can never be conflated
--   * odds are stored as observed prices, never assumed constants
--   * team identity is a foreign key, never a string compared with LIKE

create extension if not exists "uuid-ossp";

-- ─────────────────────────────────────────────────────────── identity ──

create table teams (
    id            bigserial primary key,
    sofascore_id  integer unique,          -- null only for historical-only clubs
    canonical_name text        not null,
    league        text        not null,
    country       text,
    created_at    timestamptz not null default now(),
    unique (canonical_name, league)
);

-- Every name any source has ever used for a team. Resolution is a JOIN,
-- not a dict lookup that falls open on a miss (v1's NAME_MAP.get(x, x)).
create table team_aliases (
    alias      text not null,
    source     text not null check (source in ('football_data','odds_api','sofascore','livescore','manual')),
    team_id    bigint not null references teams(id) on delete cascade,
    created_at timestamptz not null default now(),
    primary key (alias, source)
);

-- A name we could not resolve. Written instead of silently dropping the
-- fixture; the healthcheck fails while any row here is unreviewed.
create table unresolved_aliases (
    id         bigserial primary key,
    alias      text not null,
    source     text not null,
    context    jsonb,
    seen_count integer not null default 1,
    first_seen timestamptz not null default now(),
    last_seen  timestamptz not null default now(),
    reviewed   boolean not null default false,
    unique (alias, source)
);

-- ──────────────────────────────────────────────────────────── matches ──

create table matches (
    id                 bigserial primary key,
    sofascore_event_id bigint unique,        -- null for pre-2026 football-data rows
    league             text        not null,
    season             integer     not null, -- start year: 2025 = 2025/26
    kickoff_utc        timestamptz not null,
    home_team_id       bigint      not null references teams(id),
    away_team_id       bigint      not null references teams(id),
    status             text        not null default 'scheduled'
                       check (status in ('scheduled','live','finished','postponed','cancelled')),
    created_at         timestamptz not null default now(),
    -- historical rows have no event id, so dedupe on the natural key too
    unique (league, kickoff_utc, home_team_id, away_team_id),
    check (home_team_id <> away_team_id)
);

create index matches_kickoff_idx on matches (kickoff_utc);
create index matches_league_season_idx on matches (league, season);
create index matches_status_idx on matches (status) where status = 'scheduled';

create table match_stats (
    match_id      bigint primary key references matches(id) on delete cascade,
    home_goals    smallint, away_goals    smallint,
    ht_home_goals smallint, ht_away_goals smallint,
    home_shots    smallint, away_shots    smallint,
    home_shots_ot smallint, away_shots_ot smallint,
    home_corners  smallint, away_corners  smallint,
    home_yellow   smallint, away_yellow   smallint,
    home_red      smallint, away_red      smallint,
    -- Books settle "cards" as yellows + 2nd-yellow/straight reds weighted.
    -- Store the raw counts; the market-definition adjustment lives in the
    -- feature layer so it is explicit and testable.
    source        text not null check (source in ('football_data','sofascore')),
    ingested_at   timestamptz not null default now()
);

create index match_stats_corners_idx on match_stats (match_id)
    where home_corners is not null;

-- ─────────────────────────────────────────────────────────────── odds ──

create table odds_snapshots (
    id          bigserial primary key,
    match_id    bigint not null references matches(id) on delete cascade,
    bookmaker   text   not null,
    market      text   not null,   -- totals_cards | totals_corners | h2h | ...
    line        numeric(5,2),      -- null for h2h
    side        text   not null,   -- Over | Under | Home | Away | Draw
    price       numeric(8,3) not null check (price > 1.0),
    is_lay      boolean not null default false,
    fetched_at  timestamptz not null default now()
);

create index odds_snapshots_lookup_idx on odds_snapshots (match_id, market, line);
create index odds_snapshots_fetched_idx on odds_snapshots (fetched_at);

-- The last price seen before kickoff, promoted at settlement. This is the
-- permanent record EV and CLV are measured against; v1 never had one, which
-- is why no honest backtest was ever possible.
create table closing_odds (
    match_id   bigint not null references matches(id) on delete cascade,
    bookmaker  text   not null,
    market     text   not null,
    line       numeric(5,2),
    side       text   not null,
    price      numeric(8,3) not null,
    fetched_at timestamptz not null,
    primary key (match_id, bookmaker, market, line, side)
);

-- ────────────────────────────────────────────────────────────── model ──

create table model_versions (
    id           bigserial primary key,
    name         text        not null unique,
    market       text        not null,        -- totals_cards | totals_corners
    trained_at   timestamptz not null default now(),
    git_sha      text,
    train_cutoff date        not null,        -- no data after this date was seen
    params       jsonb       not null default '{}',
    metrics      jsonb       not null default '{}',
    is_active    boolean     not null default false
);

create unique index model_versions_active_idx on model_versions (market)
    where is_active;

create table predictions (
    id               bigserial primary key,
    match_id         bigint not null references matches(id) on delete cascade,
    model_version_id bigint not null references model_versions(id),
    market           text   not null,
    line             numeric(5,2) not null,
    prob_over        numeric(6,5) not null check (prob_over between 0 and 1),
    mu               numeric(6,3),            -- predicted mean count
    created_at       timestamptz not null default now(),
    unique (match_id, model_version_id, market, line)
);

-- ──────────────────────────────────────────────────── recommendations ──

create type bet_mode as enum ('backtest','paper','live');

create table recommendation_runs (
    id          bigserial primary key,
    mode        bet_mode    not null,
    created_at  timestamptz not null default now(),
    git_sha     text,
    notes       text
);

create table recommendations (
    id                  bigserial primary key,
    run_id              bigint not null references recommendation_runs(id) on delete cascade,
    match_id            bigint not null references matches(id),
    model_version_id    bigint not null references model_versions(id),
    market              text   not null,
    line                numeric(5,2) not null,
    side                text   not null check (side in ('Over','Under')),
    model_prob          numeric(6,5) not null,
    fair_odds           numeric(8,3) not null,   -- 1 / model_prob
    sharp_prob          numeric(6,5),            -- de-vigged sharp consensus
    sharp_price         numeric(8,3),
    sharp_book          text,
    -- The product: the worst price at which this bet is still worth taking.
    min_acceptable_odds numeric(8,3) not null,
    edge                numeric(6,5) not null,   -- model_prob - sharp_prob
    kelly_fraction      numeric(6,5),
    mode                bet_mode not null,
    created_at          timestamptz not null default now()
);

create index recommendations_run_idx on recommendations (run_id);
create index recommendations_match_idx on recommendations (match_id);

-- ─────────────────────────────────────────────────────────────── bets ──

create table bets (
    id                bigserial primary key,
    recommendation_id bigint references recommendations(id),  -- null = off-model bet
    match_id          bigint not null references matches(id),
    market            text   not null,
    line              numeric(5,2) not null,
    side              text   not null,
    bookmaker         text   not null default '1win',
    odds_taken        numeric(8,3) not null check (odds_taken > 1.0),
    stake             numeric(10,2) not null check (stake > 0),
    currency          text   not null default 'ILS',
    mode              bet_mode not null,
    status            text   not null default 'open'
                      check (status in ('open','won','lost','void','half_won','half_lost')),
    placed_at         timestamptz not null default now(),
    settled_at        timestamptz,
    pnl               numeric(10,2),
    -- closing-line value: odds_taken / closing_fair_odds - 1. The metric that
    -- reveals whether the edge is real in weeks rather than years.
    clv               numeric(6,5),
    note              text
);

create index bets_status_idx on bets (status);
create index bets_match_idx on bets (match_id);
create index bets_placed_idx on bets (placed_at);

create table bankroll_events (
    id          bigserial primary key,
    kind        text not null check (kind in ('deposit','withdrawal','adjustment')),
    amount      numeric(10,2) not null,
    currency    text not null default 'ILS',
    occurred_at timestamptz not null default now(),
    note        text
);

-- ───────────────────────────────────────────────────────── observability ──

create table job_runs (
    id            bigserial primary key,
    job           text not null,
    started_at    timestamptz not null default now(),
    finished_at   timestamptz,
    status        text not null default 'running'
                  check (status in ('running','ok','failed')),
    rows_affected integer,
    error         text,
    meta          jsonb
);

create index job_runs_job_idx on job_runs (job, started_at desc);
