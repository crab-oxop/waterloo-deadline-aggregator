# Waterloo Deadline Aggregator

See `SPEC.md` for the design. Status: v1 steps 1–4 working and verified against real LEARN + Google Calendar (LEARN feed → store → bare dashboard → Google Calendar sync). Events more than 7 days overdue are treated as history: priority 0, hidden, never pushed to the calendar.

## Setup

```
pip install -r requirements.txt
```

**Get your LEARN feed URL:** open LEARN → any course → Calendar → Settings → check "Enable Calendar Feeds" → Save. Click **Subscribe**, choose **All Courses**, and copy the link. Then:

```
python -m aggregator setup-d2l
```

It prompts for the link with input hidden, checks that it returns a calendar, and saves it to Windows Credential Manager. The link has a private token in it, so don't paste it anywhere else.

**Connect Google Calendar (one time):**
1. Go to https://console.cloud.google.com, create a project (e.g. "Deadline Aggregator").
2. APIs & Services → Library → enable **Google Calendar API**.
3. APIs & Services → OAuth consent screen → External, fill in the app name + your email, add yourself under **Test users**.
4. APIs & Services → Credentials → Create credentials → OAuth client ID → **Desktop app** → download the JSON.
5. `python -m aggregator setup-gcal path\to\credentials.json` and approve in the browser.

The app only asks for `calendar.app.created`: it can manage the "Waterloo Deadlines" calendar it creates and nothing else. Events have no reminders (v1 is pull, not push). Deleting that calendar by hand is safe; the next sync recreates it.

**Connect Crowdmark (one time):** `python -m aggregator setup-crowdmark`. A Chromium window opens; sign in with LEARN (Waterloo login + Duo). After that, syncs run invisibly and read-only against Crowdmark's own student API. Anything you've submitted is marked done automatically. If the dashboard says the Crowdmark sign-in expired, run the command again. Requires `pip install playwright` and `python -m playwright install chromium`.

**Connect Outlook:**
- *Calendar (works now):* in Outlook on the web → Settings → Calendar → Shared calendars → Publish a calendar → **Calendar**, **Can view all details** → Publish. Copy the **ICS** link, then run `python -m aggregator setup-outlook-ics` and paste it at the hidden prompt. Treat the link like a password: anyone with it can read your calendar. Use **Reset links** there if it ever leaks.
- *Mail + calendar via Microsoft Graph (needs IST):* Microsoft requires Waterloo's admin approval for `Mail.Read`/`Calendars.Read`, and students can't register apps. Once IST provides an app (client) ID: `python -m aggregator setup-outlook --client-id <id> --tenant uwaterloo.ca`. Graph then replaces the ICS link. Emails are kept only if they mention one of your courses (see `aggregator/mail_filters.py`, designed to be swapped out).

**Desktop app:** double-click **Waterloo Deadlines** on your Desktop or in the Start menu. To recreate the shortcuts, run `python -m aggregator install-shortcut`; to launch from a terminal, run `python -m aggregator app`. It opens a native window, and closing the window quits the app. `serve` still runs the same dashboard in your browser.

**Signing in:** on launch the app checks every connection. If anything needs you, it opens on the **Connections** page:
- **Sign in to Waterloo** opens one browser window. Sign in once, approve Duo once, and both Crowdmark and Quest are done. Crowdmark signs you out after about 8 hours, so expect this most mornings.
- **Links** (LEARN feed, Outlook calendar): paste them into the boxes. Ctrl+V works, and the link stays hidden on screen. Each link is checked before it's saved.
- Use `--no-login` to skip the check. `python -m aggregator login` does the same from a terminal: copy the link, then press Enter, and it's read from your clipboard.

## Use

```
python -m aggregator sync     # pull once
python -m aggregator list     # print items by priority
python -m aggregator serve    # dashboard at http://127.0.0.1:8765 (syncs on start + every 10 min)
python -m aggregator serve --no-calendar   # same, but never pushes to Google Calendar
python -m pytest              # tests
```

Dashboard tabs:
- **Today**: what's overdue and due today, with a total time estimate, plus today's suggested work blocks. Class times arrive once Quest is connected.
- **Work plan**: a week time grid of suggested study blocks, fitted into free time: the hours you're awake (set your wake-up and bedtime; after midnight is fine), minus classes, calendar events, your own events and a 15-minute buffer around each. Blocks start 30 min after you wake and end 30 min before bed. Edit freely: drag a block's top or bottom edge to resize it, drag the block to move it (to another day too), or click it to type exact times. Each day runs until 3 AM the next morning, so late-night work and events sit on the evening they belong to. Until your wake-up time it is still the day before: at 1 AM Saturday the plan still opens on Friday, with the rest of Friday night on it; a time typed before 3 AM means that night, after midnight. The hours outside your wake-up and bedtime are shaded but you can still put things there. A block you edit becomes yours (accepted); shrink it and the planner suggests the rest elsewhere. Drag on empty space (or use Add event) to add your own events, such as the gym or a shift. The planner works around them and they go to Google Calendar too. Highest priority first; blocks of 30 min–2 h, placed a few days before each deadline and aiming to finish ~12 h early. Anything that can't fit is flagged with how many hours it's short. Blocks are only suggestions: accept or dismiss each one, or accept all for today. Accepted blocks go to Google Calendar as `Work: …` events and never move; if something new overlaps one, it's flagged instead. Marking an item done removes its remaining blocks. Sleep and wake-up times, longest block, days to plan and the planning style are under Courses & weights. The default style, **Proactive**, suggests work earlier and aims to finish a full day before each deadline; **Balanced** and **Relaxed** plan closer to deadlines. If you shrink or move a block, the rest of that task is still suggested, at least an hour away from your edit.
- **Make time by hand**: drag a "Won't fit" row, or a goal from the **Add time** row, onto the calendar to place a 30-minute block exactly where you drop it, then resize it. Do it as often as you like, e.g. a goal in two sittings. Clicking a row or chip instead lets you type the time. Hand-placed blocks are yours: never moved, and counted toward the item. Times that have already passed are refused.
- **Plan my day** (Today and Work plan): plans out the rest of today in one go. It accepts today's suggestions, adds goal sessions, then pulls upcoming work forward into the free time left, with a 15-minute break between blocks and never more than your max work per day (default 6 h). **Undo day plan** takes back exactly what it added.
- **Goals**: study time beyond finishing deliverables, e.g. "Review MATH 137, 3 h per week" or "Prepare for PHYS 121 Midterm 1, 6 h by Oct 8". The planner fits goal sessions into free time after deadline work, one per goal per day; unused time carries over until there's enough for a real session. Goals that are out of date are removed automatically: a goal with a date once that day has passed, an exam goal once the exam has started. The page shows progress, and suggests goals from upcoming quizzes and exams; a goal linked to an exam replaces that exam's default study estimate. Study blocks (goals, quiz and exam prep) are labelled **Study:**, and work blocks **Work:**, here and in Google Calendar.
- **Midterm prep**: when an exam on your Odyssey schedule is within 14 days and has no study goal, Today and the Work plan offer one: "MATH 137 midterm, Tue Oct 20: add a study goal? 8h over 10 days". **Add** creates it, **Change hours** lets you pick the hours first, and **Not this one** hides it until the exam's date changes. Nothing is created without asking. The hours are spread evenly over the days before the exam and must be done before it starts. If Odyssey moves the exam, the goal moves with it, and it's removed once the exam has started. Exams without a time yet are skipped until they get one. Today shows a countdown to the next exam, with the focus-session time logged on its goal. The defaults (quiz 2 h, midterm 8 h, final 15 h, and how far ahead to look) are in Settings → Midterm prep. A test is a final when its title says so, or when it falls in an exam period and doesn't say midterm or quiz; otherwise it's a midterm.
- **Test check-ins**: after a test ends (an Odyssey sitting, or any quiz or exam with an end time), the next time you open the app it asks how it went, what could be improved, any other notes, and how long it took (filled in with the scheduled length). If the app is already open, the page reloads itself once the test is over, or as soon as you come back to the window. **Later** asks again in 4 hours; **Skip** stops asking about that test. Tests more than a week old aren't asked about. Your answers are on the Goals page under *After your tests*, and the last "what could be improved" note for a course shows on the next midterm-prep card for it. This is an in-app question, not a Windows notification, and nothing leaves the machine.
- **Focus sessions**: double-click a block on the Work plan, or one of your own events (or press **Focus** in its editor), to start one. Sessions on your own events end with **Log this session**, since there is nothing to mark done. A single click or Enter still opens the editor. Set a goal for the session; Start can open the assignment, your music link, and Claude with a tutor prompt filled in. Claude gets nothing until you press send, and a Copy button is there as a fallback. In the desktop app the countdown sits in a small always-on-top window (under `serve` it's a page); the numbers grow to fill the window, so make it bigger for a bigger timer. Breaks pause the timer, **+15 min** extends the block, and a chime plays when time's up (it can be turned off). **Finish** asks whether the item is done: Done marks it complete and records the focused time; Not yet replans what's left. If the session ran long, the block is stretched to match, so Google Calendar shows what really happened. A session left running when the app closed shows a banner to reopen or end it. Music, the default toggles and the Claude prompt template are in Settings → Focus sessions.
- **Free now**: when you're awake and have a gap right now, Today and Work plan suggest the most important thing to work on, with **Start now** (it books a block until your next commitment). If a planned block is about to start, the card offers **Start it** instead.
- **Reminders**: things to remember that aren't coursework. They're grouped by when you need to think about them: Overdue, Today, Tomorrow, This week, Next week, Later, No set date, and Keep in mind (ongoing habits). Add one from the quick-add bar and tick the circle when it's done. Dated reminders also go to Google Calendar.
- **All deadlines**: everything, with filters (course, source, type, open only) and sort (priority or due date). Set each item's status from its row. Marking something **Done** asks how long it actually took (or Skip), drops it to the bottom, and removes it from Google Calendar.
- **Course settings**: grade weights from each syllabus, a per-course importance (1 = normal), and your own time estimate per item.

Time estimates start from your own number or a default per type (assignment 2h, quiz 30m, exam 3h). After 3+ items with logged actual times, estimates scale by how long things really take you, per course first, then per type, then overall.

Runtime data (database, logs, the Waterloo browser profile, token cache, saved page captures) lives
outside the project, which sits in OneDrive, so sessions never leave this machine:
`%LOCALAPPDATA%\WaterlooDeadlines`, or with Microsoft Store Python
`%LOCALAPPDATA%\Packages\<python package>\LocalCache\Local\WaterlooDeadlines` (see `aggregator/paths.py`).
Set `WATERLOO_DEADLINES_DATA` to use another folder.
