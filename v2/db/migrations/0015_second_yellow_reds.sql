-- A red card shown for a second yellow is ONE dismissal, not two bookings.
--
-- SofaScore's statistics endpoint counts that second yellow in home_yellow /
-- away_yellow AND counts the resulting dismissal in home_red / away_red. Our
-- cards total was yellows + 2*reds, so such a player was counted four times
-- instead of three, and the match total came out one card too high.
--
-- Bookmakers settle a second-yellow dismissal as one yellow plus one red:
-- 1 + 2 = 3 cards. SC Freiburg v FC Schalke on 2026-10-11 had four yellows
-- (two to Yuito Suzuki, the second of which sent him off) and one red: five
-- cards by that rule, not the six our formula produced.
--
-- 32% of red cards in a 25-match sample were second yellows, so roughly 500
-- of the 1,383 matches holding a red have an inflated cards target - every one
-- of them biased toward Over, which is the direction v2 has been betting.
--
-- Null means "not yet established", which is different from zero. Only the
-- cards total needs this; the yellows-only market is unaffected.
alter table match_stats
    add column if not exists second_yellow_reds smallint;

comment on column match_stats.second_yellow_reds is
    'Dismissals for a second yellow. cards_book = yellows - this + 2*reds.';

-- Matches with no red card cannot contain a second-yellow dismissal, so they
-- are known to be zero without asking SofaScore for anything.
update match_stats
   set second_yellow_reds = 0
 where second_yellow_reds is null
   and coalesce(home_red, 0) + coalesce(away_red, 0) = 0;
