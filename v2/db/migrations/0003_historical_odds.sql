-- Closing odds that ship with the football-data CSVs. Kept separate from
-- match_stats so the stats table stays narrow.
--
-- Note the open/close split: v1 ingested PSH/PSD/PSA (Pinnacle OPENING) and
-- used them as its closing sharp reference throughout. Both are stored here,
-- correctly labelled; the open->close drift is itself a usable feature.
create table historical_odds (
    match_id         bigint primary key references matches(id) on delete cascade,
    pinnacle_close_h numeric(8,3), pinnacle_close_d numeric(8,3), pinnacle_close_a numeric(8,3),
    pinnacle_open_h  numeric(8,3), pinnacle_open_d  numeric(8,3), pinnacle_open_a  numeric(8,3),
    b365_h           numeric(8,3), b365_d           numeric(8,3), b365_a           numeric(8,3),
    avg_h            numeric(8,3), avg_d            numeric(8,3), avg_a            numeric(8,3),
    avg_over25       numeric(8,3), avg_under25      numeric(8,3),
    ah_line          numeric(5,2),
    avg_ah_home      numeric(8,3), avg_ah_away      numeric(8,3)
);
