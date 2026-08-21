alter table matches add column odds_api_event_id text;
create unique index matches_odds_api_event_idx on matches (odds_api_event_id)
    where odds_api_event_id is not null;
