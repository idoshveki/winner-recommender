"""Email v2's recorded picks for the week.

v2 was built to record rather than recommend, and that was the right default
while nothing had been validated. The side effect was that the markets this
project exists for - yellow-card totals - were computed on current data every
morning and never reached anyone, while v1 kept emailing from a results table
frozen at 2026-03-09.

This reads what the picker already wrote to v1_live_record. It does not
re-compute anything. If the email and the tracked record could disagree, the
record would be worthless, and the record is the only thing here that has ever
told the truth.

    python -m v2.recommend.send_email [--week 2026-10-05/2026-10-11] [--dry-run]
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta, timezone

from v2.lib.jobs import connect, job

TO = ["idoshveki@gmail.com"]
FROM = "Winner Picks <onboarding@resend.dev>"

CARD = "#fff"
INK = "#111827"
MUTED = "#6b7280"
LINE = "#e5e7eb"


def week_label(when: datetime) -> str:
    monday = when.date() - timedelta(days=when.weekday())
    return f"{monday}/{monday + timedelta(days=6)}"


def spell(market: str, pick: str) -> str:
    """"Over 4.5" means nothing without its market. Say the whole thing."""
    m = (market or "").upper()
    if "YC" in m:
        return f"{pick} yellow cards"
    return {"H": "Home win", "A": "Away win", "D": "Draw"}.get(pick, pick)


def fetch(conn, week: str) -> tuple[list[dict], dict]:
    picks = [
        dict(leg=r[0], market=r[1], match=r[2], pick=r[3], odds=float(r[4]),
             edge=float(r[5]) if r[5] is not None else None,
             qualifies=r[6], kickoff=r[7], model=r[8], book=r[9])
        for r in conn.execute(
            """select leg, market, match_text, pick, odds_quoted, edge,
                      qualifies, kickoff_utc, model_prob, book_prob
                 from v1_live_record
                where system = 'v2' and week = %s
                order by leg""", (week,)).fetchall()]

    settled = conn.execute(
        """select count(*), coalesce(sum(case when hit then odds_quoted - 1
                                         else -1 end), 0),
                  coalesce(sum(case when hit then 1 else 0 end), 0)
             from v1_live_record
            where system = 'v2' and hit is not null""").fetchone()
    record = dict(n=settled[0], pnl=float(settled[1]), won=settled[2])
    return picks, record


def render(week: str, picks: list[dict], record: dict) -> str:
    def row(p: dict) -> str:
        ko = p["kickoff"].strftime("%a %d %b, %H:%M") if p["kickoff"] else ""
        edge = f'{p["edge"] * 100:+.1f}%' if p["edge"] is not None else "—"
        gate = ("" if p["qualifies"] else
                f'<div style="color:{MUTED};font-size:12px;margin-top:6px">'
                f'Below the 5% edge gate — recorded for comparison, not a '
                f'recommendation.</div>')
        probs = ""
        if p["model"] is not None and p["book"] is not None:
            probs = (f'<div style="color:{MUTED};font-size:12px;margin-top:4px">'
                     f'model {float(p["model"]) * 100:.1f}% vs book '
                     f'{float(p["book"]) * 100:.1f}%</div>')
        return (
            f'<div style="padding:16px;border:1px solid {LINE};border-radius:8px;'
            f'margin-bottom:12px">'
            f'<div style="color:{MUTED};font-size:12px;text-transform:uppercase;'
            f'letter-spacing:.04em">{ko}</div>'
            f'<div style="font-size:17px;font-weight:600;color:{INK};'
            f'margin:4px 0 8px">{p["match"]}</div>'
            f'<div style="font-size:15px;color:{INK}">'
            f'<strong>{spell(p["market"], p["pick"])}</strong> '
            f'@ {p["odds"]:.2f} <span style="color:{MUTED}">· edge {edge}</span>'
            f'</div>{probs}{gate}</div>')

    if picks:
        body = "".join(row(p) for p in picks)
    else:
        body = (f'<div style="padding:16px;border:1px dashed {LINE};'
                f'border-radius:8px;color:{MUTED}">No pick cleared the gate this '
                f'week. Most weeks nothing will — that is the finding, not a '
                f'failure.</div>')

    if record["n"]:
        rec = (f'{record["won"]} of {record["n"]} settled, '
               f'{record["pnl"]:+.2f}u')
    else:
        rec = "nothing settled yet"

    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'</head><body style="margin:0;padding:20px;background:#f3f4f6;'
        f'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif">'
        f'<div style="max-width:560px;margin:0 auto;background:{CARD};'
        f'border-radius:10px;overflow:hidden">'
        f'<div style="padding:20px 20px 0">'
        f'<div style="font-size:20px;font-weight:700;color:{INK}">v2 picks</div>'
        f'<div style="color:{MUTED};font-size:13px;margin-top:2px">{week}</div>'
        f'</div>'
        f'<div style="padding:16px 20px 0">{body}</div>'
        f'<div style="padding:4px 20px 20px;color:{MUTED};font-size:12px;'
        f'line-height:1.6">'
        f'<div style="border-top:1px solid {LINE};padding-top:12px">'
        f'<strong>v2 record so far:</strong> {rec}</div>'
        f'<div style="margin-top:8px">v2 has no demonstrated edge. Out of '
        f'sample, this model\'s disagreement with Pinnacle lost about 5%. '
        f'These are recorded so the two systems can be compared over a season '
        f'— stake accordingly.</div>'
        f'<div style="margin-top:8px">'
        f'<a href="https://idoshveki.github.io/winner-recommender/" '
        f'style="color:#2563eb">Full betting record →</a></div>'
        f'</div></div></body></html>')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    week = args.week or week_label(datetime.now(timezone.utc))

    with connect() as conn:
        picks, record = fetch(conn, week)
    print(f"  week {week}: {len(picks)} recorded pick(s), "
          f"record {record['won']}/{record['n']} ({record['pnl']:+.2f}u)")
    for p in picks:
        print(f"    {p['match']}: {spell(p['market'], p['pick'])} @ {p['odds']:.2f}"
              f"{'' if p['qualifies'] else '  (below gate)'}")

    html = render(week, picks, record)
    if args.dry_run:
        out = "/tmp/v2_email_preview.html"
        with open(out, "w") as fh:
            fh.write(html)
        print(f"  dry run — wrote {out}, nothing sent")
        return 0

    key = (os.getenv("RESEND_API_KEY") or "").strip()
    if not key:
        raise SystemExit("RESEND_API_KEY is not set")

    import resend
    resend.api_key = key
    # Counted as a job so a silent failure to send shows up as a failed run
    # rather than being assumed to have worked, which is how v1 went five
    # months writing nothing.
    with job("v2-send-email", expect_rows=False) as r:
        resp = resend.Emails.send({
            "from": FROM, "to": TO,
            "subject": f"[v2] Picks {datetime.now().strftime('%Y-%m-%d')}",
            "html": html,
        })
        r.meta["resend_id"] = (resp or {}).get("id")
        r.meta["picks"] = len(picks)
        print(f"  sent to {', '.join(TO)} (id {(resp or {}).get('id')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
