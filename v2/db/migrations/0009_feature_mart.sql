-- Feature mart: everything about a match that was knowable BEFORE kickoff,
-- in one place, so rules can be written against columns instead of rebuilt
-- from scratch each time.
--
-- Every column is as-of the moment before the match starts. The builder fills
-- a row and only then updates its rolling stores, which makes lookahead
-- structurally impossible rather than something to remember.

create table team_match_form (
    match_id      bigint  not null references matches(id) on delete cascade,
    team_id       bigint  not null references teams(id),
    venue         text    not null check (venue in ('home','away')),
    opponent_id   bigint  not null references teams(id),

    -- season to date
    played        smallint, points smallint, position smallint,
    goals_for     smallint, goals_against smallint,

    -- last 5 / last 10, all competitions in our set
    l5_points     smallint, l10_points smallint,
    l5_goals_for  numeric(5,2), l5_goals_against numeric(5,2),
    l5_cards      numeric(5,2), l5_corners numeric(5,2),
    l5_fouls      numeric(5,2), l5_shots   numeric(5,2),
    l10_cards     numeric(5,2), l10_corners numeric(5,2),

    -- same venue only (home form for the home side, away form for the away side)
    venue_l5_cards   numeric(5,2), venue_l5_corners numeric(5,2),
    venue_l5_points  smallint,
    venue_l5_goals_for numeric(5,2), venue_l5_goals_against numeric(5,2),
    venue_l5_cards_against numeric(5,2),

    -- sequences
    win_streak    smallint, loss_streak smallint, unbeaten_streak smallint,
    winless_streak smallint,
    last5         text,          -- e.g. 'WWDLW', most recent first

    days_rest     smallint,
    primary key (match_id, team_id)
);

create index team_match_form_team_idx on team_match_form (team_id);

-- One row per match with both sides side by side, plus context and the
-- combined predictors that v1-style rules are written against.
create table match_features (
    match_id     bigint primary key references matches(id) on delete cascade,
    league       text not null,
    season       smallint not null,
    kickoff_utc  timestamptz not null,
    round_num    smallint,          -- matchday, from matches played
    season_pct   numeric(4,3),      -- 0 = opening day, 1 = final day
    month        smallint,

    home_team_id bigint references teams(id),
    away_team_id bigint references teams(id),

    -- v1-style combined predictors, kept because they are what the user's
    -- rules are phrased in: "home average + away average, is it high enough"
    cards_pred        numeric(5,2),   -- home venue_l5_cards + away venue_l5_cards
    corners_pred      numeric(5,2),
    goals_pred        numeric(5,2),
    fouls_pred        numeric(5,2),

    position_diff     smallint,       -- home position - away position
    points_diff       smallint,
    form_diff         smallint,       -- l5 points difference
    both_top6         boolean,
    both_bottom6      boolean,
    home_favourite    boolean,

    -- outcomes, for convenience when writing rules
    total_cards_book  smallint,       -- yellows + 2x reds (how books settle)
    total_yellows     smallint,
    total_corners     smallint,
    total_goals       smallint,
    result            text,
    btts              boolean
);

create index match_features_league_idx on match_features (league, season);
create index match_features_cards_idx on match_features (cards_pred);
create index match_features_corners_idx on match_features (corners_pred);
