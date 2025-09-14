# Task Assigner with WhatsApp Reminders
A lightweight task manager that sends automated WhatsApp reminders to assignees. Built with Streamlit, SQLAlchemy + SQLite, and APScheduler. Messages are sent via your self-hosted WAHA (WhatsApp HTTP API).
✨ Features
* Contacts: save people, normalize to E.164, auto-derive chatId like 9195…@c.us.
* Tasks: title, description, priority, due date, assignee.
* Workflow: open → in_progress → completed | cancelled.
* Reminders: daily or every N days; remind for N days (default 5). Jobs persist across restarts.
* Actions: “Remind now,” comments, safe delete that also clears future jobs.
* Admin: view scheduled jobs, edit the reminder template.
💡 Why this exists
* Keeps a single source of truth for who owns what and by when.
* Nudges happen on WhatsApp, which usually gets faster responses.
* No SaaS dependency. Runs locally or on a small VM with your WAHA instance.

🧩 **Architecture**
* UI: Streamlit single-file app
* DB: SQLite via SQLAlchemy (contacts, tasks, comments, settings, APScheduler jobs)
* Scheduler: APScheduler with SQLAlchemy job store (durable schedules)
* Messaging: WAHA HTTP API (/api/sendText)
Flow: Create task → schedule interval job → job posts to WAHA → assignee gets a WhatsApp ping → completing or cancelling the task stops future pings.

✅ **Prerequisites**
* Python 3.10+
* A running WAHA server and a logged-in session Go to https://waha.devlike.pro/ and follow their docs to set it up. If you self-host with Docker, use their quick start.
Sanity check WAHA:

#Replace values accordingly
export WA_API_BASE="http://localhost:3000"
export WA_API_SESSION="default"

curl -X POST "$WA_API_BASE/api/sendText" \
  -H "Content-Type: application/json" \
  -d '{"chatId":"<E164digits>@c.us","text":"hello from WAHA","session":"'"$WA_API_SESSION"'"}'
If this succeeds, the app will be able to send messages.

🛠️ **Install**

python -m venv venv
source venv/bin/activate           # Windows: venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
requirements.txt:

streamlit
requests
apscheduler
sqlalchemy
pandas
phonenumbers
python-dateutil

⚙️ **Configure**
You can export environment variables or use a .env (if you load it yourself). Default values are reasonable for local use.

export WA_API_BASE="http://localhost:3000"       # your WAHA base URL
export WA_API_SEND="/api/sendText"               # WAHA send endpoint
export WA_API_SESSION="default"                  # your WAHA session name
export WA_DB_URL="sqlite:///wa_task_app.sqlite"  # SQLite file
export WA_TZ="Asia/Kolkata"                      # app + scheduler timezone
.env.example:

#WAHA config
WA_API_BASE=http://localhost:3000
WA_API_SEND=/api/sendText
WA_API_SESSION=default

#App config
WA_DB_URL=sqlite:///wa_task_app.sqlite
WA_TZ=Asia/Kolkata

▶️ **Run**

streamlit run task_assigner_app.py
Quickstart:
1. Contacts: add at least one contact.
2. Create Task: set first reminder time, pick frequency, choose “remind for N days.”
3. Tasks Board: change status, add comments, “Remind now,” or delete.
4. Jobs: inspect APScheduler jobs stored in the DB.

⏰ **Scheduling semantics**
* One interval job per task:
    * start_date = first reminder
    * days = freq_days
    * end_date = start_date + remind_for_days
* Completing or cancelling a task stops future reminders.
* Jobs persist in SQLite through APScheduler’s SQLAlchemy job store.
Want exactly N reminders instead of a day-window? Switch to creating N “date” jobs at task creation. It is a small change.

🗃️ **Data and persistence**
* SQLite file wa_task_app.sqlite holds contacts, tasks, comments, settings, and jobs.
* To reset everything: stop the app and delete the SQLite file.

🧪 **Troubleshooting**
* Task Board empty: ensure the board’s tab index matches the label order; also make sure you created a task after adding a contact.
* “Remind now” fails: the UI prints the raw WAHA response. Fix WAHA connectivity or the session.
* Time resets to now: avoid recomputing dynamic value= on every rerun. Use forms or st.session_state.
* Duplicate widget IDs: give repeated widgets a unique key.
* DetachedInstanceError: relationships are eager-loaded (lazy="joined") and queries use selectinload. Keep that if you refactor.

🔐 **Security**
* Protect your WAHA endpoint with auth and an allow-list if exposed to the internet.
* Avoid logging PII in production.

🗺️ **Roadmap**
* Multi-assignee tasks
* Escalations after N days
* Per-project templates
* Optional approvals
