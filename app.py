import os
from datetime import datetime, timedelta, time as dtime
from dateutil import tz

import streamlit as st
import requests
import pandas as pd
import phonenumbers

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, Text, ForeignKey, func, UniqueConstraint
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, selectinload

# --------------------
# Config
# --------------------
API_BASE     = os.getenv("WA_API_BASE", "http://localhost:3000").rstrip("/")
API_SEND     = os.getenv("WA_API_SEND", "/api/sendText")
API_SESSION  = os.getenv("WA_API_SESSION", "default")
DB_URL       = os.getenv("WA_DB_URL", "sqlite:///wa_task_app.sqlite")
LOCAL_TZ     = os.getenv("WA_TZ", "Asia/Kolkata")
HEADERS      = {"Accept": "application/json", "Content-Type": "application/json"}

st.set_page_config(page_title="Task Assigner (WhatsApp)", page_icon="✅", layout="wide")

# --------------------
# DB
# --------------------
@st.cache_resource
def _db():
    engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    return engine, SessionLocal

engine, SessionLocal = _db()
Base = declarative_base()

class Contact(Base):
    __tablename__ = "contacts"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    phone_raw = Column(String(64), nullable=False, default="")
    phone_e164 = Column(String(32), nullable=False)
    chat_id = Column(String(64), nullable=False)  # 9198...@c.us
    tags = Column(String(200), default="")
    note = Column(String(500), default="")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now(), server_default=func.now())
    __table_args__ = (UniqueConstraint("chat_id", name="uq_chatid"),)

class Setting(Base):
    __tablename__ = "settings"
    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False)

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, default="")
    status = Column(String(32), default="open")  # open, in_progress, completed, cancelled
    priority = Column(String(16), default="medium")  # low, medium, high
    due_at = Column(DateTime, nullable=True)

    assignee_id = Column(Integer, ForeignKey("contacts.id"), nullable=False)
    # Eager-load to avoid detached-instance issues in UI and jobs
    assignee = relationship("Contact", lazy="joined")

    start_at = Column(DateTime, nullable=False)   # first reminder datetime (tz-aware)
    freq_days = Column(Integer, default=1)        # 1=daily, 2=alternate, 3=every 3 days...
    remind_for_days = Column(Integer, default=5)  # stop after N days

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now(), server_default=func.now())

class TaskComment(Base):
    __tablename__ = "task_comments"
    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False)
    author = Column(String(120), default="admin")
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

Base.metadata.create_all(engine)

# --------------------
# Scheduler
# --------------------
@st.cache_resource
def _sched():
    stores = {"default": SQLAlchemyJobStore(url=DB_URL)}
    s = BackgroundScheduler(jobstores=stores, timezone=LOCAL_TZ)
    s.start()
    return s

scheduler = _sched()

def job_id_for_task(task_id: int) -> str:
    return f"task-{task_id}"

# --------------------
# Helpers
# --------------------
def to_e164(raw: str, region="IN") -> str | None:
    try:
        num = phonenumbers.parse(str(raw), region)
        if not phonenumbers.is_valid_number(num):
            return None
        return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
    except Exception:
        return None

def chat_id_from_e164(e164: str) -> str:
    digits = "".join([c for c in e164 if c.isdigit()])
    return f"{digits}@c.us"

def api_send_text(chat_id: str, text: str) -> dict:
    url = f"{API_BASE}{API_SEND}"
    payload = {"chatId": chat_id, "text": text, "session": API_SESSION}
    r = requests.post(url, json=payload, headers=HEADERS, timeout=20)
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        return {"ok": True, "raw": r.text}

def get_setting(key: str, default: str) -> str:
    s = SessionLocal()
    try:
        row = s.query(Setting).get(key)
        return row.value if row else default
    finally:
        s.close()

def set_setting(key: str, value: str):
    s = SessionLocal()
    try:
        row = s.query(Setting).get(key)
        if row:
            row.value = value
        else:
            row = Setting(key=key, value=value)
            s.add(row)
        s.commit()
    finally:
        s.close()

DEFAULT_TEMPLATE = "Hi {assignee_name}, what's the update on the task \"{title}\"? (Due: {due_date})"
if not get_setting("message_template", ""):
    set_setting("message_template", DEFAULT_TEMPLATE)

def render_message(task: Task, contact: Contact) -> str:
    template = get_setting("message_template", DEFAULT_TEMPLATE)
    due_str = task.due_at.astimezone(tz.gettz(LOCAL_TZ)).strftime("%d-%b-%Y %I:%M %p") if task.due_at else "N/A"
    return template.format(
        assignee_name=contact.name,
        title=task.title,
        description=(task.description or "")[:500],
        due_date=due_str,
        priority=task.priority,
        status=task.status
    )

# --------------------
# Reminder logic
# --------------------
def _send_task_ping(task_id: int) -> tuple[bool, str]:
    """Shared send path for jobs and 'Remind now'. Returns (ok, info)."""
    s = SessionLocal()
    try:
        t = s.query(Task).options(selectinload(Task.assignee)).get(task_id)
        if not t:
            return False, "Task not found"
        if t.status in ("completed", "cancelled"):
            try:
                scheduler.remove_job(job_id_for_task(task_id))
            except Exception:
                pass
            return False, f"Task is {t.status}; not sending"
        msg = render_message(t, t.assignee)
        chat_id = t.assignee.chat_id
    finally:
        s.close()

    try:
        resp = api_send_text(chat_id, msg)
        return True, str(resp)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"

def schedule_task(task: Task):
    jid = job_id_for_task(task.id)
    start = task.start_at
    tzinfo = tz.gettz(LOCAL_TZ)
    if start.tzinfo is None:
        start = start.replace(tzinfo=tzinfo)
    end = start + timedelta(days=max(0, task.remind_for_days))
    # Replace existing schedule
    try:
        scheduler.remove_job(jid)
    except Exception:
        pass
    scheduler.add_job(
        _send_task_ping, "interval",
        days=max(1, task.freq_days),
        start_date=start,
        end_date=end,
        args=[task.id],
        id=jid,
        coalesce=True,
        misfire_grace_time=900,  # 15 minutes
        replace_existing=True,
        max_instances=1,
    )

def cancel_task_schedule(task_id: int):
    try:
        scheduler.remove_job(job_id_for_task(task_id))
    except Exception:
        pass

# --------------------
# CRUD ops
# --------------------
def upsert_contact(name: str, phone: str, tags: str = "", note: str = "", region="IN") -> Contact:
    e164 = to_e164(phone, region)
    if not e164:
        raise ValueError("Invalid phone number")
    chat_id = chat_id_from_e164(e164)
    s = SessionLocal()
    try:
        c = s.query(Contact).filter_by(chat_id=chat_id).one_or_none()
        if c:
            c.name = name or c.name
            c.phone_raw = phone
            c.phone_e164 = e164
            c.tags = tags or c.tags
            c.note = note or c.note
        else:
            c = Contact(name=name, phone_raw=phone, phone_e164=e164, chat_id=chat_id, tags=tags or "", note=note or "")
            s.add(c)
        s.commit(); s.refresh(c); return c
    finally:
        s.close()

def list_contacts(search="", tag_contains="") -> list[Contact]:
    s = SessionLocal()
    try:
        q = s.query(Contact)
        if search:
            like = f"%{search.strip()}%"
            q = q.filter((Contact.name.ilike(like)) | (Contact.phone_e164.ilike(like)) | (Contact.note.ilike(like)))
        if tag_contains:
            like = f"%{tag_contains.strip()}%"
            q = q.filter(Contact.tags.ilike(like))
        return q.order_by(Contact.name.asc()).all()
    finally:
        s.close()

def create_task(title, description, assignee_id, priority, due_date, start_date, start_time, freq_days, remind_for_days):
    tzinfo = tz.gettz(LOCAL_TZ)
    start_dt = datetime.combine(start_date, start_time).replace(tzinfo=tzinfo)
    due_dt = None
    if due_date:
        due_dt = datetime.combine(due_date, dtime(18, 0)).replace(tzinfo=tzinfo)  # default 6pm if only date
    s = SessionLocal()
    try:
        t = Task(
            title=title.strip(), description=description.strip(),
            assignee_id=int(assignee_id), priority=priority, due_at=due_dt,
            start_at=start_dt, freq_days=int(freq_days), remind_for_days=int(remind_for_days),
            status="open"
        )
        s.add(t); s.commit(); s.refresh(t)
        schedule_task(t)
        return t
    finally:
        s.close()

def update_task_status(task_id: int, new_status: str):
    s = SessionLocal()
    try:
        t = s.query(Task).get(task_id)
        if not t: return False
        t.status = new_status
        s.add(TaskComment(task_id=task_id, author="system", body=f"Status changed to {new_status}"))
        s.commit()
        if new_status in ("completed", "cancelled"):
            cancel_task_schedule(task_id)
        else:
            schedule_task(t)
        return True
    finally:
        s.close()

def delete_task(task_id: int) -> bool:
    """Remove scheduled job, comments, and the task."""
    cancel_task_schedule(task_id)
    s = SessionLocal()
    try:
        t = s.query(Task).get(task_id)
        if not t:
            return False
        s.query(TaskComment).filter_by(task_id=task_id).delete()
        s.delete(t)
        s.commit()
        return True
    finally:
        s.close()

def add_comment(task_id: int, author: str, body: str):
    s = SessionLocal()
    try:
        s.add(TaskComment(task_id=task_id, author=author or "admin", body=body.strip()))
        s.commit()
    finally:
        s.close()

def send_now(task_id: int):
    """UI wrapper that surfaces success/errors from the shared sender."""
    ok, info = _send_task_ping(task_id)
    return ok, info

def list_tasks(status=None, assignee_id=None, search=""):
    s = SessionLocal()
    try:
        q = s.query(Task).options(selectinload(Task.assignee)).order_by(Task.created_at.desc())
        if status and status != "all":
            q = q.filter(Task.status == status)
        if assignee_id:
            q = q.filter(Task.assignee_id == int(assignee_id))
        if search:
            like = f"%{search.strip()}%"
            q = q.filter((Task.title.ilike(like)) | (Task.description.ilike(like)))
        tasks = q.all()
        # attach numbers of comments while session is open
        counts = dict(
            s.query(TaskComment.task_id, func.count(TaskComment.id))
             .group_by(TaskComment.task_id).all()
        )
        for t in tasks:
            t.comments_count = counts.get(t.id, 0)
        return tasks
    finally:
        s.close()

def get_comments(task_id: int):
    s = SessionLocal()
    try:
        return s.query(TaskComment).filter_by(task_id=task_id).order_by(TaskComment.created_at.asc()).all()
    finally:
        s.close()

# --------------------
# UI
# --------------------
tabs = st.tabs(["Tasks Board", "Create Task", "Contacts", "Jobs", "Settings"])



# Contacts
with tabs[2]:
    st.header("Contacts")
    with st.form("contact-form", clear_on_submit=True):
        c1, c2 = st.columns([2,2])
        name = c1.text_input("Name", "")
        phone = c2.text_input("Phone (any format; default IN)", "")
        tags  = st.text_input("Tags (comma-sep)", "")
        note  = st.text_area("Note", height=70)
        if st.form_submit_button("Save / Update"):
            try:
                c = upsert_contact(name, phone, tags, note)
                st.success(f"Saved: {c.name} • {c.phone_e164} • {c.chat_id}")
            except Exception as e:
                st.error(f"Failed: {e}")
    st.markdown("---")
    f1, f2, f3 = st.columns([2,2,1])
    q = f1.text_input("Search name/phone/note")
    tf = f2.text_input("Filter tag contains")
    if f3.button("Refresh", key="refresh_contacts"):
        st.rerun()
    rows = list_contacts(q, tf)
    if rows:
        df = pd.DataFrame([{
            "ID": r.id, "Name": r.name, "E.164": r.phone_e164, "chatId": r.chat_id,
            "Tags": r.tags, "Note": r.note
        } for r in rows])
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No contacts yet.")

# Create Task
with tabs[1]:
    st.header("Create Task")
    people = list_contacts()
    if not people:
        st.warning("Add contacts first.")
    else:
        with st.form("task-form", clear_on_submit=True):
            t1, t2 = st.columns([3,2])
            title = t1.text_input("Title", "")
            priority = t2.selectbox("Priority", ["low","medium","high"], index=1)

            description = st.text_area("Description", height=100)

            c1, c2, c3 = st.columns([2,2,2])
            assignee_label_to_id = {f"{p.name} ({p.phone_e164})": p.id for p in people}
            assignee_label = c1.selectbox("Assignee", list(assignee_label_to_id.keys()))
            due_date = c2.date_input("Due date (optional)", value=None)
            freq_days = c3.selectbox(
                "Reminder frequency",
                options=[1,2,3,5,7],
                index=0,
                format_func=lambda x: "Daily" if x==1 else f"Every {x} days"
            )

            d1, d2, d3 = st.columns([2,2,2])
            default_start = (datetime.now() + timedelta(minutes=5))
            start_date = d1.date_input("First reminder date", value=default_start.date())
            start_time = d2.time_input("First reminder time", value=default_start.time())
            remind_for_days = int(d3.number_input("Remind for N days", min_value=1, max_value=60, value=5))

            if st.form_submit_button("Create & Schedule"):
                try:
                    t = create_task(
                        title, description,
                        assignee_id=assignee_label_to_id[assignee_label],
                        priority=priority,
                        due_date=due_date,
                        start_date=start_date,
                        start_time=start_time,
                        freq_days=freq_days,
                        remind_for_days=remind_for_days
                    )
                    st.success(f"Task {t.id} created and scheduled.")
                except Exception as e:
                    st.error(f"Failed: {e}")

# Tasks Board
with tabs[0]:
    st.header("Tasks Board")
    frow = st.columns([2,2,2,2])
    status = frow[0].selectbox("Status", ["all","open","in_progress","completed","cancelled"], index=0)
    assignees = list_contacts()
    assn_map = {"All": None} | {f"{p.name} ({p.phone_e164})": p.id for p in assignees}
    assignee_pick = frow[1].selectbox("Assignee", list(assn_map.keys()), index=0)
    search = frow[2].text_input("Search title/description")
    if frow[3].button("Refresh", key="refresh_tasks"):
        st.rerun()

    tasks = list_tasks(status=None if status=="all" else status,
                       assignee_id=assn_map[assignee_pick],
                       search=search)

    if not tasks:
        st.info("No tasks found.")
    else:
        for t in tasks:
            with st.expander(f"#{t.id} • {t.title} • {t.priority.upper()} • {t.status} • Assignee: {t.assignee.name}"):
                ctop = st.columns([3,2,2,2,2])
                ctop[0].markdown(f"**Description:** {t.description or '-'}")
                ctop[1].markdown(f"**Due:** {t.due_at.astimezone(tz.gettz(LOCAL_TZ)).strftime('%d-%b-%Y %I:%M %p') if t.due_at else '-'}")
                ctop[2].markdown(f"**Start:** {t.start_at.astimezone(tz.gettz(LOCAL_TZ)).strftime('%d-%b-%Y %I:%M %p')}")
                ctop[3].markdown(f"**Freq:** every {t.freq_days} day(s)")
                ctop[4].markdown(f"**Window:** {t.remind_for_days} days")

                arow = st.columns([2,2,2,2,2])
                new_status = arow[0].selectbox("Change status", ["open","in_progress","completed","cancelled"],
                                               index=["open","in_progress","completed","cancelled"].index(t.status),
                                               key=f"status-{t.id}")
                if arow[1].button("Save status", key=f"save-{t.id}"):
                    if update_task_status(t.id, new_status):
                        st.success("Status updated.")
                    else:
                        st.error("Failed to update.")
                if arow[2].button("Remind now", key=f"now-{t.id}"):
                    ok, resp = send_now(t.id)
                    if ok:
                        st.success("Sent.")
                        st.code(resp)
                    else:
                        st.error("Failed to send.")
                        st.code(resp)

                # Delete controls (confirm + button)
                confirm_del = arow[3].checkbox("Confirm delete", key=f"confirm-del-{t.id}")
                if arow[4].button("Delete task", key=f"delete-{t.id}"):
                    if confirm_del:
                        if delete_task(t.id):
                            st.success("Task deleted.")
                            st.rerun()
                        else:
                            st.error("Task not found.")
                    else:
                        st.warning("Tick 'Confirm delete' first.")

                # Comments
                comments = get_comments(t.id)
                st.markdown("**Comments**")
                if comments:
                    for cm in comments:
                        st.write(f"- _{cm.author}_ @ {cm.created_at}: {cm.body}")
                with st.form(f"comment-{t.id}", clear_on_submit=True):
                    com_author, com_body = st.columns([1,5])
                    a = com_author.text_input("Author", value="admin")
                    b = com_body.text_input("Add a comment", "")
                    if st.form_submit_button("Add comment"):
                        if b.strip():
                            add_comment(t.id, a.strip(), b.strip())
                            st.success("Comment added.")
                        else:
                            st.error("Empty comment.")

# Jobs
with tabs[3]:
    st.header("Scheduled Jobs")
    jobs = scheduler.get_jobs()
    if not jobs:
        st.info("No jobs scheduled.")
    else:
        dfj = pd.DataFrame([{
            "id": j.id, "next_run_time": j.next_run_time, "trigger": str(j.trigger), "args": str(j.args)
        } for j in jobs])
        st.dataframe(dfj, use_container_width=True, hide_index=True)
        with st.form("cancel-job"):
            jid = st.text_input("Job ID to cancel")
            if st.form_submit_button("Cancel"):
                try:
                    scheduler.remove_job(jid)
                    st.success("Cancelled.")
                except Exception as e:
                    st.error(f"Failed: {e}")

# Settings
with tabs[4]:
    st.header("Settings")
    current_tpl = get_setting("message_template", DEFAULT_TEMPLATE)
    with st.form("tpl"):
        st.markdown("**Reminder template** (placeholders: `{assignee_name}`, `{title}`, `{description}`, `{due_date}`, `{priority}`, `{status}`)")
        tpl = st.text_area("Template", height=100, value=current_tpl)
        if st.form_submit_button("Save template"):
            set_setting("message_template", tpl.strip() or DEFAULT_TEMPLATE)
            st.success("Saved.")
    st.caption(f"API: {API_BASE}{API_SEND} • session={API_SESSION} • DB={DB_URL} • TZ={LOCAL_TZ}")
