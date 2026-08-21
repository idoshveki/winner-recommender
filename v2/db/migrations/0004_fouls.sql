-- Fouls committed. Present in every football-data CSV for all four leagues and
-- never ingested by v1. Fouls are the causal mechanism behind cards.
--
-- Measured caveat (v2/docs/FINDINGS.md): in a hand-weighted blend these did NOT
-- improve the cards model. Stored so a fitted model can decide, not because the
-- gain is established.
alter table match_stats add column home_fouls smallint;
alter table match_stats add column away_fouls smallint;

-- Referee: free for EPL only (Referee column in E0.csv); other leagues need
-- SofaScore per fixture. Nullable by design, with a flag the model can gate on.
alter table matches add column referee text;
create index matches_referee_idx on matches (referee) where referee is not null;
