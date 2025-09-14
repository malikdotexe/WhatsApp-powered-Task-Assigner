Task Assigner with WhatsApp Reminders

A lightweight task manager that sends automated WhatsApp reminders to assignees. Built with Streamlit, SQLAlchemy, and APScheduler. Messages are sent through your self-hosted WAHA (WhatsApp HTTP API).

Features

Contacts: save people, normalize to E.164, auto-derive chatId like 9195…@c.us.

Tasks: title, description, priority, due date, assignee.

Workflow: open → in_progress → completed | cancelled.

Reminders: daily or every N days, for a window of N days (default 5). Jobs persist across restarts.

Actions: “Remind now”, comments, safe delete that also clears future jobs.

Admin: view scheduled jobs, edit reminder template.

How it helps

Keeps a single source of truth for ownership and deadlines.

Nudges happen on WhatsApp, which usually gets faster responses.

No SaaS dependency. Runs locally or on a small VM with your WAHA instance.

WAHA setup

Set up WAHA first. Go to the official docs: https://waha.devlike.pro/
.
You need:

The base URL of your WAHA server (for example http://localhost:3000 or your reverse-proxied domain).

A session name that is logged in to WhatsApp Web.

Quick send test once WAHA is running:

curl -X POST "$WA_API_BASE/api/sendText" \
  -H "Content-Type: application/json" \
  -d '{"chatId":"<E164digits>@c.us","text":"hello from WAHA","session":"<your_session>"}'


If this works, the app will work too.

Install
python -m venv venv
source venv/bin/activate           # Windows: venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt

Configure

Either export env vars or copy .env.example to .env and fill values.

Environment variables

WA_API_BASE base URL for WAHA, for example http://localhost:3000

WA_API_SEND WAHA send endpoint, default /api/sendText

WA_API_SESSION your WAHA session name, for example default

WA_DB_URL SQLAlchemy DB URL, default sqlite:///wa_task_app.sqlite

WA_TZ timezone for scheduler and UI, default Asia/Kolkata

Example:

export WA_API_BASE="http://localhost:3000"
export WA_API_SEND="/api/sendText"
export WA_API_SESSION="default"
export WA_DB_URL="sqlite:///wa_task_app.sqlite"
export WA_TZ="Asia/Kolkata"

Run
streamlit run task_assigner_app.py


Usage

Open Contacts, add at least one contact.

Open Create Task, set first reminder time, frequency, and “remind for N days.”

Open Tasks Board to change status, add comments, “Remind now,” or delete.

Jobs tab shows APScheduler jobs loaded from the DB.

Scheduling semantics

One interval job per task:

start_date = first reminder

days = freq_days

end_date = start_date + remind_for_days

Completing or cancelling a task stops future reminders.

Jobs are persisted in SQLite through APScheduler’s SQLAlchemy job store.

Data and persistence

SQLite file wa_task_app.sqlite holds contacts, tasks, comments, settings, and jobs.

Safe reset: stop the app and delete the file if you want a clean slate.

Troubleshooting

Task Board empty: ensure the “Tasks Board” tab renders with with tabs[0]: if it is the first tab. Create a task after adding a contact.

“Remind now” shows a failure: the app surfaces WAHA’s exact response. Fix WAHA connectivity or session.

Time resets to now: avoid recomputing value= on every rerun. Use a form or st.session_state.

Duplicate widget IDs: add unique key= to repeated buttons.

DetachedInstanceError: relationships are eager-loaded in this app. If you change queries, keep selectinload or lazy="joined".

Security

Protect your WAHA endpoint behind auth and an allow-list if it is exposed on the internet. Do not log PII in production.

Roadmap

Multi-assignee tasks.

Escalations after N days.

Per-project templates.

Optional approvals.

License

MIT by default. Change if your org requires something else.
