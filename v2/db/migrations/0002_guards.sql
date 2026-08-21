-- Structural guarantees that make v1's specific failures impossible.

-- 1. Backtest output lives in a schema the API never exposes. v1 bulk-loaded
--    27 backtest weeks into the live picks table and the UI reported them as a
--    67% win rate. A `mode` column is a convention; an unexposed schema is not.
create schema if not exists research;

create table research.backtest_runs (
    id           bigserial primary key,
    model_version_id bigint references model_versions(id),
    created_at   timestamptz not null default now(),
    git_sha      text,
    params       jsonb not null default '{}',
    metrics      jsonb not null default '{}'
);

create table research.backtest_bets (
    id            bigserial primary key,
    run_id        bigint not null references research.backtest_runs(id) on delete cascade,
    match_id      bigint not null,
    market        text not null,
    line          numeric(5,2) not null,
    side          text not null,
    model_prob    numeric(6,5) not null,
    price         numeric(8,3) not null,
    stake         numeric(10,2) not null,
    won           boolean,
    pnl           numeric(10,2)
);

-- Records every time the locked test set is read. If this has more than one
-- row for a model, the "test" number stopped being a test number.
create table research.test_set_touches (
    id          bigserial primary key,
    touched_at  timestamptz not null default now(),
    git_sha     text,
    model_name  text,
    reason      text not null
);

-- 2. A bet cannot be logged after the match has started, or in the future.
--    v1's track record was contaminated by rows written long after the fact.
create or replace function assert_bet_placed_before_kickoff()
returns trigger language plpgsql as $$
declare
    ko timestamptz;
begin
    select kickoff_utc into ko from matches where id = new.match_id;
    if ko is null then
        raise exception 'bet references unknown match %', new.match_id;
    end if;
    if new.placed_at > ko then
        raise exception
            'bet placed_at % is after kickoff % - retroactive logging is not allowed',
            new.placed_at, ko;
    end if;
    if new.placed_at > now() + interval '1 minute' then
        raise exception 'bet placed_at % is in the future', new.placed_at;
    end if;
    return new;
end $$;

create trigger trg_bets_before_kickoff
    before insert or update on bets
    for each row execute function assert_bet_placed_before_kickoff();

-- 3. Append-only audit of every mutation to bets.
create table bets_audit (
    id         bigserial primary key,
    bet_id     bigint,
    action     text not null,
    old_row    jsonb,
    new_row    jsonb,
    changed_at timestamptz not null default now()
);

create or replace function audit_bets()
returns trigger language plpgsql as $$
begin
    insert into bets_audit (bet_id, action, old_row, new_row)
    values (coalesce(new.id, old.id), tg_op, to_jsonb(old), to_jsonb(new));
    return coalesce(new, old);
end $$;

create trigger trg_bets_audit
    after insert or update or delete on bets
    for each row execute function audit_bets();
