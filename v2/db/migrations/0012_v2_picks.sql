-- Room for v2's own picks alongside v1's.
--
-- v2 has never recorded a pick because it has never had a picker: every
-- strategy tested failed its gate, so building a recommender would have meant
-- shipping something measured as unprofitable. What it CAN honestly do is
-- record what it would choose and how much edge it thinks it has - including
-- weeks where the honest answer is "nothing qualifies".
--
-- That makes the two systems comparable: v1 picks every week regardless, v2
-- picks only when its gate passes. Which of those does better over a season is
-- an empirical question worth recording rather than arguing about.

alter table v1_live_record add column if not exists edge       numeric(7,4);
alter table v1_live_record add column if not exists qualifies  boolean;
alter table v1_live_record add column if not exists model_prob numeric(6,5);
alter table v1_live_record add column if not exists book_prob  numeric(6,5);

comment on column v1_live_record.edge is
    'model probability x price - 1, at the price actually available. NULL for v1, which never computed edge against a real price.';
comment on column v1_live_record.qualifies is
    'true when the pick cleared v2''s edge threshold. false = ranked best but not worth betting. NULL for v1.';
