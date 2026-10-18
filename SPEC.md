# Waterloo Deadline Aggregator — Project Spec

Sep 22, 2026 · Eric Lu

## Overview & Goals

The user is a 1A Computer Science student at the University of Waterloo whose assignments, deadlines, and communications are scattered across a dozen unrelated interfaces — D2L/LEARN, Quest, Crowdmark, Outlook, Gmail, Slack, Discord, and Teams — each with its own login and its own notion of what matters. Nothing currently tells him, in one place, what is due, how much it counts, or what needs attention today.

This tool aggregates all of it into a single personal dashboard: it pulls deadlines, assignments, announcements, and relevant messages from every source he reads daily, unifies them into one data model, ranks them by a combination of objective grade weight and his own sense of importance, and pushes the result into both a customizable dashboard and his Google Calendar.

v1 is built for the user's own use only, run locally on his machine. The architecture is deliberately kept clean enough that it could later be distributed to other students — but v1 makes no compromises for that goal; it optimizes for solving the user's actual problem first.

## Data Sources & Access Strategy

Official APIs are used wherever they exist; browser automation (Playwright) is the fallback only where no API exists. v1 focuses on D2L first.

| Source | Access method | Notes |
| --- | --- | --- |
| D2L / LEARN | Check LEARN's per-course calendar feed (ICS/webcal subscription) first — may expose due dates with zero scraping. Fall back to Playwright scraping for announcements/content the feed doesn't cover. | v1 priority source |
| Quest | Playwright browser automation | Behind Waterloo SSO + Duo 2FA; session cookies persisted to avoid re-authenticating every run |
| Crowdmark | Playwright browser automation | No public API |
| Outlook / Teams | Microsoft Graph API (official) | OAuth |
| Gmail | Gmail API (official) | OAuth |
| Slack | Slack API (official) | OAuth |
| Discord | Bot API for servers the user can add a bot to | A self-bot reading personal DMs/servers as "the user" would violate Discord's ToS — out of scope unless a bot-accessible workaround exists |

Duo 2FA affects Quest, D2L, and any Waterloo-SSO-gated source: sessions are persisted locally so re-authentication is occasional, not per-run.

**Discord, revisited**: the club and design-team coordination the user wants tracked happens in servers they're already a member of, not personal DMs — so a bot added to those specific servers is a ToS-compliant path (resolves the earlier open question: no self-bot needed). It reads messages/announcements in the channels it's added to, the same as any other Discord bot integration.

**Website watcher**: a general-purpose module for arbitrary user-supplied URLs — club sites, external forms, anything without a structured feed. Checked once each morning as part of the daily refresh: fetches the page, diffs it against the last-seen snapshot, and surfaces "this page changed" as an item if something's new. No login/session handling needed since these are public pages the user points it at.

## System Architecture

Built in Python. Three logically separate components, even though v1 runs them all on one machine:

1. **Scrapers/connectors** — one module per source, each normalizing its output into the shared data model (see below).
2. **Backend/store** — aggregates items, computes priority scores, persists them, handles Google Calendar sync and encrypted credential storage.
3. **Dashboard** — the UI the user actually looks at.

v1 hosting: everything runs locally on the user's laptop, refreshing whenever it's on. Because the components are separated rather than monolithic, moving to an always-on host later (a Raspberry Pi, an old laptop, or a small VPS) is a relocation, not a redesign — explicitly deferred rather than designed for now.

**Refresh cadence**: the scraper runs on startup, then on a fixed interval (e.g. every 30–60 minutes) while the machine is on and connected — not continuous polling, to avoid hammering source sites or tripping rate limits/anti-bot detection, especially on Quest.

## Data Model

Every item from every source is normalized into one shared schema before it reaches the dashboard, calendar sync, or priority engine:

| Field | Description |
| --- | --- |
| `source` | Where it came from (D2L, Quest, Crowdmark, Outlook, Gmail, Slack, Discord, Teams, or `manual`) |
| `type` | assignment, quiz, exam, meeting, message, announcement, or manual entry |
| `title` | Item name |
| `course` | Associated course code, where applicable |
| `due_date` | When it's due or scheduled |
| `grade_weight` | % of final grade, entered manually from the syllabus where applicable |
| `importance` | the user's own subjective importance dial, per item or per course |
| `priority_score` | Computed — see Prioritization Engine below |
| `status` | Not started / in progress / done — user-editable |
| `source_link` | Deep link back to the original item on its source platform |
| `last_synced` | Timestamp of the most recent successful scrape for that item's source |

Manual entries (personal deadlines, verbally-announced items, club meetings) are first-class citizens of this schema — they carry `source: manual` and skip the scraping pipeline entirely, but flow through prioritization, the dashboard, and calendar sync identically to scraped items.

**Identity & deduplication**: each item's stable identity is derived from `(source, source's own internal ID or URL)`, never from title or due date, since those can change on a re-scrape. A rescrape updates the existing item in place rather than creating a duplicate — and the same identity is what a Google Calendar push checks against, so re-syncing updates the existing calendar event instead of creating a second one.

**Beyond courses**: the `course` field generalizes to a `context` — a course code (CS 135) or a non-academic group (a club or design team) — so club commitments, design-team meetings, and application deadlines live in the same schema and priority engine as coursework, not a separate system. Two more fields support time estimation (see Time Estimation & Learning Loop): `estimated_duration` and `actual_duration`.

## Prioritization Engine

Priority is a combination of three inputs, not a single number:

```
priority = grade_weight × importance × urgency(due_date)
```

- **`grade_weight`** — objective, entered manually per assignment from the syllabus (e.g. 15%). Defaults to a small nonzero value when unknown so ungraded items aren't invisible.
- **`importance`** — the user's own subjective dial, adjustable per course or per item, capturing things grade weight can't ("this course is harder for me," "this one matters for a scholarship").
- **`urgency(due_date)`** — rises as the due date approaches; a simple inverse-time-remaining curve is enough for v1.

This is deliberately rule-based for v1. Once there's a few months of real usage — what the user actually works on first, what he snoozes, what he leaves until the last minute — that behavior becomes training data for a learned model that captures his real prioritization pattern rather than a fixed formula. That's out of scope for v1 but the schema above (especially `status` transitions and timestamps) is designed to make that data collectible from day one.

## Time Estimation & Learning Loop

Each item gets an estimated duration — how long the user expects it to take — shown alongside its priority score. Initially this can be a simple default (a flat estimate per item type, or one the user sets himself when the item first appears); nothing sophisticated needed in v1.

When an item is marked done, the dashboard prompts for the actual time it took. That `estimated_duration` vs `actual_duration` pair is logged per item, and over time the tool calibrates: future estimates for that course/context or item type get adjusted by the user's own historical ratio (e.g. "assignments in MATH 137 have consistently taken him 1.4× his own estimate"). This is a smaller, faster-to-land version of the learned-prioritization idea above — a personal calibration factor, not a full model, and useful from the first few logged items rather than needing months of data.

## Dashboard UI & Customization

v1 is filter-and-sort based rather than a rearrangeable widget board — simpler to build, still gives the user real control over what he sees:

- **Filters/toggles**: by course, by source, by type (assignment/exam/meeting/message)
- **Sort**: by priority score, by urgency (due date), or by raw due date
- **Views**: a list view and a calendar view
- **Manual entries**: addable directly in the dashboard, alongside scraped items
- **Status**: mark items in progress / done directly from the dashboard

Ease of use is a first-class goal, not an afterthought — default view on open should require zero configuration to be useful (e.g. sorted by priority, all sources on).

**Setting grade weights**: entered through a per-course settings view in the dashboard itself (not a config file) — this is something the user will do at the start of every term, so it needs to be quick and live in the same UI as everything else.

**Today view**: a dedicated tab showing only what's relevant to today — classes, items due today, meetings — designed to be the first thing the user opens each morning as part of his daily check-in, no filtering required to get there.

## Google Calendar Sync

One-way sync (dashboard → calendar, never the reverse) via the official Google Calendar API. Aggregated deadlines push into a dedicated **"Waterloo Deadlines"** calendar, kept separate from the user's main personal calendar so it can be toggled on/off or wiped and resynced without touching anything else. OAuth handles authentication — no scraping or credential risk here, this is the most straightforward integration in the whole project.

## Security, Credentials & Failure Monitoring

**Credential storage**: all saved logins and session cookies are stored encrypted in a local file (or OS keychain) — never plaintext, never transmitted anywhere. This matters even more if the tool is distributed later, since it sets the precedent that each user's credentials stay on their own machine rather than being centrally held.

**Failure monitoring**: scraper failures should be rare if scraping is implemented correctly, but when a source breaks (a page redesign, an expired session, a changed selector) the dashboard actively flags it — e.g. "D2L hasn't updated in 3 days, re-check your login" — rather than failing silently. Each item's `last_synced` timestamp (see Data Model) is what drives this.

## Roadmap & Phased Scope

The user's current course load (1A, Fall 2026): CS 135, MATH 135, MATH 137, PHYS 121, ENGL 119.

| Phase | Scope |
| --- | --- |
| v1 (MVP) | D2L only (calendar feed check first, scraping fallback). Local-only, dashboard with filters/sort/views. Rule-based priority score. Encrypted local credentials. Active failure flags. |
| v2 | Remaining sources (Quest, Crowdmark, Outlook/Teams, Gmail, Slack). Google Calendar sync to dedicated "Waterloo Deadlines" calendar. |
| v3+ | Learned/ML-based prioritization trained on the user's real usage history. Tab auto-launch at class time (scheduled trigger + course→URL config, built off the Quest schedule). Always-on hosting (Pi or VPS) if local-only staleness becomes a real problem. Discord integration if a ToS-compliant path is found. Evaluate distribution to other students — requires revisiting the credential-storage model for a multi-user context. |

## Risks, Constraints & Non-Goals

The Quest, Crowdmark, Odyssey and course-page connectors read pages in a browser window that the user signs into themselves (Waterloo login + Duo). The tool never enters credentials, never submits anything, and only reads pages the user can already see. These connectors are opt-in; the D2L calendar feed and Outlook work without them. Anyone using them is responsible for following their own institution's IT policies, and syncs are kept as infrequent as the refresh cadence allows.

**Out of scope for v1** (explicitly, to keep the build focused):

- No messages sent on the user's behalf on any platform
- No editing of D2L/Quest/Crowdmark content — read-only
- No two-way Google Calendar sync — the dashboard is always the source of truth
- No push notifications/digests — the dashboard is pull, not push
- No multi-user/distribution support — single local user only
