# Auto warming: design and implementation plan

Date: 2026-10-31 · Revision: 3 · Status: milestones 1–3 built on feat/auto-warming (queue rows show visits as Warm-up; the reason is in the execution data, not shown in the row yet). Not run on real accounts yet. Branch `feat/auto-warming`, built on `staging`
(it needs account health, adaptive resources and the persona from the natural session work).

## Recommendation

One global switch, **Auto warming: on | off**, for all accounts. When it is on, a small planner
decides by itself who warms, when, for how long and what they do, using only spare capacity.
Posts always come first: a warming session ends at its next safe point when a post needs its
slot, and when the post is for the same account, the open browser is handed straight to it.

| Choice | Decision and reason |
| --- | --- |
| Switch | Global, all accounts, off by default. Accounts in Needs attention are left out automatically. |
| Where visits run | As queue items of the existing `warming` type in one system batch per day, "Auto warming · <date>", kept at the end of the queue so it never changes the order of the user's batches. They reuse the dispatcher, slots, container admission, evidence, cancel and account history. |
| Capacity | Spare only: never takes a slot needed by a post due within 15 minutes, never starts when the machine is busy or under pressure. |
| Priority | Posts (desktop and cloud) always win. Warming yields at a safe point; a publisher is never interrupted. |
| Same-account post | The warming session ends and keeps the container (the existing preparation handoff), and the post skips its own preparation. |
| What a visit does | Read-only activities: feed, notifications, Reels, the Page itself, idle pauses. No likes, comments, shares, follows or chats. |
| Reactions | Left out. They are public, can land on tragic news, and fast likes from young accounts are a classic bot signal. Revisit after two weeks of passive warming, as a separate switch for Ready accounts only (Like only, 0–2 a day, never on sensitive posts). |
| Reporting | Only that the visit ran, how long, and why it stopped. Activity details (such as notifications opened) are not reported. |
| Cloud | Desktop only. Cloud jobs are never held for warming. |

## How it decides

### Need score (who)

Each eligible account gets a score once a minute. Highest score goes first.

| Signal | Effect |
| --- | --- |
| Stage | New 3, Warming up 2, Ready 1, Resting 0.5; Needs attention is never eligible |
| Quiet time | grows with hours since the account's last session (post or visit) |
| Today | each visit and post today lowers it; a daily visit cap per stage (New 4, Warming up 3, Ready 2, Resting 1) |
| Persona | only inside active hours; the persona's rest days (no visits); a per-persona "how often this person opens Facebook" factor |
| Upcoming post | a post due in 20 to 60 minutes raises it, so the visit becomes that post's warm-up |
| Recent visit | not again within 90 minutes of the last visit; no visit starts within 15 minutes before its own post |

### Capacity (when)

A new visit starts only when all hold:

- adaptive state is calm (not busy, not under pressure);
- a preparer slot is free after keeping one slot per post due in the next 15 minutes;
- no other auto visit is starting (normal start spacing applies);
- at most one auto visit is waiting in the queue; an unstarted one expires after 30 minutes.

### The session (what and how long)

A visit is a chain of short activities chosen from the persona, the stage and the machine:

| Activity | Notes |
| --- | --- |
| Feed | scroll with reading pauses and rereads (existing passive browsing) |
| Notifications | open the bell panel, read, scroll a little, close |
| Reels | look at the Reels tab of the Page, a few seconds per item |
| Own Page | open the Page by name click or F5 (natural navigation) and scroll |

Length is 1 to 6 minutes, shortened by the busy-machine scale. Between activities, and between
scrolls inside one, is a **safe point**: the session checks for a yield or a hold there and
can stop within about 10 seconds. Every visit starts with the normal login check, so a
checkpoint, login page or personal profile stops it and marks the account Needs attention.

## Priority

| Situation | What happens |
| --- | --- |
| Account A is warming, A's own post becomes due | A yield is written for A. The session ends at its next safe point as completed, the container stays open (handoff), and the post starts on it with preparation marked done. |
| A is warming, B's post is due, no slot is free | The running visit with the lowest need score gets the yield, ends, and its slot goes to B. |
| A post is running | Never interrupted. |
| Machine comes under pressure | All visits end at their next safe point. |

The yield travels like the busy-machine signal: the backend writes `data/resources/yield.json`
(`{profile_id: reason}`), and the warming task reads it at each safe point.

## Milestones

### Milestone 1: switch, planner and flexible visits

- Setting `auto_warming` (global), API and a switch in Settings with a one-line summary.
- `backend/services/auto_warming.py`: eligibility, need score, capacity check, creating one visit
  at a time in the "Auto warming" batch, expiry of unstarted visits. Runs on the background tick.
- `FacebookWarmingTask`: activity plan (feed, notifications, Reels, own Page), flexible length,
  safe points that honour holds and the busy-machine scale.
- Tests: scoring, capacity with posts due soon, caps and rest days, expiry, activity planning.

### Milestone 2: priority and handoff

- Yield signal file and its reader in the automation; the warming task stops at a safe point.
- Dispatcher: when a post cannot get a slot, yield the lowest-score visit; when the post is for
  an account that is warming, yield it and reuse its container through the handoff, marking the
  post's preparation done.
- Tests: yield on slot shortage, same-account handoff, publishers never yielded, cloud posts win.

### Milestone 3: visibility and tuning

- Queue rows "Active visit" with the reason it was chosen and how it ended (finished, yielded).
- Settings summary: visits today, next planned account and time.
- Tune scores and caps from real runs.

## Risks

| Risk | Mitigation |
| --- | --- |
| Visits use memory needed by posts | Spare-capacity rule, slots reserved for posts due soon, yield on demand. |
| Too regular a rhythm | Persona rest days, random times inside active hours, varying length and activities. |
| A visit lands on a checkpoint | Same login check as posts; the account goes to Needs attention and is skipped. |
| Handoff leaves a browser open with no post | The existing handoff expiry stops the container. |
