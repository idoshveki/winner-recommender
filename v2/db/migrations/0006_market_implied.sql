-- The distribution implied by a book's whole line ladder, not a single line.
-- Fitting all lines at once is more stable than any one pair, and it yields a
-- fair price at lines the book never quoted - which is what makes a
-- recommendation actionable when 1win offers a different line to Pinnacle.
create table market_implied (
    match_id     bigint not null references matches(id) on delete cascade,
    bookmaker    text   not null,
    market       text   not null,
    captured_at  timestamptz not null default now(),
    n_lines      smallint not null,
    overround    numeric(6,4),
    devig_method text not null,
    implied_mean numeric(7,3) not null,
    dispersion   numeric(9,3),          -- null => Poisson
    fit_rmse     numeric(8,6) not null,
    primary key (match_id, bookmaker, market, captured_at)
);

create index market_implied_lookup_idx on market_implied (match_id, market, captured_at desc);
