# STAR LINK UAE VISA UPDATE V1

import os
import sys
import re
import csv
import io
import uuid
import hmac
import math
import secrets
import sqlite3
import hashlib
import base64
import getpass

from pathlib import Path
from functools import wraps
from datetime import datetime, date, time, timezone, timedelta

from flask import (
    Flask, render_template_string, request,
    redirect, url_for, session, flash,
    abort, g, Response, send_from_directory
)

from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)

from werkzeug.utils import secure_filename
from itsdangerous import URLSafeTimedSerializer, BadData
from PIL import Image, ImageOps, UnidentifiedImageError
import binascii


# =========================================================
# STAR LINK EMPLOYEE MANAGEMENT SYSTEM
# =========================================================

BASE = Path(__file__).resolve().parent
# DATA_DIR can point to a mounted Render persistent disk.
DATA_DIR = Path(os.getenv("DATA_DIR") or str(BASE)).expanduser().resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE = DATA_DIR / "sscm.db"
UPLOADS = DATA_DIR / "private_uploads"
LOGO_FILE = BASE / "star_link_logo.png"
SECRET_FILE = DATA_DIR / ".secret_key"

UPLOADS.mkdir(parents=True, exist_ok=True)

SECRET = os.getenv("SECRET_KEY")

if not SECRET:
    if os.getenv("RENDER") == "true":
        raise RuntimeError(
            "Set SECRET_KEY in Render Environment before deployment."
        )

    if not SECRET_FILE.exists():
        SECRET_FILE.write_text(
            secrets.token_hex(32),
            encoding="utf-8"
        )

    SECRET = SECRET_FILE.read_text(
        encoding="utf-8"
    ).strip()

app = Flask(__name__)

app.config.update(
    SECRET_KEY=SECRET,
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,
    MAX_FORM_MEMORY_SIZE=2_100_000,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("HTTPS_ONLY") == "1" or os.getenv("RENDER") == "true",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8)
)

ADMIN_EMAIL = os.getenv(
    "SSCM_ADMIN_EMAIL",
    "admin@company.com"
).lower()

LOGO = ""

if LOGO_FILE.exists():
    LOGO = (
        "data:image/png;base64,"
        + base64.b64encode(
            LOGO_FILE.read_bytes()
        ).decode()
    )

ALLOWED_FILES = {
    "pdf", "png", "jpg", "jpeg", "docx", "xlsx"
}


# =========================================================
# DATE AND TIME
# =========================================================

def now():
    return datetime.now(timezone(timedelta(hours=4)))


def today():
    return now().strftime("%Y-%m-%d")


def stamp():
    return now().strftime("%Y-%m-%d %H:%M:%S")


def valid_date(value):
    try:
        date.fromisoformat(value)
        return True
    except (ValueError, TypeError):
        return False


# =========================================================
# DATABASE
# =========================================================

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE, timeout=30)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
        g.db.execute("PRAGMA busy_timeout=30000")

    return g.db


@app.teardown_appcontext
def close_db(error=None):
    connection = g.pop("db", None)

    if connection is not None:
        connection.close()


def rows(sql, args=()):
    return db().execute(sql, args).fetchall()


def row(sql, args=()):
    return db().execute(sql, args).fetchone()


def save(sql, args=()):
    cursor = db().execute(sql, args)
    db().commit()
    return cursor.lastrowid


def count(sql, args=()):
    return row(sql, args)[0]


def insert(table, data):
    keys = list(data)
    placeholders = ",".join("?" for k in keys)

    return save(
        f"INSERT INTO {table} "
        f"({','.join(keys)}) VALUES ({placeholders})",
        tuple(data[k] for k in keys)
    )


def init_db():

    db().execute("PRAGMA journal_mode=WAL")

    db().executescript("""

    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        employee_code TEXT,
        department TEXT,
        designation TEXT,
        phone TEXT,
        role TEXT DEFAULT 'Employee',
        status TEXT DEFAULT 'Pending',
        created_at TEXT,
        joining_date TEXT,
        must_change_password INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        category TEXT,
        filename TEXT NOT NULL,
        original_name TEXT NOT NULL,
        expiry_date TEXT,
        status TEXT DEFAULT 'Pending',
        uploaded_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        attendance_date TEXT NOT NULL,
        check_in TEXT,
        check_out TEXT,
        UNIQUE(user_id,attendance_date),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS attendance_photos (
        attendance_id INTEGER NOT NULL,
        action TEXT NOT NULL CHECK(action IN ('in','out')),
        jpeg BLOB NOT NULL,
        captured_at TEXT NOT NULL,
        PRIMARY KEY(attendance_id,action),
        FOREIGN KEY(attendance_id) REFERENCES attendance(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        description TEXT,
        deadline TEXT,
        priority TEXT DEFAULT 'Medium',
        status TEXT DEFAULT 'Pending',
        created_by INTEGER,
        created_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS leaves (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        leave_type TEXT,
        start_date TEXT,
        end_date TEXT,
        reason TEXT,
        status TEXT DEFAULT 'Pending',
        created_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS support (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        category TEXT,
        subject TEXT,
        message TEXT,
        reply TEXT,
        status TEXT DEFAULT 'Open',
        created_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        category TEXT,
        subject TEXT NOT NULL,
        description TEXT,
        priority TEXT DEFAULT 'Medium',
        status TEXT DEFAULT 'Open',
        reply TEXT,
        assigned_to TEXT,
        created_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS announcements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        message TEXT NOT NULL,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS employee_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        module TEXT NOT NULL,
        title TEXT NOT NULL,
        details TEXT,
        record_date TEXT,
        end_date TEXT,
        amount REAL DEFAULT 0,
        status TEXT DEFAULT 'Pending',
        created_by INTEGER,
        created_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS departments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        manager TEXT,
        description TEXT
    );

    CREATE TABLE IF NOT EXISTS job_roles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        description TEXT
    );

    CREATE TABLE IF NOT EXISTS employee_roles (
        user_id INTEGER PRIMARY KEY,
        role_id INTEGER,
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(role_id) REFERENCES job_roles(id)
    );

    CREATE TABLE IF NOT EXISTS access_control (
        user_id INTEGER NOT NULL,
        module TEXT NOT NULL,
        can_view INTEGER DEFAULT 0,
        can_create INTEGER DEFAULT 0,
        can_edit INTEGER DEFAULT 0,
        can_delete INTEGER DEFAULT 0,
        updated_by INTEGER,
        updated_at TEXT,
        PRIMARY KEY(user_id,module),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        action TEXT,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS approval_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        module TEXT,
        record_id INTEGER,
        employee_id INTEGER,
        decision TEXT,
        decided_by INTEGER,
        remarks TEXT,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS preferences (
        user_id INTEGER PRIMARY KEY,
        theme TEXT DEFAULT 'Light',
        language TEXT DEFAULT 'English',
        timezone TEXT DEFAULT 'Asia/Dubai',
        date_format TEXT DEFAULT '%d %B %Y',
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE INDEX IF NOT EXISTS idx_records_module
    ON employee_records(module,user_id);

    CREATE INDEX IF NOT EXISTS idx_attendance_date
    ON attendance(attendance_date);

    CREATE INDEX IF NOT EXISTS idx_documents_user
    ON documents(user_id);

    CREATE INDEX IF NOT EXISTS idx_tasks_user
    ON tasks(user_id);

    CREATE INDEX IF NOT EXISTS idx_tickets_user
    ON tickets(user_id);

    """)

    columns = {
        item[1]
        for item in db().execute("PRAGMA table_info(users)")
    }

    if "joining_date" not in columns:
        db().execute(
            "ALTER TABLE users ADD COLUMN joining_date TEXT"
        )

    if "must_change_password" not in columns:
        db().execute("""
            ALTER TABLE users ADD COLUMN
            must_change_password INTEGER DEFAULT 0
        """)

    for department in [
        "Human Resources", "IT", "Finance",
        "Operations", "Sales", "Marketing",
        "Administration", "Customer Service",
        "Logistics", "Transportation",
        "Security", "Other"
    ]:
        db().execute(
            "INSERT OR IGNORE INTO departments(name) VALUES (?)",
            (department,)
        )

    db().execute("""
        INSERT OR IGNORE INTO departments(name)
        SELECT DISTINCT department FROM users
        WHERE department IS NOT NULL AND department!=''
    """)

    for role_name in [
        "Employee", "Manager", "Supervisor",
        "Team Leader", "HR Officer"
    ]:
        db().execute(
            "INSERT OR IGNORE INTO job_roles(name) VALUES (?)",
            (role_name,)
        )

    db().commit()

    if not row("SELECT id FROM users WHERE role='Admin'"):

        password = os.getenv("SSCM_ADMIN_PASSWORD")

        if not password and os.getenv("RENDER") == "true":
            raise RuntimeError("Set SSCM_ADMIN_PASSWORD in Render Environment.")

        password = password or secrets.token_urlsafe(18)

        insert("users", {
            "name": "HR Administrator",
            "email": ADMIN_EMAIL,
            "password": generate_password_hash(password),
            "employee_code": "SL-HR-001",
            "department": "Human Resources",
            "designation": "HR Administrator",
            "role": "Admin",
            "status": "Approved",
            "created_at": stamp()
        })

        print("HR ADMIN ACCOUNT CREATED")
        print("Email:", ADMIN_EMAIL)
        if os.getenv("RENDER") != "true":
            print("Password:", password)


# =========================================================
# MODULE CONFIGURATION
# =========================================================

def F(name, label, kind="text", required=True, options=()):
    return {
        "name": name,
        "label": label,
        "kind": kind,
        "required": required,
        "options": options
    }


MODULES = {}

PRIORITIES = ["Low", "Medium", "High"]

DOCUMENT_TYPES = [
    "Passport", "Emirates ID", "Residence Visa",
    "Employment Contract", "Medical Insurance",
    "Educational Certificate", "Driving License",
    "Work Permit", "Training Certificate",
    "Company Document", "Other"
]

LEAVE_TYPES = [
    "Annual Leave", "Sick Leave",
    "Emergency Leave", "Maternity Leave",
    "Paternity Leave", "Unpaid Leave", "Other"
]

SUPPORT_TYPES = [
    "HR Assistance", "Salary Concern",
    "Employee Benefits", "Workplace Complaint",
    "Medical Insurance", "Accommodation",
    "Transportation", "IT Support", "Other"
]


def register(
    key, title, table, fields, icon, color,
    actions=("view", "create", "edit", "delete"),
    statuses=None, initial="Pending", hr_only=False
):
    MODULES[key] = {
        "title": title,
        "table": table,
        "fields": fields,
        "icon": icon,
        "color": color,
        "actions": actions,
        "statuses": statuses or [
            "Pending", "Approved",
            "Rejected", "Completed"
        ],
        "initial": initial,
        "hr_only": hr_only
    }


register(
    "documents", "Documents", "documents",
    [
        F("title", "Document Name"),
        F("category", "Document Type",
          "select", True, DOCUMENT_TYPES),
        F("expiry_date", "Expiry Date", "date", False)
    ],
    "file", "red",
    statuses=["Pending", "Verified", "Rejected"]
)

register(
    "tasks", "Tasks & To-Do", "tasks",
    [
        F("title", "Task Title"),
        F("description", "Description", "textarea", False),
        F("deadline", "Deadline", "date", False),
        F("priority", "Priority", "select", True, PRIORITIES)
    ],
    "tasks", "pink",
    statuses=["Pending", "In Progress", "Completed"]
)

register(
    "attendance", "Attendance", "attendance",
    [
        F("attendance_date", "Attendance Date", "date"),
        F("check_in", "Check In", "time"),
        F("check_out", "Check Out", "time", False)
    ],
    "clock", "teal",
    actions=("view", "create")
)

register(
    "leaves", "Vacations", "leaves",
    [
        F("leave_type", "Leave Type",
          "select", True, LEAVE_TYPES),
        F("start_date", "Start Date", "date"),
        F("end_date", "End Date", "date"),
        F("reason", "Reason", "textarea")
    ],
    "calendar", "green",
    statuses=["Pending", "Approved", "Rejected"]
)

register(
    "support", "Internal Support", "support",
    [
        F("category", "Category",
          "select", True, SUPPORT_TYPES),
        F("subject", "Subject"),
        F("message", "Description", "textarea")
    ],
    "support", "pink",
    initial="Open",
    statuses=["Open", "In Progress", "Resolved"]
)

register(
    "tickets", "Ticketing System", "tickets",
    [
        F("category", "Department", "select", True, [
            "HR", "IT", "Finance", "Operations",
            "Maintenance", "Transportation",
            "Access Request", "Employee Complaint", "Other"
        ]),
        F("subject", "Ticket Subject"),
        F("description", "Description", "textarea"),
        F("priority", "Priority", "select", True, PRIORITIES)
    ],
    "ticket", "red",
    initial="Open",
    statuses=[
        "Open", "Pending", "Approved",
        "In Progress", "Resolved", "Rejected"
    ]
)


ADVANCED = [
    ("onboarding", "Onboarding", "Onboarding Activity",
     "", "tasks", "green", False, False),

    ("timesheets", "Timesheets", "Project / Work Title",
     "Hours Worked", "clock", "teal", True, False),

    ("extra-work", "Extra Work", "Overtime Assignment",
     "Overtime Hours", "clock", "orange", True, False),

    ("assets", "Assets", "Asset Name",
     "Quantity", "box", "teal", False, False),

    ("kpi", "KPI", "KPI Name",
     "Score / 100", "target", "red", False, False),

    ("performance", "Performance", "Review Title",
     "Score / 100", "chart", "red", False, False),

    ("skills", "Skills", "Skill Name",
     "", "star", "pink", True, False),

    ("contracts", "Contracts", "Contract Reference",
     "", "file", "green", False, False),

    ("trainings", "Trainings", "Training Course",
     "Training Hours", "graduation", "teal", False, False),

    ("emergency", "Emergency Contacts", "Contact Name",
     "", "phone", "red", True, False),

    ("hr-notes", "Confidential HR Notes", "Note Title",
     "", "lock", "purple", False, True),

    ("transportation", "Transportation", "Transport Route",
     "", "bus", "teal", True, False),

    ("team-calendar", "Team Calendar", "Event Name",
     "", "calendar", "purple", False, False)
]

for key, title, label, amount, symbol, color, writable, restricted in ADVANCED:

    fields = [
        F("title", label),
        F("details", "Details", "textarea", False),
        F("record_date", "Record / Start Date", "date"),
        F("end_date", "End / Expiry Date", "date", False)
    ]

    if amount:
        fields.append(F("amount", amount, "number"))

    register(
        key, title, "employee_records",
        fields, symbol, color,
        actions=(
            ("view", "create", "edit", "delete")
            if writable else ("view",)
        ),
        hr_only=restricted,
        statuses=[
            "Pending", "Approved", "Rejected",
            "In Progress", "Completed"
        ]
    )


# =========================================================
# HR PERMISSION SYSTEM
# =========================================================

PERMISSION_NAMES = {
    key: config["title"]
    for key, config in MODULES.items()
    if not config["hr_only"]
}

PERMISSION_NAMES.update({
    "profile": "My Profile",
    "settings": "Personal Settings",
    "timeline": "Employee Timeline",
    "announcements": "Company Communication",
    "scores": "Score Breakdown",
    "work-hours": "Work Hours",
    "task-performance": "Task Performance"
})

ACTION_COLUMNS = {
    "view": "can_view",
    "create": "can_create",
    "edit": "can_edit",
    "delete": "can_delete"
}




def supported_actions(module):

    # STAR LINK HR PERMISSIONS V3

    if module in MODULES:

        if MODULES[module]["hr_only"]:
            return ()

        return (
            "view",
            "create",
            "edit",
            "delete"
        )

    if module in {"profile", "settings"}:

        return ("view", "edit")

    return ("view",)


def admin():
    return bool(g.user and g.user["role"] == "Admin")


def allowed(module, action="view"):

    if not g.user or action not in ACTION_COLUMNS:
        return False

    if admin():
        return True

    if module not in PERMISSION_NAMES:
        return False

    if action not in supported_actions(module):
        return False

    permission = g.permissions.get(module)

    if not permission or not permission["can_view"]:
        return False

    return bool(permission[ACTION_COLUMNS[action]])


def require(module, action="view"):

    if not allowed(module, action):
        abort(403)


def field(name, limit=5000):
    return request.form.get(name, "").strip()[:limit]


def csrf():

    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(32)

    return session["csrf"]


def signature(password_hash):
    return hashlib.sha256(password_hash.encode()).hexdigest()


def audit(action):

    insert("audit_logs", {
        "user_id": g.user["id"] if g.user else None,
        "action": action,
        "created_at": stamp()
    })


def decision(module, record_id, uid, status):

    insert("approval_history", {
        "module": module,
        "record_id": record_id,
        "employee_id": uid,
        "decision": status,
        "decided_by": g.user["id"],
        "created_at": stamp()
    })

    audit(f"{module} #{record_id}: {status}")


@app.before_request
def load_user():

    g.user = None
    g.permissions = {}

    if session.get("user_id"):

        user = row(
            "SELECT * FROM users WHERE id=?",
            (session["user_id"],)
        )

        if (
            user
            and user["status"] == "Approved"
            and session.get("auth_signature")
            == signature(user["password"])
        ):

            g.user = user

            g.permissions = {
                item["module"]: dict(item)
                for item in rows("""
                    SELECT * FROM access_control
                    WHERE user_id=?
                """, (user["id"],))
            }

        else:
            session.clear()

    if request.method == "POST":

        expected = session.get("csrf", "")
        supplied = request.form.get("csrf", "")

        if not expected or not hmac.compare_digest(
            expected, supplied
        ):
            abort(400)

    if (
        g.user
        and g.user["must_change_password"]
        and request.endpoint not in {
            "password_settings", "logout"
        }
    ):
        return redirect(url_for("password_settings"))


def login_required(function):

    @wraps(function)
    def wrapper(*args, **kwargs):

        if not g.user:
            return redirect(url_for("login"))

        return function(*args, **kwargs)

    return wrapper


def admin_required(function):

    @wraps(function)
    def wrapper(*args, **kwargs):

        if not g.user:
            return redirect(url_for("login"))

        if not admin():
            abort(403)

        return function(*args, **kwargs)

    return wrapper


def staff_list():
    return rows("""
        SELECT id,name,employee_code,department
        FROM users
        WHERE status='Approved'
        ORDER BY name
    """)


def selected_employee():

    if not admin():
        return g.user["id"]

    uid = request.form.get("user_id", type=int)

    if not row("""
        SELECT id FROM users
        WHERE id=? AND status='Approved'
    """, (uid,)):
        abort(400)

    return uid


def save_permissions(uid, connection=None):

    connection = connection or db()

    for module in PERMISSION_NAMES:

        supported = supported_actions(module)

        view = request.form.get(module + "_view") == "1"

        flags = [
            int(
                view
                and action in supported
                and request.form.get(
                    module + "_" + action
                ) == "1"
            )
            for action in ["create", "edit", "delete"]
        ]

        connection.execute("""

            INSERT INTO access_control
            (
                user_id,module,can_view,can_create,
                can_edit,can_delete,updated_by,updated_at
            )
            VALUES (?,?,?,?,?,?,?,?)

            ON CONFLICT(user_id,module)
            DO UPDATE SET
                can_view=excluded.can_view,
                can_create=excluded.can_create,
                can_edit=excluded.can_edit,
                can_delete=excluded.can_delete,
                updated_by=excluded.updated_by,
                updated_at=excluded.updated_at

        """, (
            uid, module, int(view),
            *flags, g.user["id"], stamp()
        ))


# =========================================================
# DASHBOARD ICONS
# =========================================================

ICONS = {

    "file": """
        <path d="M25 8h38l20 20v64H25z" fill="currentColor"/>
        <path d="M63 8v22h20" fill="white" opacity=".5"/>
        <path d="M37 44h32M37 57h32M37 70h23"
        stroke="white" stroke-width="5" stroke-linecap="round"/>
    """,

    "tasks": """
        <rect x="22" y="15" width="60" height="78"
        rx="6" fill="currentColor"/>
        <rect x="36" y="5" width="32" height="20"
        rx="5" fill="currentColor"/>
        <path d="M31 42l7 7 13-15M31 67l7 7 13-15"
        stroke="white" stroke-width="5" fill="none"/>
        <path d="M56 43h15M56 68h15"
        stroke="white" stroke-width="5"/>
    """,

    "clock": """
        <circle cx="50" cy="50" r="44" fill="currentColor"/>
        <circle cx="50" cy="50" r="33" fill="white"/>
        <path d="M50 25v27l20 12"
        stroke="currentColor" stroke-width="7"
        fill="none" stroke-linecap="round"/>
    """,

    "users": """
        <circle cx="50" cy="24" r="19" fill="currentColor"/>
        <circle cx="17" cy="31" r="14"
        fill="currentColor" opacity=".55"/>
        <circle cx="83" cy="31" r="14"
        fill="currentColor" opacity=".55"/>
        <path d="M28 94V61a22 22 0 0 1 44 0v33z"
        fill="currentColor"/>
        <path d="M2 90V61a16 16 0 0 1 21-15v44z
        M77 90V46a16 16 0 0 1 21 15v29z"
        fill="currentColor" opacity=".6"/>
    """,

    "user": """
        <circle cx="50" cy="28" r="23" fill="currentColor"/>
        <path d="M10 94V80a40 40 0 0 1 80 0v14z"
        fill="currentColor"/>
    """,

    "add-user": """
        <circle cx="38" cy="24" r="19" fill="currentColor"/>
        <path d="M5 94V75a33 33 0 0 1 66 0v19z"
        fill="currentColor"/>
        <path d="M82 23v34M65 40h34"
        stroke="currentColor" stroke-width="9"
        stroke-linecap="round"/>
    """,

    "mail": """
        <rect x="6" y="18" width="88" height="65"
        rx="2" fill="currentColor"/>
        <path d="M6 22L50 54 94 22"
        fill="none" stroke="white" stroke-width="5"/>
    """,

    "alert": """
        <path d="M43 12q7-12 14 0l38 66
        q7 12-7 12H12q-14 0-7-12z"
        fill="currentColor"/>
        <path d="M44 31h12l-3 30h-6z" fill="white"/>
        <circle cx="50" cy="74" r="6" fill="white"/>
    """,

    "chart": """
        <rect x="8" y="57" width="16" height="30"
        fill="currentColor" opacity=".5"/>
        <rect x="30" y="42" width="16" height="45"
        fill="currentColor" opacity=".7"/>
        <rect x="52" y="25" width="16" height="62"
        fill="currentColor"/>
        <rect x="74" y="9" width="16" height="78"
        fill="currentColor" opacity=".8"/>
        <rect x="7" y="91" width="84" height="6"
        fill="currentColor"/>
    """,

    "calendar": """
        <rect x="9" y="15" width="82" height="77"
        rx="6" fill="currentColor"/>
        <rect x="18" y="37" width="64" height="47"
        fill="white"/>
        <path d="M28 7v19M72 7v19"
        stroke="currentColor" stroke-width="9"
        stroke-linecap="round"/>
        <path d="M30 60l13 13 27-26"
        stroke="currentColor" stroke-width="8" fill="none"/>
    """,

    "support": """
        <circle cx="55" cy="18" r="15" fill="currentColor"/>
        <circle cx="28" cy="34" r="12"
        fill="currentColor" opacity=".6"/>
        <path d="M36 66V49a20 20 0 0 1 40 0v20z"
        fill="currentColor"/>
        <path d="M14 65l18 12 28-4 14-15q9-7 15 1
        L64 91H31L5 73z" fill="currentColor" opacity=".6"/>
    """,

    "ticket": """
        <path d="M8 20h84v21a10 10 0 0 0 0 20v20H8V61
        a10 10 0 0 0 0-20z" fill="currentColor"/>
        <path d="M68 24v9M68 40v9M68 56v9M68 72v6"
        stroke="white" stroke-width="4"/>
        <path d="M23 43h30M23 56h25"
        stroke="white" stroke-width="5"/>
    """,

    "lock": """
        <path d="M28 45V28a22 22 0 0 1 44 0v17"
        fill="none" stroke="currentColor" stroke-width="12"/>
        <rect x="13" y="40" width="74" height="54"
        rx="8" fill="currentColor"/>
        <circle cx="50" cy="60" r="8" fill="white"/>
        <path d="M46 63h8v17h-8z" fill="white"/>
    """,

    "building": """
        <rect x="17" y="7" width="66" height="87"
        rx="3" fill="currentColor"/>
        <path d="M28 20h13v13H28zM59 20h13v13H59z
        M28 42h13v13H28zM59 42h13v13H59z
        M28 64h13v13H28zM59 64h13v13H59z
        M43 76h14v18H43z" fill="white"/>
    """,

    "graduation": """
        <path d="M2 35L50 12 98 35 50 58z" fill="currentColor"/>
        <path d="M24 50v25q26 20 52 0V50"
        fill="currentColor" opacity=".8"/>
        <path d="M86 40v38" stroke="currentColor" stroke-width="5"/>
        <circle cx="86" cy="82" r="5" fill="currentColor"/>
    """,

    "box": """
        <path d="M8 25L50 6 92 25v49L50 94 8 74z"
        fill="currentColor"/>
        <path d="M8 25l42 22 42-22M50 47v47"
        stroke="white" stroke-width="4" fill="none"/>
    """,

    "target": """
        <circle cx="50" cy="50" r="44" fill="currentColor"/>
        <circle cx="50" cy="50" r="32" fill="white"/>
        <circle cx="50" cy="50" r="22" fill="currentColor"/>
        <circle cx="50" cy="50" r="11" fill="white"/>
        <circle cx="50" cy="50" r="5" fill="currentColor"/>
    """,

    "star": """
        <path d="M50 4l14 30 33 5-24 24
        6 33-29-16-29 16 6-33L3 39l33-5z"
        fill="currentColor"/>
    """,

    "phone": """
        <path d="M17 6h22l11 26-16 10q9 20 29 29
        l10-16 24 10v23q-8 21-35 4C19 69-8 27 17 6z"
        fill="currentColor"/>
    """,

    "bus": """
        <rect x="10" y="6" width="80" height="79"
        rx="12" fill="currentColor"/>
        <rect x="18" y="20" width="64" height="33"
        rx="4" fill="white"/>
        <path d="M21 85v12M79 85v12"
        stroke="currentColor" stroke-width="12"/>
        <circle cx="25" cy="70" r="7" fill="white"/>
        <circle cx="75" cy="70" r="7" fill="white"/>
    """,

    "check": """
        <circle cx="50" cy="50" r="44" fill="currentColor"/>
        <path d="M25 50l17 18 34-37" fill="none"
        stroke="white" stroke-width="10"
        stroke-linecap="round" stroke-linejoin="round"/>
    """,

    "settings": """
        <g fill="currentColor">
        <circle cx="50" cy="50" r="34"/>
        <rect x="40" y="2" width="20" height="96" rx="4"/>
        <rect x="2" y="40" width="96" height="20" rx="4"/>
        <rect x="40" y="2" width="20" height="96"
        transform="rotate(45 50 50)"/>
        <rect x="40" y="2" width="20" height="96"
        transform="rotate(-45 50 50)"/>
        </g>
        <circle cx="50" cy="50" r="17" fill="white"/>
    """
}


# =========================================================
# NAVIGATION MENU
# =========================================================

GROUPS = [

    ("My Portal", [
        ("Onboarding", "module_page", "onboarding"),
        ("My Tasks", "module_page", "tasks"),
        ("Timesheets", "module_page", "timesheets"),
        ("Internal Support", "module_page", "support"),
        ("Vacations", "module_page", "leaves"),
        ("Extra Work", "module_page", "extra-work"),
        ("Profile", "profile", "profile"),
        ("Language", "preferences", "settings")
    ], False),

    ("Admin Portal", [
        ("Add Employee", "add_employee", ""),
        ("Workforce Management", "workforce", ""),
        ("Approvals", "approvals", ""),
        ("Approval History", "approval_history", ""),
        ("Approved Requests", "approved", ""),
        ("Tasks", "module_page", "tasks"),
        ("Assets", "module_page", "assets"),
        ("Roles", "roles", ""),
        ("KPI", "module_page", "kpi"),
        ("Analytics", "analytics", ""),
        ("Onboarding", "module_page", "onboarding"),
        ("Reports", "reports", ""),
        ("Team Calendar", "calendar_page", "")
    ], True),

    ("Permissions", [
        ("Employee Permissions", "permissions", "")
    ], True),

    ("Departments", [
        ("Departments", "departments", "")
    ], True),

    ("Ticketing System", [
        ("Create Ticket", "create_ticket", "tickets"),
        ("All Tickets", "module_page", "tickets"),
        ("Ticket Status", "ticket_status", "tickets")
    ], False),

    ("HR Ticket Administration", [
        ("Ticket Approvals", "ticket_approvals", ""),
        ("Audit Trail", "audit_trail", "")
    ], True),

    ("Dashboards", [
        ("Performance", "module_page", "performance"),
        ("Score Breakdown", "scores", "scores"),
        ("Transportation", "module_page", "transportation"),
        ("Communication", "communication", "announcements")
    ], False),

    ("HR Dashboards", [
        ("HR Analytics", "analytics", ""),
        ("Reports", "reports", "")
    ], True),

    ("Employee Records", [
        ("Timeline", "timeline", "timeline"),
        ("Documents", "module_page", "documents"),
        ("Skills", "module_page", "skills"),
        ("Performance", "module_page", "performance"),
        ("Contracts", "module_page", "contracts"),
        ("Assets", "module_page", "assets"),
        ("Trainings", "module_page", "trainings"),
        ("Attendance", "module_page", "attendance"),
        ("Emergency Contacts", "module_page", "emergency"),
        ("Vacations", "module_page", "leaves"),
        ("Task Performance", "task_performance", "task-performance"),
        ("Work Hours", "work_hours", "work-hours"),
        ("Internal Support", "module_page", "support")
    ], False),

    ("Confidential Records", [
        ("HR Notes", "module_page", "hr-notes")
    ], True),

    ("Settings", [
        ("Theme Settings", "preferences", "settings"),
        ("Time & Date", "preferences", "settings"),
        ("Language", "preferences", "settings"),
        ("Change Password", "password_settings", "")
    ], False)

]


SPECIAL_ICONS = {
    "add_employee": ("add-user", "green"),
    "workforce": ("users", "pink"),
    "permissions": ("lock", "purple"),
    "departments": ("building", "teal"),
    "analytics": ("chart", "red"),
    "communication": ("mail", "red"),
    "approvals": ("check", "green"),
    "approval_history": ("clock", "purple"),
    "approved": ("check", "green"),
    "reports": ("file", "teal"),
    "roles": ("users", "purple"),
    "scores": ("target", "red"),
    "profile": ("user", "pink"),
    "preferences": ("settings", "purple"),
    "ticket_status": ("alert", "pink"),
    "create_ticket": ("ticket", "pink"),
    "calendar_page": ("calendar", "teal"),
    "timeline": ("clock", "purple")
}


def visible_menu():

    if not g.user:
        return []

    if g.user["must_change_password"]:
        return []

    result = []

    for group_name, entries, hr_only in GROUPS:

        if hr_only and not admin():
            continue

        items = []

        for label, endpoint, module in entries:

            if module and not allowed(module):
                continue

            if endpoint == "create_ticket":
                if not allowed("tickets", "create"):
                    continue

            if endpoint == "scores":
                if not all(allowed(m) for m in [
                    "tasks", "performance", "kpi"
                ]):
                    continue

            if endpoint == "work_hours":
                if not all(allowed(m) for m in [
                    "attendance", "timesheets", "extra-work"
                ]):
                    continue

            if endpoint == "task_performance":
                if not allowed("tasks"):
                    continue

            if endpoint == "module_page":

                link = url_for(endpoint, module=module)
                icon = MODULES[module]["icon"]
                color = MODULES[module]["color"]

            else:

                link = url_for(endpoint)
                icon, color = SPECIAL_ICONS.get(
                    endpoint, ("file", "teal")
                )

            items.append({
                "label": label,
                "link": link,
                "icon": icon,
                "color": color
            })

        if items:
            result.append({
                "name": group_name,
                "items": items
            })

    return result


def dashboard_tiles():

    tiles = []
    seen = set()

    for group in visible_menu():

        for item in group["items"]:

            if item["link"] not in seen:

                if item["label"] != "Change Password":
                    tiles.append(item)

                seen.add(item["link"])

    order = [
        "My Tasks", "Attendance", "Communication",
        "Ticket Status", "Workforce Management",
        "Performance", "Documents", "Trainings",
        "Internal Support", "Add Employee",
        "Employee Permissions", "Timesheets",
        "Vacations", "HR Analytics", "Assets",
        "Transportation"
    ]

    tiles.sort(
        key=lambda item: (
            order.index(item["label"])
            if item["label"] in order else 100
        )
    )

    return tiles


def prefs():

    defaults = {
        "theme": "Light",
        "language": "English",
        "timezone": "Asia/Dubai",
        "date_format": "%d %B %Y"
    }

    if not g.user:
        return defaults

    item = row("""
        SELECT * FROM preferences WHERE user_id=?
    """, (g.user["id"],))

    return dict(item) if item else defaults


app.jinja_env.globals.update(
    csrf=csrf,
    admin=admin,
    allowed=allowed,
    supported_actions=supported_actions
)


# =========================================================
# PROFESSIONAL WEBSITE DESIGN
# NO BLACK LOGO BACKGROUND
# =========================================================

CSS = """


/* STAR LINK ACCOUNT AND DOCUMENT MANAGEMENT */

.saved-document-card {
    background: #f9f8fb;
    border: 1px solid #dedce6;
    border-radius: 10px;
    padding: 22px;
    margin: 16px 0;
}

.saved-document-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 15px;
}

.saved-document-header h3 {
    color: #30254f;
    margin: 0 0 10px;
}

.saved-document-header p {
    margin: 7px 0;
}

.saved-document-success {
    color: #347c49;
    font-size: 13px;
    font-weight: 600;
}

.document-renewal {
    margin-top: 18px;
}

.document-renewal summary {
    display: inline-block;
    padding: 10px 16px;
    background: #ffffff;
    border: 1px solid #d8d5e1;
    border-radius: 7px;
    color: #30254f;
    font-size: 13px;
    font-weight: 650;
    cursor: pointer;
}

.document-renewal[open] summary {
    margin-bottom: 20px;
}

.document-renewal summary:hover {
    background: #f0edf7;
}

.account-reset-card {
    max-width: 850px;
    margin: 0 auto;
}

.account-reset-details {
    background: #f3f0f9;
    border: 1px solid #ded6ed;
    border-radius: 8px;
    padding: 18px;
    margin: 20px 0;
}

.account-reset-details p {
    margin: 8px 0;
}

.account-reset-error {
    background: #fff0f0;
    border: 1px solid #efc7cc;
    color: #b42318;
    padding: 13px;
    border-radius: 7px;
}

.account-reset-checkbox {
    display: flex;
    align-items: center;
    gap: 10px;
    margin: 20px 0;
}

.account-reset-checkbox input {
    width: 18px;
    height: 18px;
}

.account-reset-notice {
    background: #f8f7fb;
    border: 1px solid #dedce6;
    border-radius: 8px;
    padding: 15px;
    margin: 20px 0;
    line-height: 1.6;
}

@media (max-width: 650px) {

    .saved-document-card {
        padding: 16px;
    }

    .saved-document-header {
        flex-direction: column;
    }

    .account-reset-card {
        width: 100%;
    }

}




/* STAR LINK PERSONAL DOCUMENT DESIGN */

.identity-upload-section {
    margin-top: 32px;
    padding-top: 25px;
    border-top: 1px solid #e1dfe8;
}

.identity-section-heading {
    margin-bottom: 22px;
}

.identity-upload-card {
    background: #f9f8fb;
    border: 1px solid #dedce6;
    border-radius: 10px;
    padding: 22px;
    margin-bottom: 18px;
}

.identity-document-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 12px;
    margin-bottom: 20px;
}

.identity-document-heading h3 {
    color: #30254f;
    font-size: 17px;
    margin: 0 0 7px;
}

.identity-upload-grid {
    display: grid;
    grid-template-columns: repeat(2,minmax(0,1fr));
    gap: 22px;
}

.identity-upload-grid label {
    color: #17191d;
    font-weight: 650;
    margin-bottom: 9px;
}

.identity-upload-grid input {
    width: 100%;
    background: white;
    min-height: 46px;
    border: 1px solid #d8d8e2;
    border-radius: 7px;
    padding: 12px;
}

.identity-upload-grid input[type=file] {
    border-style: dashed;
    cursor: pointer;
}

.identity-upload-grid input::file-selector-button {
    border: 0;
    background: #30254f;
    color: white;
    padding: 9px 12px;
    border-radius: 5px;
    margin-right: 10px;
    cursor: pointer;
}

.identity-upload-grid small {
    display: block;
    margin-top: 8px;
    color: #667080;
}

.identity-notice {
    background: #f0edf7;
    border: 1px solid #ded6ed;
    border-radius: 8px;
    padding: 16px;
    color: #30254f;
    line-height: 1.6;
    margin-top: 20px;
}

.identity-document-table {
    width: 100%;
    min-width: 680px;
    border-collapse: collapse;
}

.identity-document-table th {
    background: #f3f1f7;
    color: #30254f;
    font-weight: 700;
    text-align: left;
    padding: 15px;
}

.identity-document-table td {
    white-space: normal !important;
    vertical-align: middle !important;
    padding: 15px !important;
    border-bottom: 1px solid #e7e6ed;
    color: #17191d;
}

.identity-document-table tbody tr:hover {
    background: #f8f7fb;
}

@media (max-width: 650px) {

    .identity-upload-grid {
        grid-template-columns: 1fr;
    }

    .identity-upload-card {
        padding: 16px;
    }

    .identity-document-heading {
        align-items: flex-start;
    }

}


[hidden] { display: none !important; }



/* HEADER PROFILE PHOTOGRAPH */

.header-profile-photo {
    width: 46px !important;
    height: 46px !important;
    min-width: 46px;
    flex-shrink: 0;
    border-radius: 50% !important;
    object-fit: cover;
    object-position: center;
    border: 2px solid rgba(255,255,255,0.65);
    background: #ffffff20;
    display: block;
    cursor: pointer;
    transition: border-color 0.2s ease;
}

.header-profile-photo:hover {
    border-color: #ffffff;
}

.header-right {
    display: flex;
    align-items: center;
    gap: 14px;
}

.account-menu summary {
    display: flex;
    align-items: center;
    list-style: none;
    cursor: pointer;
}

.account-menu summary::-webkit-details-marker {
    display: none;
}

.user-details {
    text-align: right;
}

.user-details strong {
    font-weight: 650;
    color: #ffffff;
}

.user-details small {
    font-size: 12px;
    margin-top: 4px;
    color: #ded7eb;
}

@media (max-width: 600px) {

    .header-profile-photo {
        width: 38px !important;
        height: 38px !important;
        min-width: 38px;
    }

    .header-right {
        gap: 8px;
    }

}




/* STAR LINK EMPLOYEE WORKSPACE */

.employee-profile-banner {
    background: linear-gradient(120deg, #30254f, #51416e);
    color: white;
    padding: 32px;
    border-radius: 12px;
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 24px;
    margin-bottom: 24px;
}

.employee-profile-banner h2 {
    color: white;
    font-size: 27px;
    margin: 0 0 8px;
}

.employee-profile-banner p {
    color: #e2dced;
    margin: 5px 0;
}

.employee-profile-picture {
    width: 120px;
    height: 120px;
    border-radius: 12px;
    object-fit: cover;
    background: #ffffff20;
    border: 3px solid #ffffff55;
}

.employee-profile-placeholder {
    width: 120px;
    height: 120px;
    border-radius: 12px;
    display: grid;
    place-items: center;
    background: #ffffff20;
    font-size: 44px;
    font-weight: 700;
    color: white;
}

.employee-details-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 16px;
}

.employee-detail {
    background: #f8f8fb;
    border: 1px solid #e2e1e8;
    border-radius: 8px;
    padding: 16px;
    min-width: 0;
}

.employee-detail span {
    display: block;
    color: #667080;
    font-size: 12px;
    margin-bottom: 7px;
}

.employee-detail strong {
    color: #17191d;
    font-size: 15px;
    overflow-wrap: anywhere;
}

.employee-services-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 16px;
}

.employee-service {
    background: white;
    border: 1px solid #dedee6;
    border-radius: 10px;
    padding: 23px 15px;
    text-align: center;
    color: #17191d;
    transition: transform .2s, box-shadow .2s;
}

.employee-service:hover {
    transform: translateY(-3px);
    box-shadow: 0 6px 18px #30254f15;
    border-color: #a99bc5;
}

.employee-service svg {
    width: 55px;
    height: 55px;
    margin-bottom: 12px;
}

.employee-service strong {
    display: block;
    font-size: 15px;
    color: #17191d;
}

.employee-service small {
    display: block;
    color: #667080;
    margin-top: 7px;
}

.workflow-document {
    border: 1px solid #dedee6;
    border-radius: 10px;
    padding: 22px;
    background: white;
    margin-bottom: 16px;
}

.workflow-request {
    border: 1px solid #dedee6;
    border-left: 4px solid #7754ac;
    border-radius: 9px;
    padding: 22px;
    margin-bottom: 16px;
    background: white;
}

.workflow-request-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
    margin-bottom: 16px;
}

.workflow-waiting {
    display: inline-block;
    background: #fff3db;
    color: #875a00;
    border-radius: 20px;
    padding: 8px 13px;
    font-size: 12px;
    font-weight: 650;
}

.workflow-progress-track {
    width: 100%;
    height: 10px;
    background: #e9e6ef;
    border-radius: 20px;
    overflow: hidden;
    margin: 15px 0;
}

.workflow-progress-fill {
    height: 100%;
    background: #347c49;
    border-radius: 20px;
}

.workflow-checkbox {
    display: flex;
    align-items: center;
    gap: 10px;
    border: 1px solid #e1e1e8;
    border-radius: 7px;
    padding: 12px;
    margin-bottom: 8px;
}

.workflow-checkbox input {
    width: 18px;
    height: 18px;
    flex-shrink: 0;
}

.workflow-table td {
    white-space: normal !important;
    vertical-align: middle;
    padding: 14px;
}

.workflow-note {
    background: #f3f0f9;
    border: 1px solid #ded6ed;
    border-radius: 8px;
    padding: 15px;
    margin: 15px 0;
    color: #30254f;
}

@media (max-width: 1000px) {

    .employee-services-grid {
        grid-template-columns: repeat(3, minmax(0, 1fr));
    }

    .employee-details-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }

}

@media (max-width: 600px) {

    .employee-services-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .employee-details-grid {
        grid-template-columns: 1fr;
    }

    .employee-profile-banner {
        padding: 22px;
        gap: 16px;
    }

    .employee-profile-picture,
    .employee-profile-placeholder {
        width: 90px;
        height: 90px;
    }

    .employee-profile-banner h2 {
        font-size: 22px;
    }

}


/* PROFESSIONAL ATTENDANCE TABLE */


/* ==========================================
   PROFESSIONAL ATTENDANCE TABLE
   ========================================== */

.attendance-table {
    width: 100%;
    min-width: 760px;
    table-layout: fixed;
    border-collapse: separate;
    border-spacing: 0;
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 14px;
    background: #ffffff;
}

/* TABLE HEADINGS */

.attendance-table th {
    background: #f3f1f7;
    color: #30254f;
    font-size: 13px;
    font-weight: 700;
    padding: 15px 18px;
    text-align: left;
    vertical-align: middle;
    border-bottom: 1px solid #dedbe7;
    white-space: nowrap;
}

/* COLUMN WIDTHS */

.attendance-table th:nth-child(1) {
    width: 20%;
}

.attendance-table th:nth-child(2) {
    width: 18%;
}

.attendance-table th:nth-child(3) {
    width: 24%;
}

.attendance-table th:nth-child(4) {
    width: 24%;
}

.attendance-table th:nth-child(5) {
    width: 14%;
}

/* CORRECT ROW ALIGNMENT */

.attendance-table td {
    padding: 16px 18px !important;
    vertical-align: top !important;
    text-align: left;
    white-space: normal !important;
    line-height: 1.45 !important;
    height: auto !important;
    color: #17191d;
    border-bottom: 1px solid #e8e8ee;
}

/* REMOVE EXCESSIVE EMPTY SPACING */

.attendance-table td.prewrap {
    white-space: normal !important;
    overflow-wrap: anywhere;
}

.attendance-table td div {
    white-space: normal !important;
}

.attendance-table td p {
    margin: 0 0 6px;
}

/* EMPLOYEE NAME */

.attendance-table td:first-child {
    font-weight: 650;
    color: #222222;
}

/* DATE AND ATTENDANCE TIME */

.attendance-table td:nth-child(2),
.attendance-table td:nth-child(3),
.attendance-table td:nth-child(4) {
    font-variant-numeric: tabular-nums;
}

/* ATTENDANCE PHOTO */

.attendance-table td img {
    width: 145px !important;
    height: 100px !important;
    max-width: 100%;
    object-fit: cover !important;
    border-radius: 8px !important;
    border: 1px solid #dddddd;
    display: block !important;
    margin: 4px 0 6px !important;
    transition: opacity 0.2s ease;
}

.attendance-table td img:hover {
    opacity: 0.85;
}

/* VIEW FULL PHOTO LINK */

.attendance-table td:nth-child(3) a,
.attendance-table td:nth-child(4) a {
    display: inline-flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 3px;
    color: #347c49;
    text-decoration: none;
    font-size: 12px;
    font-weight: 500;
    white-space: normal !important;
}

.attendance-table td small {
    display: block;
    font-size: 12px;
    margin: 0;
    padding: 0;
}

/* PENDING CHECKOUT */

.attendance-table td .muted {
    font-size: 12px;
    color: #788292;
    margin-top: 6px;
    line-height: 1.4;
}

/* STATUS AND ACTIONS */

.attendance-table .inline {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
}

.attendance-table .badge {
    display: inline-block;
    white-space: nowrap;
    font-size: 12px;
    padding: 7px 12px;
    border-radius: 20px;
}

/* ALTERNATING ROW COLORS */

.attendance-table tbody tr {
    background: #ffffff;
}

.attendance-table tbody tr:nth-child(even) {
    background: #faf9fc;
}

.attendance-table tbody tr:hover {
    background: #f5f3fa;
}

/* MOBILE RESPONSIVENESS */

@media screen and (max-width: 768px) {

    .attendance-table {
        min-width: 690px;
        font-size: 13px;
    }

    .attendance-table th,
    .attendance-table td {
        padding: 12px !important;
    }

    .attendance-table td img {
        width: 115px !important;
        height: 85px !important;
    }

}



[hidden] { display: none !important; }

* {
    box-sizing: border-box;
}

:root {
    --purple: #30254f;
    --green: #347c49;
    --background: #e9ecf1;
    --border: #dfe3e9;
    --text: #17191d;
    --muted: #667080;
}

body {
    margin: 0;
    font-family: 'Segoe UI', Arial, sans-serif;
    background: var(--background);
    color: var(--text);
    font-size: 14px;
}

body.soft {
    --background: #e8efeb;
}

body.contrast {
    --background: #ffffff;
    --border: #777777;
    --text: #000000;
}

a {
    text-decoration: none;
    color: #347c49;
}

button, input, select, textarea {
    font: inherit;
}

button {
    cursor: pointer;
}

.header {
    height: 82px;
    background: var(--purple);
    color: white;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 28px;
    position: sticky;
    top: 0;
    z-index: 100;
    box-shadow: 0 3px 12px #0002;
}

.header-left, .header-right {
    display: flex;
    align-items: center;
    gap: 15px;
}

.header-brand {
    position: absolute;
    left: 50%;
    transform: translateX(-50%);
    background: transparent !important;
    border: none;
    box-shadow: none;
    padding: 0;
}

.company-logo {
    width: 245px;
    max-height: 62px;
    object-fit: contain;
    background: transparent !important;
    display: block;
}

.brand-fallback {
    color: white;
    font-size: 29px;
    font-weight: 800;
    letter-spacing: 3px;
    background: transparent;
}

.header-button {
    background: transparent;
    color: white;
    border: none;
    font-size: 25px;
    padding: 8px;
}

.header-button:hover {
    background: #ffffff20;
    border-radius: 5px;
}

.header-right small {
    color: #d3cbe5;
    display: block;
    margin-top: 4px;
}

.avatar {
    width: 43px;
    height: 43px;
    border-radius: 50%;
    background: #ffffff25;
    color: white;
    display: grid;
    place-items: center;
    font-weight: bold;
}

.account-menu {
    position: relative;
}

.account-menu summary {
    cursor: pointer;
    list-style: none;
}

.account-dropdown {
    position: absolute;
    right: 0;
    top: 52px;
    min-width: 200px;
    background: white;
    padding: 12px;
    border-radius: 7px;
    box-shadow: 0 8px 25px #0002;
}

.account-dropdown a,
.account-dropdown button {
    color: black;
    background: white;
    display: block;
    width: 100%;
    padding: 12px;
    text-align: left;
    border: none;
}

.sidebar {
    position: fixed;
    top: 82px;
    bottom: 0;
    left: 0;
    width: 310px;
    max-width: 90vw;
    background: white;
    color: black;
    overflow-y: auto;
    padding: 20px;
    z-index: 95;
    transform: translateX(-100%);
    transition: transform .2s ease;
    border-right: 1px solid var(--border);
}

.sidebar.visible {
    transform: translateX(0);
}

.sidebar-brand {
    background: var(--purple);
    padding: 15px;
    border-radius: 7px;
    margin-bottom: 20px;
}

.sidebar-brand img {
    width: 100%;
}

.sidebar-label {
    color: #667080;
    font-size: 11px;
    letter-spacing: 2px;
    margin-bottom: 16px;
}

.nav-link {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px;
    color: #111111;
    border-radius: 5px;
    font-weight: 500;
}

.nav-link:hover {
    background: #eeeaf7;
    color: black;
}

.nav-link svg {
    width: 22px;
    height: 22px;
    color: #6d5993;
    flex-shrink: 0;
}

.nav-group {
    border-bottom: 1px solid #eeeeee;
}

.nav-group summary {
    color: #111111;
    cursor: pointer;
    font-weight: 700;
    padding: 15px 9px;
}

.nav-group .nav-link {
    padding-left: 24px;
    font-size: 13px;
}

.overlay {
    position: fixed;
    inset: 82px 0 0;
    z-index: 90;
    background: #0004;
    display: none;
}

.overlay.visible {
    display: block;
}

.main {
    max-width: 1560px;
    margin: auto;
    padding: 28px 30px;
    min-height: 80vh;
}

h1 {
    font-size: 30px;
    margin: 0 0 10px;
    letter-spacing: -.6px;
}

h2 {
    font-size: 19px;
    margin: 0 0 16px;
}

p {
    line-height: 1.6;
}

.muted {
    color: var(--muted);
    font-size: 13px;
}

.breadcrumb {
    color: var(--muted);
    font-size: 12px;
    margin-bottom: 22px;
}

.page-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 16px;
    margin-bottom: 24px;
}

.dashboard-welcome {
    margin-bottom: 23px;
}

.dashboard-search {
    max-width: 450px;
    margin-bottom: 23px;
}

.metro-grid {
    display: grid;
    grid-template-columns: repeat(6, minmax(0,1fr));
    gap: 16px;
}

.metro-tile {
    background: white;
    border: 1px solid var(--border);
    border-radius: 8px;
    aspect-ratio: 1/1;
    min-height: 150px;
    padding: 18px;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-direction: column;
    gap: 17px;
    text-align: center;
    color: black;
    transition: transform .18s, box-shadow .18s;
}

.metro-tile:hover {
    transform: translateY(-4px);
    box-shadow: 0 10px 25px #30254f20;
    border-color: #b6a6ce;
}

.metro-icon {
    width: 68%;
    max-width: 120px;
    aspect-ratio: 1;
}

.metro-icon svg {
    width: 100%;
    height: 100%;
}

.metro-title {
    font-size: 16px;
    font-weight: 750;
    color: #111111;
}

.red { color: #e52b45; }
.pink { color: #df1168; }
.teal { color: #347f86; }
.green { color: #398849; }
.purple { color: #7754ac; }
.orange { color: #df8647; }

.metro-wide {
    grid-column: span 3;
    background: var(--purple);
    color: white;
    border-radius: 8px;
    padding: 25px;
    display: flex;
    flex-direction: column;
    justify-content: center;
}

.metro-wide h2 {
    color: white;
    font-size: 23px;
}

.workspace-stats {
    display: grid;
    grid-template-columns: repeat(4,minmax(0,1fr));
    gap: 15px;
}

.workspace-stat {
    text-align: center;
    color: white;
}

.workspace-stat svg {
    width: 38px;
    height: 38px;
}

.workspace-stat-number {
    font-size: 27px;
    font-weight: 750;
    margin: 6px 0;
}

.workspace-stat-label {
    font-size: 12px;
    color: #e7e1f2;
}

.card {
    background: white;
    color: black;
    padding: 24px;
    border: 1px solid var(--border);
    border-radius: 9px;
    margin-bottom: 20px;
    box-shadow: 0 2px 8px #00000004;
}

.stat-grid {
    display: grid;
    grid-template-columns:
        repeat(auto-fit,minmax(160px,1fr));
    gap: 16px;
    margin: 20px 0;
}

.stat {
    padding: 21px;
    background: white;
    border: 1px solid var(--border);
    border-radius: 8px;
    color: #111111;
}

.stat-number {
    font-size: 32px;
    font-weight: 750;
    color: var(--purple);
    margin-top: 8px;
}

.two-columns {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
}

.btn {
    padding: 11px 17px;
    background: var(--green);
    color: white;
    border: 1px solid transparent;
    border-radius: 6px;
    font-size: 13px;
    font-weight: 650;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
}

.btn:hover {
    filter: brightness(.92);
}

.btn-purple {
    background: var(--purple);
}

.btn-red {
    background: #c63245;
}

.btn-light {
    background: #f0f2f5;
    color: #222222;
    border-color: var(--border);
}

.btn-small {
    font-size: 12px;
    padding: 7px 10px;
}

.btn-block {
    width: 100%;
}

.inline {
    display: inline-flex;
    gap: 7px;
    flex-wrap: wrap;
    align-items: center;
}

.inline select {
    width: auto;
}

label {
    display: block;
    font-size: 13px;
    font-weight: 650;
    color: black;
    margin-bottom: 8px;
}

input, select, textarea {
    width: 100%;
    background: white;
    border: 1px solid #d2d7de;
    color: black;
    padding: 12px;
    border-radius: 6px;
    outline: none;
}

input:focus, select:focus, textarea:focus {
    border-color: #8670a9;
    box-shadow: 0 0 0 3px #8670a915;
}

input[type=checkbox] {
    width: 18px;
    height: 18px;
    accent-color: #347c49;
}

input:disabled {
    opacity: .35;
}

textarea {
    min-height: 105px;
    resize: vertical;
}

.form-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 18px;
}

.form-group {
    margin-bottom: 16px;
}

.full {
    grid-column: 1/-1;
}

.table-wrap {
    overflow-x: auto;
}

table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
}

th {
    text-align: left;
    color: #454653;
    background: #f3f1f7;
    padding: 14px;
    white-space: nowrap;
}

td {
    color: #111111;
    padding: 15px 14px;
    border-bottom: 1px solid #edf0f3;
    vertical-align: middle;
    overflow-wrap: anywhere;
}

tbody tr:hover {
    background: #faf9fc;
}

.permission-table td:not(:first-child),
.permission-table th:not(:first-child) {
    text-align: center;
}

.toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 12px;
    margin-bottom: 18px;
}

.toolbar input {
    max-width: 350px;
}

.badge {
    display: inline-block;
    padding: 6px 10px;
    background: #edf0f4;
    color: #444444;
    font-size: 11px;
    font-weight: bold;
    border-radius: 20px;
}

.badge.approved,
.badge.verified,
.badge.completed,
.badge.resolved {
    background: #e1f3e6;
    color: #26653b;
}

.badge.pending,
.badge.open,
.badge.in-progress {
    background: #fff0d4;
    color: #8b601a;
}

.badge.rejected,
.badge.disabled,
.badge.high {
    background: #fce4e9;
    color: #ad2d3c;
}

.flash {
    padding: 15px;
    background: #e6f5eb;
    color: #2d6940;
    border: 1px solid #cce3d2;
    border-radius: 6px;
    margin-bottom: 20px;
}

.flash.error {
    background: #fde7eb;
    color: #ad2d3c;
    border-color: #f0ccd3;
}

.empty {
    padding: 30px;
    text-align: center;
    color: #687080;
}

.activity {
    border-bottom: 1px solid #eeeeee;
    padding: 15px 0;
}

.clock {
    color: #347c49;
    font-size: 34px;
    font-weight: 750;
    margin: 16px 0;
}

.prewrap {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
}

.progress {
    height: 10px;
    background: #eeeeee;
    border-radius: 10px;
    overflow: hidden;
}

.progress div {
    background: #4b9060;
    height: 100%;
}

.auth-layout {
    display: grid;
    grid-template-columns: 1fr 1fr;
    min-height: 100vh;
}

.auth-banner {
    background: var(--purple);
    color: white;
    padding: 65px;
    display: flex;
    flex-direction: column;
    justify-content: center;
}

.auth-banner img {
    width: 100%;
    max-width: 500px;
    max-height: none;
    margin-bottom: 45px;
    background: transparent;
}

.auth-banner h1 {
    color: white;
    font-size: 47px;
    line-height: 1.2;
    margin-top: 30px;
}

.auth-banner p {
    color: #e3ddee;
    font-size: 16px;
}

.auth-panel {
    display: grid;
    place-items: center;
    padding: 25px;
}

.auth-box {
    background: white;
    padding: 35px;
    border-radius: 12px;
    width: 100%;
    max-width: 550px;
    border: 1px solid var(--border);
}

.mobile-brand {
    display: none;
}

.footer {
    text-align: center;
    color: #697080;
    padding: 25px;
    font-size: 12px;
}

@media(max-width:1250px) {

    .metro-grid {
        grid-template-columns: repeat(4,minmax(0,1fr));
    }

    .metro-wide {
        grid-column: span 2;
    }

    .workspace-stats {
        grid-template-columns: repeat(2,1fr);
    }

}

@media(max-width:760px) {

    .header {
        height: 70px;
        padding: 10px;
    }

    .company-logo {
        width: 165px;
    }

    .brand-fallback {
        font-size: 21px;
    }

    .user-details, .desktop-only {
        display: none;
    }

    .sidebar {
        top: 70px;
    }

    .overlay {
        top: 70px;
    }

    .main {
        padding: 17px 13px;
    }

    .metro-grid {
        grid-template-columns: repeat(2,minmax(0,1fr));
        gap: 11px;
    }

    .metro-tile {
        min-height: 145px;
        padding: 13px;
    }

    .metro-title {
        font-size: 14px;
    }

    .metro-icon {
        max-width: 90px;
    }

    .metro-wide {
        grid-column: span 2;
    }

    .form-grid,
    .two-columns,
    .auth-layout {
        grid-template-columns: 1fr;
    }

    .auth-banner {
        display: none;
    }

    .mobile-brand {
        display: block;
        background: var(--purple);
        padding: 15px;
        border-radius: 6px;
        margin-bottom: 20px;
    }

    .mobile-brand img {
        width: 100%;
    }

    .auth-box {
        padding: 23px;
    }

    .auth-panel {
        padding: 15px;
    }

    .card {
        padding: 17px;
    }

    h1 {
        font-size: 25px;
    }

}

"""


# =========================================================
# MAIN HTML TEMPLATE
# =========================================================

CSS += '\n\n/* STAR LINK DARK THEME */\n\nbody.dark {\n\n    color-scheme: dark;\n\n    --background: #14131c;\n    --surface: #211f2c;\n    --surface-secondary: #2a2736;\n    --purple: #493669;\n    --green: #347c49;\n    --border: #3b374a;\n    --text: #f1eef7;\n    --muted: #b6b0c5;\n\n    background: var(--background);\n    color: var(--text);\n\n}\n\n\n/* HEADER */\n\nbody.dark .header {\n    background: #30254f;\n    border-bottom: 1px solid #493c63;\n    box-shadow: 0 3px 15px #0003;\n}\n\nbody.dark .header-right {\n    color: #ffffff;\n}\n\n\n/* SIDEBAR */\n\nbody.dark .sidebar {\n    background: #211f2c;\n    color: #f1eef7;\n    border-color: var(--border);\n}\n\nbody.dark .sidebar-label {\n    color: #b6b0c5;\n}\n\nbody.dark .nav-link,\nbody.dark .nav-group summary {\n    color: #e5e0ef !important;\n}\n\nbody.dark .nav-link:hover {\n    background: #352d49;\n    color: white !important;\n}\n\nbody.dark .nav-link svg {\n    color: #bba3ef;\n}\n\nbody.dark .nav-group {\n    border-color: var(--border);\n}\n\n\n/* ACCOUNT DROPDOWN */\n\nbody.dark .account-dropdown,\nbody.dark .account-dropdown a,\nbody.dark .account-dropdown button {\n    background: #252231;\n    color: #f1eef7;\n}\n\nbody.dark .account-dropdown {\n    border: 1px solid var(--border);\n}\n\nbody.dark .account-dropdown a:hover,\nbody.dark .account-dropdown button:hover {\n    background: #352d49;\n}\n\n\n/* MAIN DASHBOARD CARDS */\n\nbody.dark .card,\nbody.dark .stat,\nbody.dark .metro-tile,\nbody.dark .auth-box,\nbody.dark .employee-service,\nbody.dark .workflow-document,\nbody.dark .workflow-request,\nbody.dark .hr-approval-stat {\n\n    background: var(--surface) !important;\n    color: var(--text) !important;\n    border-color: var(--border) !important;\n    box-shadow: 0 3px 12px #0002;\n\n}\n\nbody.dark .metro-tile:hover,\nbody.dark .employee-service:hover {\n    background: #2b2739 !important;\n    border-color: #8c75b9 !important;\n    box-shadow: 0 8px 25px #0003;\n}\n\nbody.dark .metro-title,\nbody.dark .stat-number,\nbody.dark .employee-service strong,\nbody.dark .hr-approval-stat strong {\n    color: #f1eef7 !important;\n}\n\n\n/* HEADINGS AND TEXT */\n\nbody.dark h1,\nbody.dark h2,\nbody.dark h3,\nbody.dark h4,\nbody.dark label {\n    color: #f1eef7 !important;\n}\n\nbody.dark .muted,\nbody.dark .footer,\nbody.dark .breadcrumb,\nbody.dark .empty,\nbody.dark small,\nbody.dark .employee-service small {\n    color: var(--muted) !important;\n}\n\nbody.dark a:not(.btn):not(.header-button):not(.nav-link):not(.header-brand) {\n    color: #bca7ef;\n}\n\nbody.dark .activity,\nbody.dark hr {\n    border-color: var(--border);\n}\n\n\n/* PROFILE INFORMATION */\n\nbody.dark .employee-detail,\nbody.dark .mp-detail,\nbody.dark .mp-photo-box,\nbody.dark .profile-photo-section,\nbody.dark .profile-photo-upload,\nbody.dark .account-reset-details,\nbody.dark .hr-approval-details,\nbody.dark .identity-upload-card,\nbody.dark .saved-document-card {\n\n    background: var(--surface-secondary) !important;\n    color: var(--text) !important;\n    border-color: var(--border) !important;\n\n}\n\nbody.dark .employee-detail strong,\nbody.dark .mp-detail strong,\nbody.dark .hr-approval-details strong {\n    color: #f1eef7 !important;\n}\n\nbody.dark .employee-detail span,\nbody.dark .mp-detail span,\nbody.dark .hr-approval-details label {\n    color: #b6b0c5 !important;\n}\n\nbody.dark .employee-profile-banner,\nbody.dark .mp-banner,\nbody.dark .profile-setup-heading,\nbody.dark .hr-approval-heading {\n    background: linear-gradient(\n        120deg,\n        #30254f,\n        #493669\n    ) !important;\n    color: white !important;\n}\n\n\n/* INPUT FIELDS */\n\nbody.dark input:not([type=checkbox]):not([type=radio]),\nbody.dark select,\nbody.dark textarea {\n\n    background: #191823 !important;\n    color: #f1eef7 !important;\n    border: 1px solid #494358 !important;\n\n}\n\nbody.dark input::placeholder,\nbody.dark textarea::placeholder {\n    color: #a29bb2;\n}\n\nbody.dark input:focus,\nbody.dark select:focus,\nbody.dark textarea:focus {\n    border-color: #b49ae8 !important;\n    box-shadow: 0 0 0 3px #b49ae820;\n}\n\nbody.dark input[readonly],\nbody.dark input:disabled {\n    background: #302c3e !important;\n    color: #b6b0c5 !important;\n}\n\nbody.dark input[type=checkbox],\nbody.dark input[type=radio] {\n    accent-color: #8d77bd;\n}\n\nbody.dark input::file-selector-button {\n    background: #493669 !important;\n    color: #ffffff !important;\n    border: 0;\n    border-radius: 5px;\n    padding: 9px 12px;\n}\n\n\n/* TABLES AND ATTENDANCE RECORDS */\n\nbody.dark table,\nbody.dark .attendance-table,\nbody.dark .identity-document-table,\nbody.dark .hr-approval-table,\nbody.dark .workflow-table,\nbody.dark .mp-table {\n\n    background: var(--surface) !important;\n    color: var(--text) !important;\n\n}\n\nbody.dark th,\nbody.dark .attendance-table th,\nbody.dark .identity-document-table th,\nbody.dark .hr-approval-table th,\nbody.dark .mp-table th {\n\n    background: #302b40 !important;\n    color: #e6def4 !important;\n    border-color: var(--border) !important;\n\n}\n\nbody.dark td,\nbody.dark .attendance-table td,\nbody.dark .identity-document-table td {\n\n    color: #f1eef7 !important;\n    border-color: var(--border) !important;\n\n}\n\nbody.dark tbody tr {\n    background: var(--surface) !important;\n}\n\nbody.dark tbody tr:nth-child(even) {\n    background: #262333 !important;\n}\n\nbody.dark tbody tr:hover {\n    background: #302b40 !important;\n}\n\nbody.dark .attendance-table img {\n    border-color: var(--border) !important;\n}\n\n\n/* BUTTONS */\n\nbody.dark .btn-purple {\n    background: #624b8b !important;\n    color: white !important;\n}\n\nbody.dark .btn-light,\nbody.dark .document-renewal summary {\n\n    background: #353044 !important;\n    color: #f1eef7 !important;\n    border-color: #494358 !important;\n\n}\n\nbody.dark .btn-light:hover,\nbody.dark .document-renewal summary:hover {\n    background: #453b59 !important;\n}\n\nbody.dark .btn-red {\n    background: #a93648 !important;\n    color: white !important;\n}\n\n\n/* APPROVAL STATUS LABELS */\n\nbody.dark .badge {\n    background: #373345 !important;\n    color: #ddd7eb !important;\n}\n\nbody.dark .badge.approved,\nbody.dark .badge.verified,\nbody.dark .badge.completed,\nbody.dark .badge.resolved {\n    background: #203d2c !important;\n    color: #9de4b0 !important;\n}\n\nbody.dark .badge.pending,\nbody.dark .badge.open,\nbody.dark .badge.in-progress {\n    background: #47391f !important;\n    color: #ffd48a !important;\n}\n\nbody.dark .badge.rejected,\nbody.dark .badge.disabled,\nbody.dark .badge.high {\n    background: #492735 !important;\n    color: #ffabb8 !important;\n}\n\n\n/* NOTIFICATIONS AND WARNINGS */\n\nbody.dark .flash {\n    background: #203d2c;\n    color: #9de4b0;\n    border-color: #365941;\n}\n\nbody.dark .flash.error,\nbody.dark .mp-error,\nbody.dark .account-reset-error,\nbody.dark .profile-setup-error {\n\n    background: #492735 !important;\n    color: #ffabb8 !important;\n    border-color: #754052 !important;\n\n}\n\nbody.dark .workflow-note,\nbody.dark .identity-notice,\nbody.dark .profile-setup-note,\nbody.dark .account-reset-notice {\n\n    background: #30283f !important;\n    color: #e0d4f5 !important;\n    border-color: #49405d !important;\n\n}\n\nbody.dark .workflow-waiting,\nbody.dark .hr-approval-warning {\n    background: #47391f !important;\n    color: #ffd48a !important;\n    border-color: #6c542e !important;\n}\n\nbody.dark .saved-document-success,\nbody.dark .profile-photo-saved,\nbody.dark .clock {\n    color: #9de4b0 !important;\n}\n\n\n/* COLOURFUL DASHBOARD ICONS */\n\nbody.dark .red {\n    color: #ff8194;\n}\n\nbody.dark .pink {\n    color: #ed83b6;\n}\n\nbody.dark .teal {\n    color: #75c7ce;\n}\n\nbody.dark .green {\n    color: #87cf9b;\n}\n\nbody.dark .purple {\n    color: #baa2ef;\n}\n\nbody.dark .orange {\n    color: #f0b179;\n}\n\n\n/* THEME SWITCH BUTTON */\n\n.sl-theme-switch {\n\n    width: 42px;\n    height: 42px;\n    min-width: 42px;\n    display: inline-flex;\n    align-items: center;\n    justify-content: center;\n    background: #ffffff12;\n    border: 1px solid #ffffff30;\n    color: #ffffff;\n    border-radius: 50%;\n    cursor: pointer;\n    padding: 9px;\n\n}\n\n.sl-theme-switch:hover {\n    background: #ffffff30;\n}\n\n.sl-theme-switch svg {\n    width: 22px;\n    height: 22px;\n    fill: none;\n    stroke: currentColor;\n    stroke-width: 1.8;\n    stroke-linecap: round;\n    stroke-linejoin: round;\n}\n\n.sl-theme-switch:disabled {\n    opacity: .5;\n    cursor: wait;\n}\n\n.sl-theme-switch .sl-sun {\n    display: none;\n}\n\nbody.dark .sl-theme-switch .sl-sun {\n    display: block;\n}\n\nbody.dark .sl-theme-switch .sl-moon {\n    display: none;\n}\n\n\n/* MOBILE RESPONSIVE THEME BUTTON */\n\n@media (max-width: 650px) {\n\n    .sl-theme-switch {\n        width: 34px;\n        height: 34px;\n        min-width: 34px;\n        padding: 7px;\n    }\n\n    .sl-theme-switch svg {\n        width: 18px;\n        height: 18px;\n    }\n\n}\n\n\n/* SCROLLBAR */\n\nbody.dark::-webkit-scrollbar,\nbody.dark *::-webkit-scrollbar {\n    width: 9px;\n    height: 9px;\n}\n\nbody.dark::-webkit-scrollbar-track,\nbody.dark *::-webkit-scrollbar-track {\n    background: #191823;\n}\n\nbody.dark::-webkit-scrollbar-thumb,\nbody.dark *::-webkit-scrollbar-thumb {\n    background: #494358;\n    border-radius: 10px;\n}\n\n'

LAYOUT = """

<!DOCTYPE html>
<html lang="en">

<head>
<meta charset="UTF-8">
<meta name="viewport"
content="width=device-width,initial-scale=1">

<title>{{ title }} | STAR LINK</title>

<style>
""" + CSS + """
</style>
</head>

<body class="{{ preference.theme|lower }}">

{% macro token() %}
<input type="hidden" name="csrf" value="{{ csrf() }}">
{% endmacro %}

{% macro icon(name) %}
<svg viewBox="0 0 100 100" aria-hidden="true">
{{ icons.get(name,icons['file'])|safe }}
</svg>
{% endmacro %}

{% macro brand() %}
{% if logo %}
<img class="company-logo" src="{{ logo }}" alt="STAR LINK">
{% else %}
<span class="brand-fallback">STAR LINK</span>
{% endif %}
{% endmacro %}

{% macro badge(value) %}
<span class="badge {{ (value or '')|lower|replace(' ','-') }}">
{{ value or '—' }}
</span>
{% endmacro %}

{% macro input_field(f,value='',readonly=false) %}

<div class="form-group {{ 'full' if f.kind=='textarea' else '' }}">

<label>{{ f.label }}</label>

{% if f.kind=='select' %}

<select name="{{ f.name }}"
{{ 'required' if f.required else '' }}
{{ 'disabled' if readonly else '' }}>

{% for option in f.options %}
<option value="{{ option }}"
{{ 'selected' if value|string==option|string else '' }}>
{{ option }}
</option>
{% endfor %}

</select>

{% elif f.kind=='textarea' %}

<textarea name="{{ f.name }}"
{{ 'required' if f.required else '' }}
{{ 'readonly' if readonly else '' }}>{{ value or '' }}</textarea>

{% else %}

<input name="{{ f.name }}" type="{{ f.kind }}"
value="{{ value if value is not none else '' }}"
{{ 'required' if f.required else '' }}
{{ 'readonly' if readonly else '' }}
{% if f.kind=='number' %}min="0" step="0.01"{% elif f.kind=='time' %}step="1"{% endif %}>

{% endif %}

</div>

{% endmacro %}

{% macro employee_picker(staff,selected=none) %}
{% if admin() %}
<div class="form-group">
<label>Select Employee</label>
<select name="user_id" required>
{% for employee in staff %}
<option value="{{ employee.id }}"
{{ 'selected' if selected==employee.id else '' }}>
{{ employee.name }} — {{ employee.employee_code or '' }}
</option>
{% endfor %}
</select>
</div>
{% endif %}
{% endmacro %}

{% macro search() %}
<input placeholder="Search records..."
oninput="searchTable(this)">
{% endmacro %}

{% macro permission_matrix(names,current) %}

<div class="table-wrap">
<table class="permission-table">
<thead>
<tr>
<th>Portal Module</th>
<th>View</th>
<th>Create</th>
<th>Edit</th>
<th>Delete</th>
</tr>
</thead>

<tbody>

{% for module,label in names.items() %}
{% set p=current.get(module,{}) %}

<tr>

<td><strong>{{ label }}</strong></td>

<td>
<input type="checkbox"
name="{{ module }}_view"
value="1"
data-view
onchange="permissionChange(this)"
aria-label="{{ label }} view"
{{ 'checked' if p.get('can_view',0) else '' }}>
</td>

{% for action in ['create','edit','delete'] %}
{% set available=action in supported_actions(module) %}

<td>
<input type="checkbox"
name="{{ module }}_{{ action }}"
value="1"
data-action
data-supported="{{ 1 if available else 0 }}"
aria-label="{{ label }} {{ action }}"
{{ 'checked' if available and
p.get('can_'+action,0) and p.get('can_view',0) else '' }}
{{ 'disabled' if not available or
not p.get('can_view',0) else '' }}>
</td>

{% endfor %}

</tr>

{% endfor %}

</tbody>
</table>
</div>

{% endmacro %}

{% if g.user %}

<header class="header">

<div class="header-left">

<button class="header-button"
onclick="toggleMenu()"
aria-label="Open menu">☰</button>

<a class="header-button desktop-only"
href="{{ url_for('dashboard') }}">⌂</a>

<button class="header-button desktop-only"
onclick="focusSearch()"
aria-label="Search">⌕</button>

</div>

<a class="header-brand"
href="{{ url_for('dashboard') }}">
{{ brand() }}
</a>

<div class="header-right">


{% if not g.user.must_change_password
and request.endpoint not in
['profile_details','first_onboarding','password_settings'] %}

<button

    type="button"

    id="sl-theme-switch"

    class="sl-theme-switch"

    title="{{ 'Switch to Light Mode'
              if preference.theme=='Dark'
              else 'Switch to Dark Mode' }}"

    aria-label="{{ 'Switch to Light Mode'
              if preference.theme=='Dark'
              else 'Switch to Dark Mode' }}"

    aria-pressed="{{ 'true'
                     if preference.theme=='Dark'
                     else 'false' }}"

    data-url="{{ url_for('sl_save_theme') }}"

    data-csrf="{{ csrf() }}"

>

    <!-- MOON ICON -->

    <svg
        class="sl-moon"
        viewBox="0 0 24 24"
        aria-hidden="true"
    >

        <path d="
            M20.9 13.35
            A9 9 0 0 1 10.65 3.1
            A9 9 0 1 0 20.9 13.35
            Z
        "></path>

    </svg>


    <!-- SUN ICON -->

    <svg
        class="sl-sun"
        viewBox="0 0 24 24"
        aria-hidden="true"
    >

        <circle cx="12" cy="12" r="4"></circle>

        <path d="
            M12 2v2
            M12 20v2
            M2 12h2
            M20 12h2
            M4.93 4.93l1.42 1.42
            M17.65 17.65l1.42 1.42
            M19.07 4.93l-1.42 1.42
            M6.35 17.65l-1.42 1.42
        "></path>

    </svg>

</button>

{% endif %}



<div class="user-details">
<strong>{{ g.user.name }}</strong>
<small>
{{ 'HR Administrator' if admin() else 'Employee' }}
</small>
</div>

<details class="account-menu">

<summary>


{% if header_photo_available() %}

    <img
        class="avatar header-profile-photo"
        src="{{ url_for('profile_picture', uid=g.user.id) }}"
        alt="Your profile picture"
        title="My Account"
    >

{% else %}

    <div class="avatar">
        {{ g.user.name[:1]|upper }}
    </div>

{% endif %}


</summary>

<div class="account-dropdown">

{% if allowed('profile') and not g.user.must_change_password %}


<a href="{{ url_for('profile') }}">
    My Profile
</a>

<a href="{{ url_for('dashboard') }}">
    My Workspace
</a>


{% endif %}

<a href="{{ url_for('password_settings') }}">
Change Password
</a>

<form method="post" action="{{ url_for('logout') }}">
{{ token() }}
<button>Sign Out</button>
</form>

</div>
</details>

</div>

</header>

<div class="overlay" id="overlay" onclick="closeMenu()"></div>

<aside class="sidebar" id="sidebar">

<div class="sidebar-brand">{{ brand() }}</div>
<div class="sidebar-label">COMPANY WORKSPACE</div>

<a class="nav-link" href="{{ url_for('dashboard') }}">
⌂ Main Menu
</a>

{% for group in menu %}

<details class="nav-group">

<summary>{{ group.name }}</summary>

{% for item in group['items'] %}

<a class="nav-link" href="{{ item.link }}">
{{ icon(item.icon) }}
{{ item.label }}
</a>

{% endfor %}

</details>

{% endfor %}

<br>

<form method="post" action="{{ url_for('logout') }}">
{{ token() }}
<button class="btn btn-red btn-block">Sign Out</button>
</form>

</aside>

<main class="main">

{% if request.endpoint!='dashboard' %}

<div class="breadcrumb">
<a href="{{ url_for('dashboard') }}">Main Menu</a>
/ {{ title }}
</div>

{% endif %}

{% for category,message in get_flashed_messages(with_categories=true) %}
<div class="flash {{ category }}">{{ message }}</div>
{% endfor %}

<!-- CONTENT -->

</main>

<footer class="footer">
STAR LINK · Human Resources & Workforce Management
</footer>

{% else %}

<div class="auth-layout">

<section class="auth-banner">

{{ brand() }}

<h1>Your Company.<br>Your People.<br>Your Workspace.</h1>

<p>
A centralized employee management platform
designed to connect people, streamline operations
and support employee welfare.
</p>

<p>
Human Resources · Workforce Management<br>
Attendance · Documents · Performance<br>
Employee Services · Internal Support
</p>

</section>

<section class="auth-panel">

<div class="auth-box">

<div class="mobile-brand">{{ brand() }}</div>

{% for category,message in get_flashed_messages(with_categories=true) %}
<div class="flash {{ category }}">{{ message }}</div>
{% endfor %}

<!-- AUTH -->

</div>

</section>

</div>

{% endif %}

<script>

function toggleMenu() {
    document.getElementById("sidebar").classList.toggle("visible");
    document.getElementById("overlay").classList.toggle("visible");
}

function closeMenu() {
    document.getElementById("sidebar")?.classList.remove("visible");
    document.getElementById("overlay")?.classList.remove("visible");
}

document.addEventListener("keydown", e => {
    if(e.key === "Escape") closeMenu();
});

function searchTable(input) {
    const card = input.closest(".card");
    if(!card) return;
    const value = input.value.toLowerCase();

    card.querySelectorAll("tbody tr").forEach(tr => {
        tr.style.display =
        tr.innerText.toLowerCase().includes(value)
        ? "" : "none";
    });
}

function filterTiles(input) {
    const value = input.value.toLowerCase();

    document.querySelectorAll(".metro-tile").forEach(tile => {
        tile.style.display =
        tile.innerText.toLowerCase().includes(value)
        ? "" : "none";
    });
}

function focusSearch() {
    const input = document.getElementById("dashboardSearch");

    if(input) {
        input.focus();
    } else {
        window.location.href =
        {{ url_for('dashboard')|tojson }} + "#dashboardSearch";
    }
}

function permissionChange(input) {
    const tr = input.closest("tr");
    const view = tr.querySelector("[data-view]");

    tr.querySelectorAll("[data-action]").forEach(box => {
        if(box.dataset.supported !== "1") return;

        box.disabled = !view.checked;

        if(!view.checked) box.checked = false;
    });
}

function allViews(value) {
    document.querySelectorAll("[data-view]").forEach(box => {
        box.checked = value;
        permissionChange(box);
    });
}

function updateClock() {
    document.querySelectorAll(".live-clock").forEach(clock => {
        clock.textContent = new Date().toLocaleTimeString("en-GB", {
            timeZone: "Asia/Dubai"
        });
    });

    document.querySelectorAll(".display-clock").forEach(clock => {
        clock.textContent = new Date().toLocaleTimeString("en-GB", {
            timeZone: {{ preference.timezone|tojson }}
        });
    });
}

setInterval(updateClock, 1000);
updateClock();

</script>



<script>

(() => {

    const button = document.getElementById(
        "sl-theme-switch"
    );

    if (!button) return;

    button.addEventListener("click", async () => {

        const currentDark =
            document.body.classList.contains("dark");

        const nextTheme =
            currentDark ? "Light" : "Dark";

        button.disabled = true;

        try {

            const data = new URLSearchParams({

                csrf: button.dataset.csrf,

                theme: nextTheme

            });

            const response = await fetch(

                button.dataset.url,

                {

                    method: "POST",

                    credentials: "same-origin",

                    headers: {

                        "Content-Type":
                            "application/x-www-form-urlencoded"

                    },

                    body: data

                }

            );

            if (
                !response.ok
                || response.redirected
            ) {

                throw new Error(
                    "Unable to save theme preference."
                );

            }

            const result = await response.json();

            if (
                !["Light", "Dark"].includes(
                    result.theme
                )
            ) {

                throw new Error(
                    "Invalid theme response."
                );

            }

            document.body.classList.remove(

                "light",

                "dark",

                "soft",

                "contrast"

            );

            document.body.classList.add(
                result.theme.toLowerCase()
            );

            const isDark =
                result.theme === "Dark";

            const label = isDark

                ? "Switch to Light Mode"

                : "Switch to Dark Mode";

            button.title = label;

            button.setAttribute(
                "aria-label",
                label
            );

            button.setAttribute(
                "aria-pressed",
                String(isDark)
            );

            const themeSelect =
                document.querySelector(
                    'select[name="theme"]'
                );

            if (themeSelect) {

                themeSelect.value = result.theme;

            }

        } catch (error) {

            window.alert(

                "Theme could not be saved. " +

                "Please refresh the page and try again."

            );

        } finally {

            button.disabled = false;

        }

    });

})();

</script>


</body>
</html>

"""


def page(title, content, **context):

    template = LAYOUT.replace(
        "<!-- CONTENT -->", content
    ).replace(
        "<!-- AUTH -->", content
    )

    return render_template_string(
        template,
        title=title,
        logo=LOGO,
        icons=ICONS,
        menu=visible_menu(),
        preference=prefs(),
        today=today(),
        **context
    )


TABLE = """

<div class="card">

<div class="toolbar">
<h2>{{ table_title }}</h2>
{{ search() }}
</div>

<div class="table-wrap">

<table>

<thead>
<tr>
{% for label,key in columns %}
<th>{{ label }}</th>
{% endfor %}
</tr>
</thead>

<tbody>

{% for item in records %}

<tr>

{% for label,key in columns %}

<td>

{% if key in ['status','decision'] %}
{{ badge(item[key]) }}
{% else %}
{{ item[key] if item[key] is not none else '—' }}
{% endif %}

</td>

{% endfor %}

</tr>

{% else %}

<tr>
<td colspan="{{ columns|length }}" class="empty">
No records available.
</td>
</tr>

{% endfor %}

</tbody>

</table>
</div>
</div>

"""


def table_page(title, records, columns, description=""):

    return page(
        title,
        """
        <h1>{{ title }}</h1>
        <p class="muted">{{ description }}</p>
        """ + TABLE,
        table_title=title,
        records=records,
        columns=columns,
        description=description
    )


# =========================================================
# LOGIN
# =========================================================

@app.route("/")
def home():
    return redirect(
        url_for("dashboard" if g.user else "login")
    )


@app.route("/login", methods=["GET", "POST"])
def login():

    if g.user:
        return redirect(url_for("dashboard"))

    if request.method == "POST":

        email = field("email", 200).lower()
        password = request.form.get("password", "")

        user = row(
            "SELECT * FROM users WHERE lower(email)=?",
            (email,)
        )

        if not user or not check_password_hash(
            user["password"], password
        ):

            flash("Incorrect email or password.", "error")

        elif user["status"] != "Approved":

            flash(
                "Your account requires HR approval "
                "or has been disabled.",
                "error"
            )

        else:

            session.clear()
            session["user_id"] = user["id"]
            session["auth_signature"] = signature(user["password"])
            session.permanent = True

            g.user = user

            audit("Signed into STAR LINK")

            if user["must_change_password"]:
                return redirect(url_for("password_settings"))

            return redirect(url_for("dashboard"))

    return page("Login", """

    <h1>Welcome back</h1>

    <p class="muted">
    Sign in to your STAR LINK employee workspace.
    </p>

    <br>

    <form method="post">

    {{ token() }}

    <div class="form-group">
    <label>Company Email</label>
    <input type="email" name="email"
    placeholder="Enter your email"
    autocomplete="username" required>
    </div>

    <div class="form-group">
    <label>Password</label>
    <input type="password" name="password"
    placeholder="Enter your password"
    autocomplete="current-password" required>
    </div>

    <button class="btn btn-purple btn-block">Sign In</button>

    </form>

    <p style="text-align:center;margin-top:25px">
    New employee?
    <a href="{{ url_for('signup') }}">Create an account</a>
    </p>

    <p class="muted" style="text-align:center">
    Account access is controlled by Human Resources.
    </p>

    """)


@app.post("/logout")
@login_required
def logout():

    audit("Signed out")
    session.clear()

    return redirect(url_for("login"))


# =========================================================
# EMPLOYEE FORM VALIDATION
# =========================================================

def department_names():

    return [
        item["name"]
        for item in rows(
            "SELECT name FROM departments ORDER BY name"
        )
    ]


def employee_fields():

    return [
        F("name", "Full Name"),
        F("email", "Company Email", "email"),
        F("employee_code", "Employee ID"),
        F("phone", "Phone Number", "tel", False),
        F("department", "Department",
          "select", True, department_names()),
        F("designation", "Designation"),
        F("joining_date", "Joining Date", "date", False)
    ]


def validate_fields(fields):

    data = {}

    for f in fields:

        value = field(f["name"])

        if f["required"] and not value:
            abort(400)

        if f["kind"] == "select":
            if value not in f["options"]:
                abort(400)

        elif f["kind"] == "date":
            if value and not valid_date(value):
                abort(400)

        elif f["kind"] == "time":
            if value:
                try:
                    time.fromisoformat(value)
                except ValueError:
                    abort(400)

        elif f["kind"] == "number":
            try:
                value = float(value or 0)
            except ValueError:
                abort(400)

            if not math.isfinite(value):
                abort(400)

            if not 0 <= value <= 1000000:
                abort(400)

        data[f["name"]] = value

    return data


def employee_error(data, password, confirm, exclude_id=0):

    if not re.fullmatch(
        r"[^\s@]+@[^\s@]+\.[^\s@]+",
        data["email"]
    ):
        return "Enter a valid email address."

    if not 10 <= len(password) <= 256:
        return "Password must contain 10 to 256 characters."

    if password != confirm:
        return "Passwords do not match."

    if row("""
        SELECT id FROM users
        WHERE (lower(email)=lower(?)
        OR lower(employee_code)=lower(?))
        AND id!=?
    """, (
        data["email"],
        data["employee_code"],
        exclude_id
    )):
        return "Email or Employee ID already exists."

    return None


# =========================================================
# EMPLOYEE SELF REGISTRATION
# =========================================================

@app.route("/signup", methods=["GET", "POST"])
def signup():

    if g.user:
        return redirect(url_for("dashboard"))

    fields = employee_fields()

    if request.method == "POST":

        data = validate_fields(fields)

        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        data["email"] = data["email"].lower()

        error = employee_error(data, password, confirm)

        if error:
            flash(error, "error")

        else:

            try:

                insert("users", {
                    **data,
                    "password": generate_password_hash(password),
                    "role": "Employee",
                    "status": "Pending",
                    "created_at": stamp()
                })

                flash(
                    "Registration successful. "
                    "HR must approve your account and grant access."
                )

                return redirect(url_for("login"))

            except sqlite3.IntegrityError:

                db().rollback()
                flash("Employee already exists.", "error")

    return page("Registration", """

    <h1>Employee Registration</h1>

    <p class="muted">
    Create your STAR LINK employee account.
    </p>

    <form method="post">

    {{ token() }}

    <div class="form-grid">

    {% for f in fields %}
    {{ input_field(f,request.form.get(f.name,'')) }}
    {% endfor %}

    <div class="form-group">
    <label>Password</label>
    <input type="password" name="password" minlength="10" required>
    </div>

    <div class="form-group">
    <label>Confirm Password</label>
    <input type="password" name="confirm" required>
    </div>

    </div>

    <button class="btn btn-purple btn-block">
    Register Employee
    </button>

    </form>

    <p style="text-align:center">
    Already registered?
    <a href="{{ url_for('login') }}">Sign In</a>
    </p>

    """, fields=fields)


# =========================================================
# NEW HR ADD EMPLOYEE FEATURE
# ONLY HR CAN ACCESS THIS PAGE
# =========================================================

@app.route("/employees/add", methods=["GET", "POST"])
@admin_required
def add_employee():

    fields = employee_fields() + [
        F("status", "Account Status", "select", True, [
            "Approved", "Pending", "Disabled"
        ])
    ]

    roles_data = rows(
        "SELECT id,name FROM job_roles ORDER BY name"
    )

    values = {
        "status": "Approved",
        "joining_date": today()
    }

    current = default_workflow_permissions()

    if request.method == "POST":

        values = validate_fields(fields)

        values["email"] = values["email"].lower()

        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        role_id = request.form.get("role_id", type=int)

        error = employee_error(
            values, password, confirm
        )

        if role_id is not None:

            if not row(
                "SELECT id FROM job_roles WHERE id=?",
                (role_id,)
            ):
                error = "Invalid job role."

        for module in PERMISSION_NAMES:

            current[module] = {
                "can_" + action: int(
                    request.form.get(
                        module + "_" + action
                    ) == "1"
                )
                for action in ACTION_COLUMNS
            }

        if error:

            flash(error, "error")

        else:

            connection = db()

            try:

                connection.execute("BEGIN IMMEDIATE")

                if connection.execute("""
                    SELECT id FROM users
                    WHERE lower(email)=lower(?)
                    OR lower(employee_code)=lower(?)
                """, (
                    values["email"],
                    values["employee_code"]
                )).fetchone():
                    raise ValueError("Duplicate employee.")

                uid = connection.execute("""

                    INSERT INTO users
                    (
                        name,email,password,employee_code,
                        department,designation,phone,
                        role,status,created_at,joining_date,
                        must_change_password
                    )

                    VALUES (?,?,?,?,?,?,?,
                        'Employee',?,?,?,1)

                """, (
                    values["name"],
                    values["email"],
                    generate_password_hash(password),
                    values["employee_code"],
                    values["department"],
                    values["designation"],
                    values["phone"],
                    values["status"],
                    stamp(),
                    values["joining_date"] or None
                )).lastrowid

                if role_id is not None:

                    connection.execute("""
                        INSERT INTO employee_roles
                        (user_id,role_id)
                        VALUES (?,?)
                    """, (uid, role_id))

                save_permissions(uid, connection)

                connection.execute("""
                    INSERT INTO audit_logs
                    (user_id,action,created_at)
                    VALUES (?,?,?)
                """, (
                    g.user["id"],
                    f"HR created employee #{uid}",
                    stamp()
                ))

                connection.execute("""
                    INSERT INTO approval_history
                    (
                        module,record_id,employee_id,
                        decision,decided_by,created_at
                    )
                    VALUES (?,?,?,?,?,?)
                """, (
                    "Employee Registration",
                    uid,
                    uid,
                    values["status"],
                    g.user["id"],
                    stamp()
                ))

                connection.commit()

                flash(
                    "Employee account created successfully. "
                    "The employee must change the temporary "
                    "password at first login."
                )

                return redirect(url_for("workforce"))

            except (sqlite3.IntegrityError, ValueError):

                connection.rollback()

                flash(
                    "Email or Employee ID already exists.",
                    "error"
                )

    return page("Add Employee", """

    <div class="page-heading">

    <div>
    <h1>Add New Employee</h1>
    <p class="muted">
    Create employee accounts and assign individual permissions.
    </p>
    </div>

    <a class="btn btn-light" href="{{ url_for('workforce') }}">
    Back to Workforce
    </a>

    </div>

    <form method="post">

    {{ token() }}

    <div class="card">

    <h2>Employee Information</h2>

    <div class="form-grid">

    {% for f in fields %}
    {{ input_field(f,values.get(f.name,'')) }}
    {% endfor %}

    <div class="form-group">

    <label>Job Role</label>

    <select name="role_id">

    <option value="">Not Assigned</option>

    {% for role in roles_data %}
    <option value="{{ role.id }}">
    {{ role.name }}
    </option>
    {% endfor %}

    </select>

    </div>

    <div class="form-group">
    <label>Temporary Password</label>
    <input type="password" name="password"
    minlength="10" maxlength="256"
    autocomplete="new-password" required>
    </div>

    <div class="form-group">
    <label>Confirm Temporary Password</label>
    <input type="password" name="confirm"
    autocomplete="new-password" required>
    </div>

    </div>

    <p class="muted">
    Employees must change their temporary password
    when they sign in for the first time.
    Job roles do not grant HR administrator privileges.
    </p>

    </div>

    <div class="card">

    <div class="toolbar">

    <h2>Employee Access Permissions</h2>

    <div class="inline">

    <button type="button" class="btn btn-light"
    onclick="allViews(true)">
    Allow All Views
    </button>

    <button type="button" class="btn btn-red"
    onclick="allViews(false)">
    Revoke All
    </button>

    </div>

    </div>

    <p class="muted">
    Core employee services are selected by default. HR can change these permissions.
    Select only the access this employee requires.
    </p>

    {{ permission_matrix(names,current) }}

    </div>

    <button class="btn btn-purple">
    + Create Employee Account
    </button>

    <a class="btn btn-light" href="{{ url_for('workforce') }}">
    Cancel
    </a>

    </form>

    """,
        fields=fields,
        roles_data=roles_data,
        values=values,
        names=PERMISSION_NAMES,
        current=current
    )


# =========================================================
# MAIN DASHBOARD
# =========================================================

def dashboard_statistics():

    if admin():

        return [

            {
                "label": "Employees",
                "value": count("""
                    SELECT COUNT(*) FROM users
                    WHERE role='Employee'
                    AND status='Approved'
                """),
                "icon": "users",
                "link": url_for("workforce")
            },

            {
                "label": "Present Today",
                "value": count("""
                    SELECT COUNT(*) FROM attendance a
                    JOIN users u ON u.id=a.user_id
                    WHERE attendance_date=?
                    AND u.role='Employee'
                    AND u.status='Approved'
                """, (today(),)),
                "icon": "clock",
                "link": url_for("module_page", module="attendance")
            },

            {
                "label": "Approvals",
                "value": count("""
                    SELECT COUNT(*) FROM users
                    WHERE status='Pending'
                """) + count("""
                    SELECT COUNT(*) FROM leaves
                    WHERE status='Pending'
                """) + count("""
                    SELECT COUNT(*) FROM employee_records
                    WHERE status='Pending'
                """) + count("""
                    SELECT COUNT(*) FROM documents
                    WHERE status='Pending'
                """),
                "icon": "check",
                "link": url_for("approvals")
            },

            {
                "label": "Open Tickets",
                "value": count("""
                    SELECT COUNT(*) FROM tickets
                    WHERE status NOT IN ('Resolved','Rejected')
                """),
                "icon": "alert",
                "link": url_for("ticket_status")
            }

        ]

    result = []

    for module, label, table, icon in [
        ("documents", "My Documents", "documents", "file"),
        ("tasks", "My Tasks", "tasks", "tasks"),
        ("leaves", "Vacations", "leaves", "calendar"),
        ("tickets", "My Tickets", "tickets", "ticket")
    ]:

        if allowed(module):

            result.append({
                "label": label,
                "value": count(
                    f"SELECT COUNT(*) FROM {table} WHERE user_id=?",
                    (g.user["id"],)
                ),
                "icon": icon,
                "link": url_for("module_page", module=module)
            })

    return result


ATTENDANCE_CARD = """
{% if allowed('attendance') %}

<div class="card">

    <h2>Daily Attendance</h2>

    <div class="muted">
        Official UAE Time
    </div>

    <div class="clock live-clock"></div>

    {% if current %}

        <p>
            Check-in: {{ current.check_in }}
        </p>

        {% if current.check_out %}

            <p>
                Check-out: {{ current.check_out }}
            </p>

            {{ badge('Completed') }}

        {% endif %}

    {% endif %}

    {% if allowed('attendance','create') and (not current or not current.check_out) %}

        {% set direction = 'out' if current else 'in' %}

        <p>
            Take a new picture of your office or work site
            to complete your attendance.
            Do not photograph confidential material
            or other people.
        </p>

        <form
            method="post"
            action="{{ url_for('mark_attendance') }}"
            class="attendance-camera"
            data-challenge-url="{{ url_for('attendance_camera_challenge') }}"
        >

            {{ token() }}

            <input
                type="hidden"
                name="action"
                value="{{ direction }}"
            >

            <input
                type="hidden"
                name="camera_token"
            >

            <input
                type="hidden"
                name="camera_photo"
            >

            <div
                class="camera-preview"
                hidden
                style="
                    max-width:520px;
                    margin:12px 0;
                "
            >

                <video
                    autoplay
                    muted
                    playsinline
                    style="
                        width:100%;
                        border-radius:10px;
                        background:#17191d;
                    "
                    hidden
                ></video>

                <img
                    alt="Captured work location"
                    style="
                        width:100%;
                        border-radius:10px;
                    "
                    hidden
                >

                <canvas hidden></canvas>

            </div>

            <p
                class="camera-message muted"
                role="status"
                aria-live="polite"
            >
                Camera access is required.
                Gallery and file selection are not available.
            </p>

            <div class="inline">

                <button
                    type="button"
                    class="btn btn-purple camera-open"
                >
                    Open Camera -
                    Check {{ 'Out' if current else 'In' }}
                </button>

                <button
                    type="button"
                    class="btn camera-capture"
                    disabled
                    hidden
                >
                    Take Photo
                </button>

                <button
                    type="button"
                    class="btn btn-light camera-cancel"
                    hidden
                >
                    Cancel
                </button>

                <button
                    type="submit"
                    class="btn {{ 'btn-red' if current else '' }} camera-submit"
                    disabled
                    hidden
                >
                    Confirm Check
                    {{ 'Out' if current else 'In' }}
                </button>

            </div>

            <noscript>
                <p>
                    JavaScript is required to use
                    the attendance camera.
                </p>
            </noscript>

        </form>

        <script>

        (() => {

            const form =
                document.currentScript.previousElementSibling;

            const video =
                form.querySelector('video');

            const image =
                form.querySelector('img');

            const canvas =
                form.querySelector('canvas');

            const preview =
                form.querySelector('.camera-preview');

            const message =
                form.querySelector('.camera-message');

            const open =
                form.querySelector('.camera-open');

            const capture =
                form.querySelector('.camera-capture');

            const cancel =
                form.querySelector('.camera-cancel');

            const submit =
                form.querySelector('.camera-submit');

            const photo =
                form.elements.namedItem('camera_photo');

            const challenge =
                form.elements.namedItem('camera_token');

            let stream = null;
            let attempt = 0;
            let expires = 0;

            // STOP CAMERA

            function stop() {

                if (stream) {

                    stream.getTracks().forEach(
                        track => track.stop()
                    );

                }

                stream = null;
                video.srcObject = null;

            }


            // RESET CAMERA

            function reset(text) {

                attempt++;

                stop();

                photo.value = '';
                challenge.value = '';

                expires = 0;

                image.removeAttribute('src');

                preview.hidden = true;
                video.hidden = true;
                image.hidden = true;

                capture.hidden = true;
                capture.disabled = true;

                submit.hidden = true;
                submit.disabled = true;

                cancel.hidden = true;

                open.disabled = false;

                open.textContent = 'Open Camera';

                message.textContent = text;

            }


            // OPEN DIRECT DEVICE CAMERA

            open.addEventListener('click', async () => {

                reset('Requesting camera access...');

                const id = attempt;

                open.disabled = true;
                cancel.hidden = false;

                try {

                    if (
                        !window.isSecureContext ||
                        !navigator.mediaDevices?.getUserMedia
                    ) {

                        throw new Error(
                            'Use HTTPS or localhost in a browser with camera access.'
                        );

                    }

                    const camera =
                        await navigator.mediaDevices.getUserMedia({

                            video: {

                                facingMode: {
                                    ideal: 'environment'
                                },

                                width: {
                                    ideal: 1280
                                },

                                height: {
                                    ideal: 960
                                }

                            },

                            audio: false

                        });

                    if (id !== attempt) {

                        camera.getTracks().forEach(
                            t => t.stop()
                        );

                        return;

                    }

                    stream = camera;

                    video.srcObject = stream;

                    preview.hidden = false;
                    video.hidden = false;

                    await video.play();

                    const body = new URLSearchParams({

                        csrf:
                            form.elements.namedItem('csrf').value,

                        action:
                            form.elements.namedItem('action').value

                    });

                    const response = await fetch(
                        form.dataset.challengeUrl,
                        {
                            method: 'POST',
                            body,
                            credentials: 'same-origin'
                        }
                    );

                    if (!response.ok) {

                        throw new Error(
                            'Could not start attendance. Refresh and try again.'
                        );

                    }

                    const data = await response.json();

                    if (id !== attempt) {
                        return;
                    }

                    challenge.value = data.token;

                    expires = Date.now() + 115000;

                    capture.hidden = false;
                    capture.disabled = false;

                    message.textContent =
                        'Point the camera at your office or work site, then take a photo.';

                } catch (error) {

                    if (id === attempt) {

                        reset(
                            'Camera unavailable or permission denied. ' +
                            'Allow camera access and try again. ' +
                            (error.message || '')
                        );

                    }

                }

            });


            // CAPTURE INSTANT PHOTOGRAPH

            capture.addEventListener('click', () => {

                if (
                    !stream ||
                    !video.videoWidth ||
                    video.readyState < 2 ||
                    Date.now() >= expires
                ) {

                    reset(
                        'Camera session expired or not ready. Open the camera again.'
                    );

                    return;

                }

                const scale = Math.min(
                    1,
                    1280 / Math.max(
                        video.videoWidth,
                        video.videoHeight
                    )
                );

                canvas.width = Math.max(
                    1,
                    Math.round(video.videoWidth * scale)
                );

                canvas.height = Math.max(
                    1,
                    Math.round(video.videoHeight * scale)
                );

                canvas.getContext('2d').drawImage(
                    video,
                    0,
                    0,
                    canvas.width,
                    canvas.height
                );

                photo.value = canvas.toDataURL(
                    'image/jpeg',
                    0.7
                );

                image.src = photo.value;

                image.hidden = false;
                video.hidden = true;

                stop();

                capture.disabled = true;
                capture.hidden = true;

                submit.hidden = false;
                submit.disabled = false;

                open.disabled = false;

                open.textContent = 'Retake Photo';

                message.textContent =
                    'Check the photo and confirm attendance before the camera session expires.';

            });


            // CANCEL ATTENDANCE

            cancel.addEventListener('click', () => {

                reset(
                    'Camera closed. No attendance was recorded.'
                );

            });


            // PHOTO VALIDATION BEFORE SUBMISSION

            form.addEventListener('submit', event => {

                if (
                    !photo.value ||
                    !challenge.value ||
                    Date.now() >= expires
                ) {

                    event.preventDefault();

                    reset(
                        'A fresh camera photo is required. Open the camera again.'
                    );

                } else {

                    submit.disabled = true;
                    open.disabled = true;
                    cancel.disabled = true;

                    message.textContent =
                        'Saving attendance...';

                }

            });


            // RELEASE CAMERA WHEN LEAVING PAGE

            window.addEventListener('pagehide', () => {

                reset('Open the camera again.');

            });

        })();

        </script>

    {% elif not current %}

        <p>No attendance recorded today.</p>

    {% endif %}

</div>

{% endif %}
"""


def current_attendance():

    if not allowed("attendance"):
        return None

    return row("""
        SELECT * FROM attendance
        WHERE user_id=? AND attendance_date=?
    """, (g.user["id"], today()))


@app.route("/dashboard")
@login_required
def dashboard():

    tiles = dashboard_tiles()

    news = rows("""
        SELECT * FROM announcements
        ORDER BY id DESC LIMIT 4
    """) if allowed("announcements") else []

    return page("Main Menu", """

    <div class="page-heading">

    <div class="dashboard-welcome">

    <h1>Welcome, {{ g.user.name }}</h1>

    <div class="muted">
    {{ 'Human Resources & Workforce Control Center'
    if admin() else 'Your Employee Workspace' }}
    </div>

    <p class="muted">{{ current_date }}</p>

    </div>

    {% if admin() %}

    <div class="inline">

    <a class="btn" href="{{ url_for('add_employee') }}">
    + Add Employee
    </a>

    <a class="btn btn-purple"
    href="{{ url_for('permissions') }}">
    Manage Permissions
    </a>

    </div>

    {% endif %}

    </div>

    {% if tiles %}

    <div class="dashboard-search">
    <input id="dashboardSearch"
    placeholder="Search your workspace..."
    oninput="filterTiles(this)">
    </div>

    <div class="metro-grid">

    {% for tile in tiles[:7] %}

    <a class="metro-tile" href="{{ tile.link }}">
    <div class="metro-icon {{ tile.color }}">
    {{ icon(tile.icon) }}
    </div>
    <div class="metro-title">{{ tile.label }}</div>
    </a>

    {% endfor %}

    {% if statistics %}

    <div class="metro-wide">

    <h2>
    {{ 'Company Overview' if admin() else 'My Workspace' }}
    </h2>

    <div class="workspace-stats">

    {% for item in statistics %}

    <a class="workspace-stat" href="{{ item.link }}">
    {{ icon(item.icon) }}
    <div class="workspace-stat-number">{{ item.value }}</div>
    <div class="workspace-stat-label">{{ item.label }}</div>
    </a>

    {% endfor %}

    </div>

    </div>

    {% endif %}

    {% for tile in tiles[7:] %}

    <a class="metro-tile" href="{{ tile.link }}">
    <div class="metro-icon {{ tile.color }}">
    {{ icon(tile.icon) }}
    </div>
    <div class="metro-title">{{ tile.label }}</div>
    </a>

    {% endfor %}

    </div>

    {% else %}

    <div class="card empty">
    <h2>Your account has been approved</h2>
    <p>HR has not assigned any portal permissions yet.</p>
    <p>Please contact Human Resources for access.</p>
    </div>

    {% endif %}

    <br><br>

    <div class="two-columns">

    """ + ATTENDANCE_CARD + """

    {% if allowed('announcements') %}

    <div class="card">

    <h2>Company Announcements</h2>

    {% for item in news %}

    <div class="activity">
    <strong>{{ item.title }}</strong>
    <p class="prewrap">{{ item.message }}</p>
    <small class="muted">{{ item.created_at }}</small>
    </div>

    {% else %}
    <p class="muted">No announcements yet.</p>
    {% endfor %}

    <a href="{{ url_for('communication') }}">
    View all announcements
    </a>

    </div>

    {% endif %}

    </div>

    """,
        tiles=tiles,
        statistics=dashboard_statistics(),
        current=current_attendance(),
        news=news,
        current_date=now().strftime(prefs()["date_format"])
    )


# =========================================================
# WORKFORCE MANAGEMENT
# =========================================================

@app.route("/employees")
@app.route("/WorkforceManagement")
@admin_required
def workforce():

    employees = rows("""
        SELECT * FROM users
        WHERE role='Employee'
        ORDER BY id DESC
    """)

    statistics = [
        ("Registered", len(employees)),
        ("Approved", sum(
            x["status"] == "Approved" for x in employees
        )),
        ("Pending", sum(
            x["status"] == "Pending" for x in employees
        )),
        ("Disabled", sum(
            x["status"] == "Disabled" for x in employees
        ))
    ]

    return page("Workforce Management", """

    <div class="page-heading">

    <div>
    <h1>Workforce Management</h1>
    <p class="muted">
    Manage employees, departments and individual access.
    </p>
    </div>

    <div class="inline">

    <a class="btn" href="{{ url_for('add_employee') }}">
    + Add Employee
    </a>

    <a class="btn btn-purple" href="{{ url_for('permissions') }}">
    Manage Permissions
    </a>

    </div>

    </div>

    <div class="stat-grid">

    {% for label,value in statistics %}

    <div class="stat">
    {{ label }}
    <div class="stat-number">{{ value }}</div>
    </div>

    {% endfor %}

    </div>

    <div class="card">

    <div class="toolbar">
    <h2>Employee Directory</h2>
    {{ search() }}
    </div>

    <div class="table-wrap">

    <table>

    <thead>
    <tr>
    <th>Employee</th>
    <th>Employee ID</th>
    <th>Department</th>
    <th>Designation</th>
    <th>Joining Date</th>
    <th>Status</th>
    <th>HR Controls</th>
    </tr>
    </thead>

    <tbody>

    {% for employee in employees %}

    <tr>

    <td>
    <strong>{{ employee.name }}</strong>
    <div class="muted">{{ employee.email }}</div>
    </td>

    <td>{{ employee.employee_code }}</td>
    <td>{{ employee.department }}</td>
    <td>{{ employee.designation }}</td>
    <td>{{ employee.joining_date or '—' }}</td>
    <td>{{ badge(employee.status) }}</td>

    <td>

    <div class="inline">

    <a class="btn btn-small btn-light"
    href="{{ url_for('employee_edit',user_id=employee.id) }}">
    Edit Profile
    </a>

    <a class="btn btn-small btn-purple"
    href="{{ url_for('permissions',employee=employee.id) }}">
    Permissions
    </a>

    <a class="btn btn-small btn-light"
    href="{{ url_for('timeline',employee=employee.id) }}">
    Timeline
    </a>

    </div>

    <br><br>

    <form method="post" class="inline"
    action="{{ url_for('employee_status',user_id=employee.id) }}">

    {{ token() }}

    <select name="status">

    {% for status in ['Pending','Approved','Rejected','Disabled'] %}

    <option {{ 'selected' if status==employee.status else '' }}>
    {{ status }}
    </option>

    {% endfor %}

    </select>

    <button class="btn btn-small">Save Access</button>

    </form>

    </td>

    </tr>

    {% else %}

    <tr>
    <td colspan="7" class="empty">No employees registered.</td>
    </tr>

    {% endfor %}

    </tbody>
    </table>
    </div>
    </div>

    """, employees=employees, statistics=statistics)


@app.post("/employees/<int:user_id>/status")
@admin_required
def employee_status(user_id):

    if not row("""
        SELECT id FROM users
        WHERE id=? AND role='Employee'
    """, (user_id,)):
        abort(404)

    status = field("status")

    if status not in {
        "Approved", "Pending", "Rejected", "Disabled"
    }:
        abort(400)

    save("""
        UPDATE users SET status=?
        WHERE id=? AND role='Employee'
    """, (status, user_id))

    decision("Employee Registration", user_id, user_id, status)

    flash("Employee account status updated.")

    return redirect(url_for("workforce"))


@app.route("/employee/<int:user_id>/edit", methods=["GET", "POST"])
@admin_required
def employee_edit(user_id):

    employee = row("""
        SELECT * FROM users
        WHERE id=? AND role='Employee'
    """, (user_id,))

    if not employee:
        abort(404)

    fields = employee_fields()

    if request.method == "POST":

        data = validate_fields(fields)

        data["email"] = data["email"].lower()

        if not re.fullmatch(
            r"[^\s@]+@[^\s@]+\.[^\s@]+",
            data["email"]
        ):
            abort(400)

        if row("""
            SELECT id FROM users
            WHERE (lower(email)=lower(?)
            OR lower(employee_code)=lower(?))
            AND id!=?
        """, (
            data["email"],
            data["employee_code"],
            user_id
        )):

            flash("Email or Employee ID already exists.", "error")

        else:

            keys = list(data)

            save(
                "UPDATE users SET "
                + ",".join(k + "=?" for k in keys)
                + " WHERE id=? AND role='Employee'",
                [data[k] for k in keys] + [user_id]
            )

            audit(f"Edited employee #{user_id}")

            flash("Employee profile updated.")

            return redirect(url_for("workforce"))

    return page("Edit Employee", """

    <h1>{{ employee.name }}</h1>

    <p class="muted">{{ employee.email }}</p>

    <div class="card">

    <h2>Employment Information</h2>

    <form method="post">

    {{ token() }}

    <div class="form-grid">

    {% for f in fields %}
    {{ input_field(f,employee[f.name]) }}
    {% endfor %}

    </div>

    <button class="btn">Save Employee</button>

    </form>

    </div>

    <div class="card">

    <h2>Reset Employee Password</h2>

    <form method="post"
    action="{{ url_for('employee_reset_password',user_id=employee.id) }}">

    {{ token() }}

    <div class="form-grid">

    <div class="form-group">
    <label>New Temporary Password</label>
    <input type="password" name="password" minlength="10" required>
    </div>

    <div class="form-group">
    <label>Confirm Password</label>
    <input type="password" name="confirm" required>
    </div>

    </div>

    <button class="btn btn-purple">Reset Password</button>

    </form>

    </div>

    <div class="card">

    <h2>Employee Records</h2>

    <div class="metro-grid">

    {% for key,config in modules.items() %}

    <a class="metro-tile"
    href="{{ url_for('module_page',module=key,employee=employee.id) }}">

    <div class="metro-icon {{ config.color }}">
    {{ icon(config.icon) }}
    </div>

    <div class="metro-title">{{ config.title }}</div>

    </a>

    {% endfor %}

    </div>
    </div>

    """, employee=employee, fields=fields, modules=MODULES)


@app.post("/employee/<int:user_id>/reset-password")
@admin_required
def employee_reset_password(user_id):

    if not row("""
        SELECT id FROM users
        WHERE id=? AND role='Employee'
    """, (user_id,)):
        abort(404)

    password = request.form.get("password", "")
    confirm = request.form.get("confirm", "")

    if password != confirm or not 10 <= len(password) <= 256:

        flash("Invalid password or passwords do not match.", "error")

    else:

        save("""
            UPDATE users
            SET password=?,must_change_password=1
            WHERE id=? AND role='Employee'
        """, (generate_password_hash(password), user_id))

        audit(f"HR reset employee password #{user_id}")

        flash("Employee password reset successfully.")

    return redirect(url_for("employee_edit", user_id=user_id))


# =========================================================
# PERMISSION MANAGEMENT
# =========================================================

@app.route("/permissions", methods=["GET", "POST"])
@admin_required
def permissions():

    employees = rows("""
        SELECT id,name,employee_code,status
        FROM users
        WHERE role='Employee'
        ORDER BY name
    """)

    selected = request.args.get("employee", type=int)

    if request.method == "POST":

        selected = request.form.get("user_id", type=int)

        if not row("""
            SELECT id FROM users
            WHERE id=? AND role='Employee'
        """, (selected,)):
            abort(400)

        save_permissions(selected)

        db().commit()

        audit(f"HR updated permissions for employee #{selected}")

        flash("Employee permissions saved.")

        return redirect(url_for("permissions", employee=selected))

    if selected is None and employees:
        selected = employees[0]["id"]

    current = {}

    if selected:

        if not row("""
            SELECT id FROM users
            WHERE id=? AND role='Employee'
        """, (selected,)):
            abort(404)

        current = {
            item["module"]: dict(item)
            for item in rows("""
                SELECT * FROM access_control
                WHERE user_id=?
            """, (selected,))
        }

    return page("Permissions", """

    <h1>Employee Access Control</h1>

    <p class="muted">
    Only Human Resources can grant or revoke portal permissions.
    </p>

    <div class="card">

    <form method="get">

    <label>Select Employee</label>

    <select name="employee" onchange="this.form.submit()">

    {% for employee in employees %}

    <option value="{{ employee.id }}"
    {{ 'selected' if employee.id==selected else '' }}>

    {{ employee.name }} — {{ employee.employee_code }}
    — {{ employee.status }}

    </option>

    {% endfor %}

    </select>
    </form>
    </div>

    {% if selected %}

    <div class="card">

    <div class="toolbar">

    <h2>Module Permission Matrix</h2>

    <div class="inline">

    <button type="button" class="btn btn-light"
    

    onclick="allViews(true)">Allow All Views</button>

    <button

        type="button"

        class="btn btn-purple"

        onclick="

            allViews(true);

            document.querySelectorAll('[data-action]')
            .forEach(box => {

                if (!box.disabled) {

                    box.checked = true;

                }

            });

        "

    >

        Grant All Permissions

    </button>

    

    <button type="button" class="btn btn-red"
    onclick="allViews(false)">Revoke All</button>

    </div>

    </div>

    <form method="post">

    {{ token() }}

    <input type="hidden" name="user_id" value="{{ selected }}">

    {{ permission_matrix(names,current) }}

    <br>

    <button class="btn btn-purple">
    Save Employee Permissions
    </button>

    </form>

    </div>

    <div class="card">
    <h2>Protected HR Functions</h2>
    <p>
    Employee registration approvals, permission management,
    official HR decisions, confidential notes,
    company-wide reports and audit logs remain HR-only.
    </p>
    </div>

    {% else %}

    <div class="card empty">
    No employee accounts yet.
    <br><br>
    <a class="btn" href="{{ url_for('add_employee') }}">
    + Add Employee
    </a>
    </div>

    {% endif %}

    """,
        employees=employees,
        selected=selected,
        current=current,
        names=PERMISSION_NAMES
    )


# =========================================================
# GENERIC MODULE SECURITY
# =========================================================

def get_module(module):

    config = MODULES.get(module)

    if not config:
        abort(404)

    if config["hr_only"] and not admin():
        abort(403)

    return config


def validate_record(module, data):

    for start, end in [
        ("start_date", "end_date"),
        ("record_date", "end_date")
    ]:

        if data.get(start) and data.get(end):

            if data[end] < data[start]:
                abort(400)

    if module in {"performance", "kpi"}:

        if data.get("amount", 0) > 100:
            abort(400)

    if module in {"timesheets", "extra-work"}:

        if not 0 < data.get("amount", 0) <= 24:
            abort(400)

    if module == "attendance" and data.get("check_out"):

        if time.fromisoformat(data["check_out"]) < time.fromisoformat(
            data["check_in"]
        ):
            abort(400)


def module_rows(module, employee=None):

    config = get_module(module)

    require(module)

    table = config["table"]

    photo_flags = ""

    if module == "attendance":

        photo_flags = """,
        EXISTS(
            SELECT 1 FROM attendance_photos p
            WHERE p.attendance_id=r.id AND p.action='in'
        ) AS has_in_photo,
        EXISTS(
            SELECT 1 FROM attendance_photos p
            WHERE p.attendance_id=r.id AND p.action='out'
        ) AS has_out_photo
        """

    sql = f"""
        SELECT r.*,u.name AS employee_name{photo_flags}
        FROM {table} r
        JOIN users u ON u.id=r.user_id
        WHERE 1=1
    """

    args = []

    if table == "employee_records":

        sql += " AND r.module=?"
        args.append(module)

    if not admin():

        sql += " AND r.user_id=?"
        args.append(g.user["id"])

    elif employee is not None:

        sql += " AND r.user_id=?"
        args.append(employee)

    return rows(sql + " ORDER BY r.id DESC", args)




def can_modify(module, item, action):

    if action not in ("edit", "delete"):
        return False

    if not allowed(module, action):
        return False

    # HR has complete access.
    if admin():
        return True

    # Employees can modify their own records
    # only when HR grants permission.
    return item["user_id"] == g.user["id"]


def owned_record(module, record_id, action="view"):

    config = get_module(module)

    require(module, action)

    table = config["table"]

    sql = f"SELECT * FROM {table} WHERE id=?"

    args = [record_id]

    if table == "employee_records":

        sql += " AND module=?"
        args.append(module)

    item = row(sql, args)

    if not item:
        abort(404)

    if not admin() and item["user_id"] != g.user["id"]:
        abort(403)

    if action in {"edit", "delete"}:

        if not can_modify(module, item, action):
            abort(403)

    return item


app.jinja_env.globals["can_modify"] = can_modify


# =========================================================
# ALL RECORD MANAGEMENT MODULES
# =========================================================

@app.route("/module/<module>", methods=["GET", "POST"])
@login_required
def module_page(module):

    config = get_module(module)

    require(module)

    table = config["table"]

    if request.method == "POST":

        require(module, "create")

        if module == "attendance":
            abort(403)

        data = validate_fields(config["fields"])

        validate_record(module, data)

        uid = selected_employee()

        data["user_id"] = uid

        if module != "attendance":
            data["status"] = config["initial"]

        if table == "employee_records":

            data.update({
                "module": module,
                "created_by": g.user["id"],
                "created_at": stamp()
            })

            if admin() and module not in WORKFLOW_MODULES:
                data["status"] = "Approved"

        elif table == "tasks":

            data["created_by"] = g.user["id"]
            data["created_at"] = stamp()

        elif table == "documents":

            uploaded = request.files.get("document")

            if not uploaded or not uploaded.filename:
                abort(400)

            original = secure_filename(uploaded.filename)

            extension = (
                original.rsplit(".", 1)[-1].lower()
                if "." in original else ""
            )

            if extension not in ALLOWED_FILES:

                flash("Unsupported file type.", "error")

                return redirect(
                    url_for("module_page", module=module)
                )

            filename = uuid.uuid4().hex + "." + extension

            uploaded.save(UPLOADS / filename)

            data.update({
                "filename": filename,
                "original_name": original,
                "uploaded_at": stamp()
            })

        elif table == "attendance":

            data["check_out"] = data.get("check_out") or None

        else:

            data["created_at"] = stamp()

        try:

            record_id = sl_insert_request(module, table, data)

        except Exception as error:

            db().rollback()

            if module == "documents":

                (UPLOADS / data["filename"]).unlink(
                    missing_ok=True
                )

            if isinstance(error, sqlite3.IntegrityError):

                flash(
                    "Record conflicts with existing data.",
                    "error"
                )

                return redirect(
                    url_for("module_page", module=module)
                )

            raise

        audit(f"Created {module} record #{record_id}")

        if table == "employee_records" and admin() and module not in WORKFLOW_MODULES:

            decision(
                module, record_id, uid, "Approved"
            )

        flash("Record saved successfully.")

        return redirect(url_for("module_page", module=module))

    employee_filter = (
        request.args.get("employee", type=int)
        if admin() else None
    )

    records = module_rows(module, employee_filter)

    status_filter = request.args.get("status")

    if status_filter and module != "attendance":

        records = [
            item for item in records
            if item["status"] == status_filter
        ]

    attendance_html = (
        ATTENDANCE_CARD if module == "attendance" else ""
    )

    return page(config["title"], """

    <div class="page-heading">

    <div>
    <h1>{{ title }}</h1>
    <p class="muted">Manage and review authorized records.</p>
    </div>

    </div>

    """ + attendance_html + """

    {% if allowed(module,'create') and module!='attendance' %}

    <details class="card"
    {{ 'open' if request.args.get('new')=='1' else '' }}>

    <summary style="cursor:pointer;font-weight:700">
    + Add New Record
    </summary>

    <br>

    <form method="post" enctype="multipart/form-data">

    {{ token() }}

    <div class="form-grid">

    {{ employee_picker(staff,employee_filter) }}

    {% for f in config.fields %}

    {{ input_field(f,today if f.name=='record_date' else '') }}

    {% endfor %}

    {% if module=='documents' %}

    <div class="form-group">
    <label>Upload Document</label>
    <input type="file" name="document"
    accept=".pdf,.png,.jpg,.jpeg,.docx,.xlsx" required>
    </div>

    {% endif %}

    </div>

    

{% if module in sl_approval_modules %}

{% set candidates = sl_form_approvers(module) %}

<div class="sl-approver-section">

    <h2>Choose Approvers</h2>


{% set mandatory_names = sl_mandatory_approver_names(module) %}

{% if mandatory_names %}

    <div class="sl-required-reviewers">

        <strong>Mandatory Approvers</strong>

        <p>

            {{ mandatory_names|join(' → ') }}

        </p>

        <small>

            These reviewers are included automatically.

            You may select additional authorized approvers.

        </small>

    </div>

{% endif %}



    <p class="muted">
        Select additional authorized approvers, if needed. Your mandatory approval stages are included automatically.
        Every selected reviewer must approve it.
    </p>

    {% if candidates %}

        <div id="sl-approver-list">

            <div class="sl-approver-row">

                <label>Approver 1</label>

                <select
                    name="sl_reviewer"
                    
                >

                    <option value="">
                        Select Approver
                    </option>

                    {% for person in candidates %}

                        <option value="{{ person.id }}">

                            {{ person.name }}
                            — {{ person.designation or 'Employee' }}
                            — {{ person.department or 'General' }}

                        </option>

                    {% endfor %}

                </select>

            </div>

        </div>

        <button
            type="button"
            class="btn btn-light"
            id="sl-add-approver"
        >
            + Add Another Approver
        </button>

        <p class="muted">

            Approvals take place in the displayed order.
            Final HR approval applies only when configured.

        </p>

    {% else %}

        <p class="muted">

            
        {% if admin() %}

            No additional reviewers are available.

            You can configure authorized reviewers through
            Workflow Configuration.

            {% if 'workflow_settings' in
                sl_repair_endpoints() %}

                <br><br>

                <a class="btn btn-small btn-purple"
                   href="{{ url_for('workflow_settings') }}">

                    Configure Approvers

                </a>

            {% endif %}

        {% else %}

            Your existing approval configuration will be used.

            If mandatory reviewers have not been assigned,
            please contact HR.

        {% endif %}
        

        </p>

    {% endif %}

</div>

<script>

(() => {

    const list = document.getElementById(
        "sl-approver-list"
    );

    const add = document.getElementById(
        "sl-add-approver"
    );

    if (!list || !add) return;

    const original = list.firstElementChild;

    function renumber() {

        const items = list.querySelectorAll(
            ".sl-approver-row"
        );

        items.forEach((item, index) => {

            item.querySelector("label").textContent =
                "Approver " + (index + 1);

        });

        add.disabled = items.length >= 10;

    }

    add.addEventListener("click", () => {

        if (list.children.length >= 10) return;

        const item = original.cloneNode(true);

        const select = item.querySelector("select");

        select.value = "";
        select.required = true;

        const remove = document.createElement("button");

        remove.type = "button";

        remove.className = "btn btn-light";

        remove.textContent = "Remove";

        remove.addEventListener("click", () => {

            item.remove();

            renumber();

        });

        item.appendChild(remove);

        list.appendChild(item);

        renumber();

    });

    const form = list.closest("form");

    form.addEventListener("submit", event => {

        const selected = Array.from(
            list.querySelectorAll("select")
        ).map(
            item => item.value
        ).filter(Boolean);

        if (new Set(selected).size !== selected.length) {

            event.preventDefault();

            window.alert(
                "Please choose a different person for each approval stage."
            );

        }

    });

})();

</script>

{% endif %}


<button class="btn">Save Record</button>

    </form>

    </details>

    {% endif %}

    <div class="card">

    <div class="toolbar">
    <h2>{{ title }} Records</h2>
    {{ search() }}
    </div>

    {% if admin() %}

    <form method="get" class="inline">

    <select name="employee" onchange="this.form.submit()">

    <option value="">All Employees</option>

    {% for employee in staff %}
    <option value="{{ employee.id }}"
    {{ 'selected' if employee_filter==employee.id else '' }}>
    {{ employee.name }}
    </option>
    {% endfor %}

    </select>

    {% if module!='attendance' %}

    <select name="status" onchange="this.form.submit()">

    <option value="">All Statuses</option>

    {% for status in config.statuses %}
    <option {{ 'selected' if request.args.get('status')==status else '' }}>
    {{ status }}
    </option>
    {% endfor %}

    </select>

    {% endif %}

    </form>

    <br><br>

    {% endif %}

    <div class="table-wrap">

    <table class="{{ 'attendance-table' if module == 'attendance' else '' }}">

    <thead>

    <tr>

    <th>Employee</th>

    {% for f in config.fields %}
    <th>{{ f.label }}</th>
    {% endfor %}

    {% if module!='attendance' %}
    <th>Status</th>
    {% endif %}

    
    <th>
        {{ 'Status / Actions'
        if module == 'attendance'
        else 'Actions' }}
    </th>
    

    </tr>

    </thead>

    <tbody>

    {% for item in records %}

    <tr>

    <td>{{ item.employee_name }}</td>

    {% for f in config.fields %}

    <td class="prewrap">

    {{ item[f.name] if item[f.name] is not none else '—' }}

    {% if module == 'attendance'
    and f.name in ['check_in', 'check_out'] %}

        {% set direction =
            'in' if f.name == 'check_in' else 'out'
        %}

        {% set has_photo =
            item.has_in_photo
            if direction == 'in'
            else item.has_out_photo
        %}

        {% if has_photo %}

            {% set photo_url = url_for(
                'attendance_photo',
                record_id=item.id,
                direction=direction
            ) %}

            <div style="margin-top:10px;">

                <a href="{{ photo_url }}"
                   target="_blank"
                   rel="noopener">

                    <img

                        src="{{ photo_url }}"

                        alt="{{ 'Check-in'
                        if direction == 'in'
                        else 'Check-out' }} photo"

                        loading="lazy"

                        style="
                            width:150px;
                            max-width:100%;
                            height:105px;
                            object-fit:cover;
                            border-radius:8px;
                            display:block;
                        "

                    >

                    <small>
                        View full photo
                    </small>

                </a>

            </div>

        {% elif item[f.name] %}

            <div class="muted">

                No photo recorded

            </div>

        {% else %}

            <div class="muted">

                Pending

            </div>

        {% endif %}

    {% endif %}



    {% if f.name=='expiry_date' and item[f.name] and item[f.name]<today %}
    <div class="red">Expired</div>
    {% endif %}

    </td>

    {% endfor %}

    {% if module!='attendance' %}
    

        <td>

            {{ badge(item.status) }}

            {% if module in workflow_modules
                and item.status in active_request_states %}

                <small style="
                    display:block;
                    margin-top:6px;
                    white-space:normal;
                    color:#667080;
                ">

                    {{ approval_stage(
                        item.user_id,
                        module,
                        item.id
                    ) }}

                </small>

            {% endif %}

        </td>

        
    {% endif %}

    <td>

    <div class="inline">

    

    {% if module == 'attendance' %}

        {% if item.check_out %}

            {{ badge('Completed') }}

        {% else %}

            {{ badge('In Progress') }}

        {% endif %}

    {% endif %}

    {% if module=='documents' %}

    <a class="btn btn-small btn-light"
    href="{{ url_for('download_document',record_id=item.id) }}">
    Download
    </a>

    {% endif %}

    {% if can_modify(module,item,'edit') %}

    <a class="btn btn-small btn-light"
    href="{{ url_for('edit_record',module=module,record_id=item.id) }}">
    Edit
    </a>

    {% endif %}

    {% if can_modify(module,item,'delete') %}

    <form method="post"
    action="{{ url_for('delete_record',module=module,record_id=item.id) }}"
    onsubmit="return confirm('Delete this record?')">

    {{ token() }}
    <button class="btn btn-small btn-red">Delete</button>

    </form>

    {% endif %}

    {% if admin() and module!='attendance' %}

    <a class="btn btn-small btn-purple"
    href="{{ url_for('review_record',module=module,record_id=item.id) }}">
    Review
    </a>

    {% endif %}

    {% if module=='tasks' and not admin()
    and allowed('tasks','edit')
    and item.status in ['Pending','In Progress'] %}

    <form method="post"
    action="{{ url_for('complete_task',record_id=item.id) }}">
    {{ token() }}
    <button class="btn btn-small">Complete</button>
    </form>

    {% endif %}

    </div>

    </td>

    </tr>

    {% if module in ['tickets','support'] and item.reply %}

    <tr>
    <td colspan="{{ config.fields|length+3 }}">
    <strong>HR Response:</strong>
    <p class="prewrap">{{ item.reply }}</p>
    {% if module=='tickets' %}
    <p class="muted">Assigned to: {{ item.assigned_to or '—' }}</p>
    {% endif %}
    </td>
    </tr>

    {% endif %}

    {% else %}

    <tr>
    <td colspan="{{ config.fields|length+3 }}" class="empty">
    No records available.
    </td>
    </tr>

    {% endfor %}

    </tbody>
    </table>
    </div>
    </div>

    """,
        module=module,
        config=config,
        records=records,
        staff=staff_list() if admin() else [],
        employee_filter=employee_filter,
        current=current_attendance()
    )


# =========================================================
# EDIT RECORD
# =========================================================

@app.route(
    "/module/<module>/<int:record_id>/edit",
    methods=["GET", "POST"]
)
@login_required
def edit_record(module, record_id):

    config = get_module(module)

    if request.method == "POST":
        db().execute("BEGIN IMMEDIATE")

    item = owned_record(module, record_id, "edit")

    if request.method == "POST":

        data = validate_fields(config["fields"])

        if module == "attendance":

            # Attendance date cannot be moved.
            if data["attendance_date"] != item["attendance_date"]:
                abort(400)

            # A new checkout still requires live camera.
            if bool(data.get("check_out")) != bool(item["check_out"]):
                abort(400)

            for key in ("check_in", "check_out"):

                if data.get(key) and not re.fullmatch(
                    r"[0-9]{2}:[0-9]{2}(:[0-9]{2})?",
                    data[key]
                ):
                    abort(400)

            if not field("correction_reason", 500):

                flash(
                    "Enter a reason for the attendance correction.",
                    "error"
                )

                return redirect(
                    url_for(
                        "edit_record",
                        module=module,
                        record_id=record_id
                    )
                )

        validate_record(module, data)

        if module == "attendance":
            data["check_out"] = data.get("check_out") or None

        if module == "documents" or module in WORKFLOW_MODULES:
            data["status"] = MODULES[module]["initial"]

        if module != "attendance":
            data["status"] = config["initial"]

        keys = list(data)

        sql = (
            f"UPDATE {config['table']} SET "
            + ",".join(key + "=?" for key in keys)
            + " WHERE id=?"
        )

        args = [data[key] for key in keys] + [record_id]

        if config["table"] == "employee_records":

            sql += " AND module=?"
            args.append(module)

        if not admin():

            sql += " AND user_id=?"
            args.append(g.user["id"])


        try:

            cursor = db().execute(sql, args)

            if not cursor.rowcount:

                db().rollback()
                abort(409)

            if module == "attendance":

                details = (

                    f"Attendance #{record_id}: "

                    f"{item['check_in']} / {item['check_out']} -> "

                    f"{data['check_in']} / {data['check_out']}. "

                    f"Reason: {field('correction_reason', 500)}"

                )

                db().execute(

                    "INSERT INTO audit_logs"
                    "(user_id,action,created_at) "
                    "VALUES (?,?,?)",

                    (
                        g.user["id"],
                        details,
                        stamp()
                    )

                )

            db().commit()

        except sqlite3.IntegrityError:

            db().rollback()

            flash("Record conflicts with existing data.", "error")

            return redirect(
                url_for(
                    "edit_record",
                    module=module,
                    record_id=record_id
                )
            )

        audit(f"Edited {module} record #{record_id}")

        flash("Record updated.")

        return redirect(url_for("module_page", module=module))

    return page("Edit " + config["title"], """

    <h1>{{ title }}</h1>

    <div class="card">

    <form method="post">

    {{ token() }}

    <div class="form-grid">

    {% for f in config.fields %}
    {{ input_field(
            f,
            item[f.name],
            readonly=(
                module=='attendance'
                and (
                    f.name=='attendance_date'
                    or (
                        f.name=='check_out'
                        and not item.check_out
                    )
                )
            )
        ) }}
    {% endfor %}

    </div>

    

        {% if module=='attendance' %}

        <div style="
            background:#f3f0f9;
            border:1px solid #ded6ed;
            border-radius:8px;
            padding:15px;
            margin:15px 0;
        ">

            <strong>Attendance Correction</strong>

            <p class="muted">

                Correct existing attendance times only.
                Original camera evidence will be retained.

                A new check-out must be completed
                using the live camera.

            </p>

        </div>

        <label>Reason for correction *</label>

        <textarea
            name="correction_reason"
            maxlength="500"
            placeholder="Enter the reason for correcting attendance"
            required
        ></textarea>

        <br><br>

        {% else %}

        <p class="muted">

            Editing this record resets its review status.

        </p>

        {% endif %}

        <button class="btn">Save Changes</button>

        

    <a class="btn btn-light"
    href="{{ url_for('module_page',module=module) }}">
    Cancel
    </a>

    </form>

    </div>

    """, module=module, config=config, item=item)


# =========================================================
# DELETE RECORD
# =========================================================

@app.post("/module/<module>/<int:record_id>/delete")
@login_required
def delete_record(module, record_id):

    config = get_module(module)

    db().execute("BEGIN IMMEDIATE")

    item = owned_record(module, record_id, "delete")

    table = config["table"]

    sql = f"DELETE FROM {table} WHERE id=?"

    args = [record_id]

    if table == "employee_records":

        sql += " AND module=?"
        args.append(module)

    if not admin():

        sql += " AND user_id=?"
        args.append(g.user["id"])


    cursor = db().execute(sql, args)

    if not cursor.rowcount:

        db().rollback()
        abort(409)

    if module == "attendance":

        db().execute(

            "INSERT INTO audit_logs"
            "(user_id,action,created_at) "
            "VALUES (?,?,?)",

            (
                g.user["id"],

                f"Deleted attendance #{record_id} "
                f"for employee #{item['user_id']}: "
                f"{item['attendance_date']} "
                f"{item['check_in']} / {item['check_out']}",

                stamp()
            )

        )

    db().commit()

    if module == "documents":

        filename = Path(item["filename"]).name

        (UPLOADS / filename).unlink(missing_ok=True)

    audit(f"Deleted {module} record #{record_id}")

    flash("Record deleted successfully.")

    return redirect(url_for("module_page", module=module))


# =========================================================
# HR APPROVALS
# =========================================================

@app.route(
    "/module/<module>/<int:record_id>/review",
    methods=["GET", "POST"]
)
@admin_required
def review_record(module, record_id):

    config = get_module(module)

    if module == "attendance":
        abort(400)

    item = owned_record(module, record_id)

    if request.method == "POST":

        status = field("status")

        if status not in config["statuses"]:
            abort(400)

        data = {"status": status}

        if module in {"support", "tickets"}:
            data["reply"] = field("reply")

        if module == "tickets":
            data["assigned_to"] = field("assigned_to", 200)

        keys = list(data)

        save(
            f"UPDATE {config['table']} SET "
            + ",".join(key + "=?" for key in keys)
            + " WHERE id=?",
            [data[key] for key in keys] + [record_id]
        )

        decision(module, record_id, item["user_id"], status)

        flash("HR decision saved.")

        return redirect(url_for("module_page", module=module))

    return page("HR Review", """

    <h1>HR Review — {{ config.title }}</h1>

    <div class="two-columns">

    <div class="card">

    <h2>Record Information</h2>

    {% for f in config.fields %}

    <div class="activity">
    <strong>{{ f.label }}</strong>
    <p class="prewrap">{{ item[f.name] }}</p>
    </div>

    {% endfor %}

    </div>

    <div class="card">

    <h2>HR Decision</h2>

    <form method="post">

    {{ token() }}

    <div class="form-group">

    <label>Status</label>

    <select name="status">

    {% for status in config.statuses %}
    <option {{ 'selected' if item.status==status else '' }}>
    {{ status }}
    </option>
    {% endfor %}

    </select>

    </div>

    {% if module=='tickets' %}

    <div class="form-group">
    <label>Assigned Department / Officer</label>
    <input name="assigned_to" value="{{ item.assigned_to or '' }}">
    </div>

    {% endif %}

    {% if module in ['support','tickets'] %}

    <div class="form-group">
    <label>HR Response</label>
    <textarea name="reply">{{ item.reply or '' }}</textarea>
    </div>

    {% endif %}

    <button class="btn btn-purple">Save HR Decision</button>

    </form>

    </div>

    </div>

    """, config=config, item=item, module=module)


# =========================================================
# DOCUMENT DOWNLOAD
# =========================================================

@app.route("/document/<int:record_id>/download")
@login_required
def download_document(record_id):

    item = owned_record("documents", record_id)

    audit(f"Downloaded document #{record_id}")

    return send_from_directory(
        str(UPLOADS),
        item["filename"],
        as_attachment=True,
        download_name=item["original_name"]
    )


# =========================================================
# DAILY ATTENDANCE
# =========================================================

def camera_signer():

    return URLSafeTimedSerializer(
        app.secret_key,
        salt="attendance-camera-v1"
    )


# =========================================================
# GENERATE SHORT-LIVED CAMERA SESSION
# =========================================================

@app.post("/attendance/camera-challenge")
@login_required
def attendance_camera_challenge():

    require("attendance", "create")

    action = field("action")

    current = current_attendance()

    if action not in {"in", "out"}:
        abort(400)

    if action == "in" and current:
        abort(409)

    if action == "out" and (
        not current or current["check_out"]
    ):
        abort(409)

    payload = {

        "uid": g.user["id"],

        "action": action,

        "day": today(),

        "nonce": secrets.token_hex(16)

    }

    return {
        "token": camera_signer().dumps(payload)
    }


# =========================================================
# SERVER-SIDE PHOTOGRAPH VALIDATION
# =========================================================

def decode_attendance_photo(value):

    prefix = "data:image/jpeg;base64,"

    if (
        not value.startswith(prefix)
        or len(value) > 2_000_000
    ):

        raise ValueError(
            "A fresh JPEG camera photo is required."
        )

    try:

        raw = base64.b64decode(
            value[len(prefix):],
            validate=True
        )

        if not raw or len(raw) > 1_500_000:

            raise ValueError(
                "Camera photo is too large or empty."
            )

        with Image.open(io.BytesIO(raw)) as img:

            if (
                img.format != "JPEG"
                or not (
                    32 <= img.width <= 2048
                    and 32 <= img.height <= 2048
                )
            ):

                raise ValueError(
                    "Invalid camera photo dimensions or format."
                )

            img.load()

            cleaned = ImageOps.exif_transpose(
                img
            ).convert("RGB")

            cleaned.thumbnail(
                (1280, 1280)
            )

            result = io.BytesIO()

            cleaned.save(
                result,
                format="JPEG",
                quality=70,
                optimize=True
            )

            return result.getvalue()

    except (
        binascii.Error,
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombError
    ) as error:

        raise ValueError(
            "Invalid camera photo."
        ) from error


# =========================================================
# PRIVATE PHOTO VIEWING - HR ONLY
# =========================================================

@app.get("/attendance/photo/<int:record_id>/<direction>")
@login_required
def attendance_photo(record_id, direction):

    require("attendance", "view")

    if direction not in {"in", "out"}:
        abort(404)

    photo = row("""

        SELECT p.jpeg, a.user_id

        FROM attendance_photos p

        JOIN attendance a
        ON a.id = p.attendance_id

        WHERE p.attendance_id = ?
        AND p.action = ?

    """, (record_id, direction))

    if not photo:
        abort(404)

    # HR can view all employee photographs.
    # Employees can only view their own photographs.

    if not admin() and photo["user_id"] != g.user["id"]:
        abort(404)

    return Response(

        photo["jpeg"],

        mimetype="image/jpeg",

        headers={

            "Content-Disposition": "inline",

            "Cache-Control": "private, no-store",

            "X-Content-Type-Options": "nosniff"

        }

    )


@app.post("/attendance/mark")
@login_required
def mark_attendance():

    require("attendance", "create")

    uid = g.user["id"]

    action = field("action")

    timestamp = now()

    day = timestamp.strftime("%Y-%m-%d")

    clock = timestamp.strftime("%H:%M:%S")

    back = url_for(
        "module_page",
        module="attendance"
    )

    if action not in {"in", "out"}:
        abort(400)

    # CHECK CAMERA SESSION AND IMAGE

    try:

        proof = camera_signer().loads(

            request.form.get(
                "camera_token",
                ""
            ),

            max_age=120

        )

        if (

            proof.get("uid") != uid

            or proof.get("action") != action

            or proof.get("day") != day

        ):

            raise ValueError(
                "Invalid camera session."
            )

        photo = decode_attendance_photo(

            request.form.get(
                "camera_photo",
                ""
            )

        )

    except (
        BadData,
        ValueError,
        TypeError,
        AttributeError
    ):

        flash(

            "A valid fresh camera photo is required. "
            "Open the camera and try again.",

            "error"

        )

        return redirect(back)

    connection = db()

    try:

        connection.execute(
            "BEGIN IMMEDIATE"
        )

        # =================================================
        # EMPLOYEE CHECK-IN
        # =================================================

        if action == "in":

            cursor = connection.execute("""

                INSERT INTO attendance
                (
                    user_id,
                    attendance_date,
                    check_in
                )

                VALUES (?,?,?)

            """, (

                uid,
                day,
                clock

            ))

            record_id = cursor.lastrowid

        # =================================================
        # EMPLOYEE CHECK-OUT
        # =================================================

        else:

            existing = connection.execute("""

                SELECT id
                FROM attendance
                WHERE user_id=?
                AND attendance_date=?
                AND check_in IS NOT NULL
                AND check_in!=''
                AND (
                    check_out IS NULL
                    OR check_out=''
                )

            """, (

                uid,
                day

            )).fetchone()

            if not existing:

                connection.rollback()

                flash(

                    "No active check-in found.",

                    "error"

                )

                return redirect(back)

            record_id = existing["id"]

            cursor = connection.execute("""

                UPDATE attendance
                SET check_out=?
                WHERE id=?

                AND (
                    check_out IS NULL
                    OR check_out=''
                )

            """, (

                clock,
                record_id

            ))

            if cursor.rowcount != 1:

                connection.rollback()

                flash(

                    "Attendance has changed. Refresh and try again.",

                    "error"

                )

                return redirect(back)

        # =================================================
        # STORE ATTENDANCE PHOTO
        # =================================================

        connection.execute("""

            INSERT INTO attendance_photos
            (
                attendance_id,
                action,
                jpeg,
                captured_at
            )

            VALUES (?,?,?,?)

        """, (

            record_id,

            action,

            photo,

            timestamp.strftime(
                "%Y-%m-%d %H:%M:%S"
            )

        ))

        # =================================================
        # HR AUDIT TRAIL
        # =================================================

        connection.execute("""

            INSERT INTO audit_logs
            (
                user_id,
                action,
                created_at
            )

            VALUES (?,?,?)

        """, (

            uid,

            (
                "Attendance check-in with photo"
                if action == "in"
                else "Attendance check-out with photo"
            ),

            stamp()

        ))

        connection.commit()

    except sqlite3.IntegrityError:

        connection.rollback()

        flash(

            "Attendance already recorded or has changed.",

            "error"

        )

        return redirect(back)

    except Exception:

        connection.rollback()

        raise

    if action == "in":

        flash(
            "Checked in successfully with photo."
        )

    else:

        flash(
            "Checked out successfully with photo."
        )

    return redirect(back)


# =========================================================
# TASK COMPLETION
# =========================================================

@app.post("/tasks/<int:record_id>/complete")
@login_required
def complete_task(record_id):

    require("tasks", "edit")

    item = row(
        "SELECT * FROM tasks WHERE id=?",
        (record_id,)
    )

    if not item:
        abort(404)

    if not admin() and item["user_id"] != g.user["id"]:
        abort(403)

    if item["status"] not in {"Pending", "In Progress"}:
        abort(400)

    save("""
        UPDATE tasks SET status='Completed'
        WHERE id=?
        AND status IN ('Pending','In Progress')
    """, (record_id,))

    audit(f"Completed task #{record_id}")

    flash("Task completed.")

    return redirect(url_for("module_page", module="tasks"))


# =========================================================
# TICKETING SYSTEM
# =========================================================

@app.route("/CreateTicket")
@login_required
def create_ticket():

    require("tickets", "create")

    return redirect(
        url_for("module_page", module="tickets", new=1)
    )


@app.route("/AllTickets")
@login_required
def all_tickets():

    require("tickets")

    return redirect(url_for("module_page", module="tickets"))


@app.route("/TicketStatus")
@login_required
def ticket_status():

    require("tickets")

    return redirect(url_for("module_page", module="tickets"))


@app.route("/TicketApprovals")
@admin_required
def ticket_approvals():

    return redirect(
        url_for("module_page", module="tickets", status="Open")
    )


# =========================================================
# CENTRAL APPROVAL CENTER
# =========================================================

@app.route("/approvals")
@admin_required
def approvals():

    pending = [{
        "label": "Employee Registrations",
        "value": count("""
            SELECT COUNT(*) FROM users
            WHERE role='Employee' AND status='Pending'
        """),
        "link": url_for("workforce")
    }]

    for module, config in MODULES.items():

        if module == "attendance":
            continue

        sql = (
            f"SELECT COUNT(*) FROM {config['table']} "
            "WHERE status IN ('Pending','Open')"
        )

        args = []

        if config["table"] == "employee_records":

            sql += " AND module=?"
            args.append(module)

        pending.append({
            "label": config["title"],
            "value": count(sql, args),
            "link": url_for("module_page", module=module)
        })

    return page("Approvals", """

    <h1>Central Approval Center</h1>

    <p class="muted">
    Review outstanding employee requests.
    </p>

    <div class="stat-grid">

    {% for item in pending %}

    <a class="stat" href="{{ item.link }}">

    <strong>{{ item.label }}</strong>
    <div class="stat-number">{{ item.value }}</div>
    <p class="muted">Awaiting review</p>

    </a>

    {% endfor %}

    </div>

    """, pending=pending)


def approval_data(approved_only=False):

    sql = """
        SELECT h.*,u.name AS employee_name,
        a.name AS reviewer
        FROM approval_history h
        LEFT JOIN users u ON u.id=h.employee_id
        LEFT JOIN users a ON a.id=h.decided_by
    """

    if approved_only:

        sql += """
            WHERE h.decision IN
            ('Approved','Verified','Completed','Resolved')
        """

    return rows(sql + " ORDER BY h.id DESC")


APPROVAL_COLUMNS = [
    ("Module", "module"),
    ("Record ID", "record_id"),
    ("Employee", "employee_name"),
    ("Decision", "decision"),
    ("Reviewed By", "reviewer"),
    ("Date", "created_at")
]


@app.route("/approval-history")
@admin_required
def approval_history():

    return table_page(
        "Approval History",
        approval_data(),
        APPROVAL_COLUMNS
    )


@app.route("/approved")
@admin_required
def approved():

    return table_page(
        "Approved Requests",
        approval_data(True),
        APPROVAL_COLUMNS
    )


# =========================================================
# DEPARTMENTS
# =========================================================

@app.route("/departments", methods=["GET", "POST"])
@admin_required
def departments():

    if request.method == "POST":

        name = field("name", 100)

        if not name:
            abort(400)

        try:

            insert("departments", {
                "name": name,
                "manager": field("manager", 120),
                "description": field("description")
            })

            audit("Created department: " + name)
            flash("Department created.")

        except sqlite3.IntegrityError:

            db().rollback()
            flash("Department already exists.", "error")

        return redirect(url_for("departments"))

    return page("Departments", """

    <h1>Department Management</h1>

    <div class="card">

    <h2>Create Department</h2>

    <form method="post">

    {{ token() }}

    <div class="form-grid">

    <div class="form-group">
    <label>Department Name</label>
    <input name="name" required>
    </div>

    <div class="form-group">
    <label>Department Manager</label>
    <input name="manager">
    </div>

    </div>

    <div class="form-group">
    <label>Description</label>
    <textarea name="description"></textarea>
    </div>

    <button class="btn">Add Department</button>

    </form>

    </div>

    """ + TABLE,
        table_title="Company Departments",
        records=rows("""
            SELECT d.*,
            (
                SELECT COUNT(*) FROM users u
                WHERE u.department=d.name
                AND u.status='Approved'
                AND u.role='Employee'
            ) AS employees
            FROM departments d ORDER BY d.name
        """),
        columns=[
            ("Department", "name"),
            ("Manager", "manager"),
            ("Employees", "employees"),
            ("Description", "description")
        ]
    )


# =========================================================
# JOB ROLES
# =========================================================

@app.route("/roles", methods=["GET", "POST"])
@admin_required
def roles():

    if request.method == "POST":

        action = field("action")

        if action == "create":

            name = field("name", 100)

            if not name:
                abort(400)

            try:

                insert("job_roles", {
                    "name": name,
                    "description": field("description")
                })

                audit("Created job role: " + name)
                flash("Job role created.")

            except sqlite3.IntegrityError:

                db().rollback()
                flash("Role already exists.", "error")

        elif action == "assign":

            uid = selected_employee()

            role_id = request.form.get("role_id", type=int)

            if not row(
                "SELECT id FROM job_roles WHERE id=?",
                (role_id,)
            ):
                abort(400)

            save("""
                INSERT INTO employee_roles(user_id,role_id)
                VALUES (?,?)
                ON CONFLICT(user_id)
                DO UPDATE SET role_id=excluded.role_id
            """, (uid, role_id))

            audit(f"Assigned job role to employee #{uid}")

            flash("Job role assigned.")

        else:

            abort(400)

        return redirect(url_for("roles"))

    return page("Roles", """

    <h1>Employee Role Management</h1>

    <p class="muted">
    Job roles are separate from administrator privileges.
    </p>

    <div class="two-columns">

    <div class="card">

    <h2>Create Role</h2>

    <form method="post">

    {{ token() }}

    <input type="hidden" name="action" value="create">

    <div class="form-group">
    <label>Role Name</label>
    <input name="name" required>
    </div>

    <div class="form-group">
    <label>Description</label>
    <textarea name="description"></textarea>
    </div>

    <button class="btn">Create Role</button>

    </form>

    </div>

    <div class="card">

    <h2>Assign Employee Role</h2>

    <form method="post">

    {{ token() }}

    <input type="hidden" name="action" value="assign">

    {{ employee_picker(staff) }}

    <div class="form-group">

    <label>Job Role</label>

    <select name="role_id">

    {% for role in role_list %}
    <option value="{{ role.id }}">{{ role.name }}</option>
    {% endfor %}

    </select>

    </div>

    <button class="btn">Assign Role</button>

    </form>

    </div>

    </div>

    """ + TABLE,
        staff=staff_list(),
        role_list=rows(
            "SELECT * FROM job_roles ORDER BY name"
        ),
        table_title="Employee Role Assignments",
        records=rows("""
            SELECT u.name AS employee_name,
            r.name AS role_name,u.department
            FROM employee_roles e
            JOIN users u ON u.id=e.user_id
            JOIN job_roles r ON r.id=e.role_id
        """),
        columns=[
            ("Employee", "employee_name"),
            ("Role", "role_name"),
            ("Department", "department")
        ]
    )


# =========================================================
# HR ANALYTICS
# =========================================================

@app.route("/HRAnalytics")
@admin_required
def analytics():

    employees = count("""
        SELECT COUNT(*) FROM users
        WHERE role='Employee' AND status='Approved'
    """)

    present = count("""
        SELECT COUNT(*) FROM attendance a
        JOIN users u ON u.id=a.user_id
        WHERE a.attendance_date=?
        AND u.status='Approved'
        AND u.role='Employee'
    """, (today(),))

    task_total = count("SELECT COUNT(*) FROM tasks")

    task_completed = count("""
        SELECT COUNT(*) FROM tasks
        WHERE status='Completed'
    """)

    completion = (
        round(task_completed / task_total * 100, 1)
        if task_total else 0
    )

    departments_data = rows("""
        SELECT department,COUNT(*) AS total
        FROM users
        WHERE role='Employee' AND status='Approved'
        GROUP BY department
        ORDER BY total DESC
    """)

    statistics = [
        ("Active Employees", employees),
        ("Present Today", present),
        ("Not Checked In", employees - present),
        ("Task Completion", str(completion) + "%")
    ]

    return page("HR Analytics", """

    <h1>HR Analytics Dashboard</h1>

    <p class="muted">Live company statistics.</p>

    <div class="stat-grid">

    {% for label,value in statistics %}

    <div class="stat">
    {{ label }}
    <div class="stat-number">{{ value }}</div>
    </div>

    {% endfor %}

    </div>

    <div class="two-columns">

    <div class="card">

    <h2>Employees by Department</h2>

    {% for item in departments_data %}

    <div class="activity">

    <strong>{{ item.department }}</strong>

    <p>{{ item.total }} employees</p>

    <div class="progress">
    <div style="width:{{ item.total/employees*100 if employees else 0 }}%">
    </div>
    </div>

    </div>

    {% else %}

    <p>No employees registered.</p>

    {% endfor %}

    </div>

    <div class="card">

    <h2>Task Completion</h2>

    <div class="stat-number">{{ completion }}%</div>

    <p>{{ task_completed }} of {{ task_total }} tasks completed.</p>

    <div class="progress">
    <div style="width:{{ completion }}%"></div>
    </div>

    </div>

    </div>

    """,
        employees=employees,
        statistics=statistics,
        departments_data=departments_data,
        completion=completion,
        task_total=task_total,
        task_completed=task_completed
    )


# =========================================================
# PERFORMANCE SCORES
# =========================================================

def performance_data():

    sql = """
        SELECT id,name,department FROM users
        WHERE role='Employee' AND status='Approved'
    """

    args = []

    if not admin():

        sql += " AND id=?"
        args.append(g.user["id"])

    result = []

    for employee in rows(sql, args):

        uid = employee["id"]

        total = count("""
            SELECT COUNT(*) FROM tasks WHERE user_id=?
        """, (uid,))

        completed = count("""
            SELECT COUNT(*) FROM tasks
            WHERE user_id=? AND status='Completed'
        """, (uid,))

        task_score = (
            round(completed / total * 100, 1)
            if total else None
        )

        kpi = row("""
            SELECT AVG(amount) AS score
            FROM employee_records
            WHERE user_id=? AND module='kpi'
            AND status IN ('Approved','Completed')
        """, (uid,))["score"]

        review = row("""
            SELECT AVG(amount) AS score
            FROM employee_records
            WHERE user_id=? AND module='performance'
            AND status IN ('Approved','Completed')
        """, (uid,))["score"]

        overall = None

        if all(x is not None for x in [
            task_score, kpi, review
        ]):

            overall = round(
                task_score * .30 + kpi * .30 + review * .40,
                1
            )

        result.append({
            "employee_name": employee["name"],
            "department": employee["department"],
            "total": total,
            "completed": completed,
            "task_score": task_score,
            "kpi": round(kpi, 1) if kpi is not None else None,
            "review": round(review, 1) if review is not None else None,
            "overall": overall
        })

    return result


@app.route("/EmployeeScoreBreakdown")
@login_required
def scores():

    for module in ["scores", "tasks", "kpi", "performance"]:
        require(module)

    return table_page(
        "Employee Score Breakdown",
        performance_data(),
        [
            ("Employee", "employee_name"),
            ("Department", "department"),
            ("Task Score", "task_score"),
            ("KPI", "kpi"),
            ("HR Review", "review"),
            ("Overall / 100", "overall")
        ],
        "30% task completion, 30% KPI and 40% HR review. "
        "All components must be available to calculate an overall score."
    )


@app.route("/task-performance")
@login_required
def task_performance():

    require("task-performance")
    require("tasks")

    return table_page(
        "Task Performance",
        performance_data(),
        [
            ("Employee", "employee_name"),
            ("Total Tasks", "total"),
            ("Completed", "completed"),
            ("Completion %", "task_score")
        ]
    )


# =========================================================
# WORK HOURS
# =========================================================

@app.route("/work-hours")
@login_required
def work_hours():

    for module in [
        "work-hours", "attendance", "timesheets", "extra-work"
    ]:
        require(module)

    employees = staff_list() if admin() else [g.user]

    result = []

    for employee in employees:

        uid = employee["id"]

        hours = row("""
            SELECT COALESCE(SUM(
                (
                    julianday(attendance_date || ' ' || check_out)
                    -
                    julianday(attendance_date || ' ' || check_in)
                )*24
            ),0) AS hours
            FROM attendance
            WHERE user_id=?
            AND check_out IS NOT NULL
            AND check_out!=''
            AND check_out>=check_in
        """, (uid,))["hours"]

        timesheets = row("""
            SELECT COALESCE(SUM(amount),0) AS hours
            FROM employee_records
            WHERE user_id=? AND module='timesheets'
            AND status IN ('Approved','Completed')
        """, (uid,))["hours"]

        overtime = row("""
            SELECT COALESCE(SUM(amount),0) AS hours
            FROM employee_records
            WHERE user_id=? AND module='extra-work'
            AND status IN ('Approved','Completed')
        """, (uid,))["hours"]

        result.append({
            "name": employee["name"],
            "attendance": round(hours, 2),
            "timesheets": round(timesheets, 2),
            "overtime": round(overtime, 2)
        })

    return table_page(
        "Work Hours",
        result,
        [
            ("Employee", "name"),
            ("Attendance Hours", "attendance"),
            ("Approved Timesheets", "timesheets"),
            ("Approved Overtime", "overtime")
        ],
        "Hours are reported separately to avoid double counting."
    )


# =========================================================
# TEAM CALENDAR
# =========================================================

@app.route("/team-calendar")
@admin_required
def calendar_page():

    events = []

    for item in rows("""
        SELECT r.*,u.name AS employee_name
        FROM employee_records r
        JOIN users u ON u.id=r.user_id
        WHERE r.module='team-calendar'
        AND r.status IN ('Approved','Completed')
    """):

        events.append({
            "date": item["record_date"],
            "name": item["employee_name"],
            "event": item["title"],
            "type": "Calendar Event"
        })

    for item in rows("""
        SELECT l.*,u.name AS employee_name
        FROM leaves l
        JOIN users u ON u.id=l.user_id
        WHERE l.status='Approved'
    """):

        events.append({
            "date": item["start_date"],
            "name": item["employee_name"],
            "event": item["leave_type"] + " to " + item["end_date"],
            "type": "Approved Leave"
        })

    for item in rows("""
        SELECT t.*,u.name AS employee_name
        FROM tasks t
        JOIN users u ON u.id=t.user_id
        WHERE deadline IS NOT NULL AND deadline!=''
    """):

        events.append({
            "date": item["deadline"],
            "name": item["employee_name"],
            "event": item["title"],
            "type": "Task Deadline"
        })

    events.sort(key=lambda item: item["date"] or "")

    return page("Team Calendar", """

    <div class="page-heading">

    <h1>Team Calendar</h1>

    <a class="btn"
    href="{{ url_for('module_page',module='team-calendar',new=1) }}">
    + Create Event
    </a>

    </div>

    """ + TABLE,
        table_title="Company Schedule",
        records=events,
        columns=[
            ("Date", "date"),
            ("Employee", "name"),
            ("Event", "event"),
            ("Type", "type")
        ]
    )


# =========================================================
# EMPLOYEE TIMELINE
# =========================================================

@app.route("/timeline")
@login_required
def timeline():

    require("timeline")

    uid = (
        request.args.get("employee", g.user["id"], type=int)
        if admin() else g.user["id"]
    )

    employee = row(
        "SELECT id,name FROM users WHERE id=?",
        (uid,)
    )

    if not employee:
        abort(404)

    events = []

    for item in rows("""
        SELECT module,title,created_at
        FROM employee_records WHERE user_id=?
    """, (uid,)):

        if not admin():

            if item["module"] == "hr-notes":
                continue

            if not allowed(item["module"]):
                continue

        events.append({
            "event": item["module"].replace("-", " ").title()
            + ": " + item["title"],
            "date": item["created_at"]
        })

    if allowed("tasks"):

        for item in rows("""
            SELECT title,created_at FROM tasks WHERE user_id=?
        """, (uid,)):

            events.append({
                "event": "Task: " + item["title"],
                "date": item["created_at"]
            })

    if allowed("documents"):

        for item in rows("""
            SELECT title,uploaded_at FROM documents WHERE user_id=?
        """, (uid,)):

            events.append({
                "event": "Document: " + item["title"],
                "date": item["uploaded_at"]
            })

    if admin():

        for item in rows("""
            SELECT action,created_at FROM audit_logs
            WHERE user_id=? ORDER BY id DESC LIMIT 500
        """, (uid,)):

            events.append({
                "event": item["action"],
                "date": item["created_at"]
            })

    events.sort(
        key=lambda item: item["date"] or "",
        reverse=True
    )

    return page("Timeline", """

    <h1>Employee Timeline</h1>

    <p class="muted">{{ employee.name }}</p>

    {% if admin() %}

    <div class="card">

    <form method="get">

    <label>Select Employee</label>

    <select name="employee" onchange="this.form.submit()">

    {% for item in staff %}

    <option value="{{ item.id }}"
    {{ 'selected' if item.id==employee.id else '' }}>
    {{ item.name }}
    </option>

    {% endfor %}

    </select>

    </form>

    </div>

    {% endif %}

    """ + TABLE,
        employee=employee,
        staff=staff_list() if admin() else [],
        table_title="Employee Activity",
        records=events,
        columns=[
            ("Activity", "event"),
            ("Date", "date")
        ]
    )


# =========================================================
# COMPANY COMMUNICATION
# =========================================================

@app.route("/Communication", methods=["GET", "POST"])
@login_required
def communication():

    require("announcements")

    if request.method == "POST":

        if not admin():
            abort(403)

        title = field("title", 150)
        message = field("message")

        if not title or not message:
            abort(400)

        insert("announcements", {
            "title": title,
            "message": message,
            "created_at": stamp()
        })

        audit("Published announcement")

        flash("Announcement published.")

        return redirect(url_for("communication"))

    return page("Communication", """

    <h1>Company Communication</h1>

    <p class="muted">Official company announcements and HR updates.</p>

    {% if admin() %}

    <div class="card">

    <h2>Publish Announcement</h2>

    <form method="post">

    {{ token() }}

    <div class="form-group">
    <label>Title</label>
    <input name="title" required>
    </div>

    <div class="form-group">
    <label>Message</label>
    <textarea name="message" required></textarea>
    </div>

    <button class="btn btn-purple">Publish</button>

    </form>
    </div>

    {% endif %}

    {% for item in records %}

    <div class="card">

    <h2>{{ item.title }}</h2>

    <p class="prewrap">{{ item.message }}</p>

    <p class="muted">Human Resources · {{ item.created_at }}</p>

    {% if admin() %}

    <div class="inline">

    <a class="btn btn-small btn-light"
    href="{{ url_for('edit_announcement',record_id=item.id) }}">
    Edit
    </a>

    <form method="post"
    action="{{ url_for('delete_announcement',record_id=item.id) }}"
    onsubmit="return confirm('Delete announcement?')">

    {{ token() }}

    <button class="btn btn-small btn-red">Delete</button>

    </form>

    </div>

    {% endif %}

    </div>

    {% else %}

    <div class="card empty">No announcements available.</div>

    {% endfor %}

    """, records=rows("""
        SELECT * FROM announcements ORDER BY id DESC
    """))


@app.route(
    "/announcement/<int:record_id>/edit",
    methods=["GET", "POST"]
)
@admin_required
def edit_announcement(record_id):

    item = row(
        "SELECT * FROM announcements WHERE id=?",
        (record_id,)
    )

    if not item:
        abort(404)

    if request.method == "POST":

        title = field("title", 150)
        message = field("message")

        if not title or not message:
            abort(400)

        save("""
            UPDATE announcements
            SET title=?,message=? WHERE id=?
        """, (title, message, record_id))

        audit(f"Edited announcement #{record_id}")

        flash("Announcement updated.")

        return redirect(url_for("communication"))

    return page("Edit Announcement", """

    <h1>Edit Announcement</h1>

    <div class="card">

    <form method="post">

    {{ token() }}

    <div class="form-group">
    <label>Title</label>
    <input name="title" value="{{ item.title }}" required>
    </div>

    <div class="form-group">
    <label>Message</label>
    <textarea name="message" required>{{ item.message }}</textarea>
    </div>

    <button class="btn">Save Changes</button>

    </form>

    </div>

    """, item=item)


@app.post("/announcement/<int:record_id>/delete")
@admin_required
def delete_announcement(record_id):

    save(
        "DELETE FROM announcements WHERE id=?",
        (record_id,)
    )

    audit(f"Deleted announcement #{record_id}")

    flash("Announcement deleted.")

    return redirect(url_for("communication"))


# =========================================================
# MY PROFILE
# =========================================================

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():

    require("profile")

    if request.method == "POST":

        require("profile", "edit")

        save("""
            UPDATE users SET phone=? WHERE id=?
        """, (field("phone", 30), g.user["id"]))

        audit("Updated profile")

        flash("Profile saved.")

        return redirect(url_for("profile"))

    return page("My Profile", """

    <h1>My Profile</h1>

    <div class="card">

    <h2>{{ g.user.name }}</h2>

    {{ badge(g.user.status) }}

    <br><br>

    <form method="post">

    {{ token() }}

    <div class="form-grid">

    {% for label,key in [
        ('Full Name','name'),
        ('Company Email','email'),
        ('Employee ID','employee_code'),
        ('Department','department'),
        ('Designation','designation'),
        ('Joining Date','joining_date'),
        ('Access Role','role')
    ] %}

    <div class="form-group">
    <label>{{ label }}</label>
    <input value="{{ g.user[key] or '' }}" readonly>
    </div>

    {% endfor %}

    <div class="form-group">
    <label>Phone Number</label>
    <input name="phone" value="{{ g.user.phone or '' }}"
    {{ 'readonly' if not allowed('profile','edit') else '' }}>
    </div>

    </div>

    {% if allowed('profile','edit') %}
    <button class="btn">Save Profile</button>
    {% else %}
    <p class="muted">Your profile access is read-only.</p>
    {% endif %}

    </form>

    </div>

    """)


# =========================================================
# THEME / LANGUAGE / TIME SETTINGS
# =========================================================

@app.route("/preferences", methods=["GET", "POST"])
@login_required
def preferences():

    require("settings")

    fields = [

        F("theme", "Website Theme", "select", True, [
            "Light", "Dark", "Soft", "Contrast"
        ]),

        F("language", "Preferred Language", "select", True, [
            "English", "Arabic", "Hindi",
            "Urdu", "Malayalam", "Tamil", "Tagalog"
        ]),

        F("timezone", "Display Time Zone", "select", True, [
            "Asia/Dubai", "Asia/Kolkata", "Asia/Karachi",
            "Asia/Manila", "Europe/London", "UTC"
        ]),

        F("date_format", "Date Format", "select", True, [
            "%d %B %Y", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d"
        ])

    ]

    if request.method == "POST":

        require("settings", "edit")

        data = validate_fields(fields)

        save("""
            INSERT INTO preferences
            (user_id,theme,language,timezone,date_format)
            VALUES (?,?,?,?,?)
            ON CONFLICT(user_id)
            DO UPDATE SET
                theme=excluded.theme,
                language=excluded.language,
                timezone=excluded.timezone,
                date_format=excluded.date_format
        """, (
            g.user["id"],
            data["theme"],
            data["language"],
            data["timezone"],
            data["date_format"]
        ))

        audit("Updated personal settings")

        flash("Settings saved.")

        return redirect(url_for("preferences"))

    return page("Personal Settings", """

    <h1>Personal Settings</h1>

    <div class="card">

    <h2>Theme, Language, Time & Date</h2>

    <form method="post">

    {{ token() }}

    <div class="form-grid">

    {% for f in fields %}

    {{ input_field(
        f,preference[f.name],
        not allowed('settings','edit')
    ) }}

    {% endfor %}

    </div>

    <div class="clock display-clock"></div>

    <p class="muted">
    Preferred language is saved as a profile preference.
    Full interface translation is not included.
    Attendance always uses official UAE time.
    </p>

    {% if allowed('settings','edit') %}
    <button class="btn">Save Settings</button>
    {% endif %}

    </form>

    </div>

    """, fields=fields)


# =========================================================
# PASSWORD SETTINGS
# =========================================================

@app.route("/settings/password", methods=["GET", "POST"])
@login_required
def password_settings():

    if request.method == "POST":

        old = request.form.get("old_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm", "")

        if not check_password_hash(g.user["password"], old):

            flash("Incorrect current password.", "error")

        elif not 10 <= len(new) <= 256:

            flash(
                "Use a password containing at least 10 characters.",
                "error"
            )

        elif new != confirm:

            flash("Passwords do not match.", "error")

        elif new == old:

            flash("Choose a different password.", "error")

        else:

            save("""
                UPDATE users
                SET password=?,must_change_password=0
                WHERE id=?
            """, (
                generate_password_hash(new),
                g.user["id"]
            ))

            audit("Changed account password")

            session.clear()

            flash("Password updated. Sign in again.")

            return redirect(url_for("login"))

    return page("Change Password", """

    <h1>Account Security</h1>

    <div class="card" style="max-width:650px">

    {% if g.user.must_change_password %}

    <div class="flash">
    You must change your temporary password
    before accessing the employee portal.
    </div>

    {% endif %}

    <h2>Change Password</h2>

    <form method="post">

    {{ token() }}

    <div class="form-group">
    <label>Current Password</label>
    <input type="password" name="old_password" required>
    </div>

    <div class="form-group">
    <label>New Password</label>
    <input type="password" name="new_password" minlength="10" required>
    </div>

    <div class="form-group">
    <label>Confirm New Password</label>
    <input type="password" name="confirm" required>
    </div>

    <button class="btn">Update Password</button>

    </form>

    </div>

    """)


# =========================================================
# REPORTS AND CSV EXPORT
# =========================================================

REPORTS = {

    "employees": (
        "Employee Directory",
        """
        SELECT name,email,employee_code,department,
        designation,joining_date,status
        FROM users WHERE role='Employee'
        """
    ),

    "attendance": (
        "Attendance Report",
        """
        SELECT u.name AS employee,
        a.attendance_date,a.check_in,a.check_out
        FROM attendance a
        JOIN users u ON u.id=a.user_id
        """
    ),

    "documents": (
        "Document Report",
        """
        SELECT u.name AS employee,
        d.title,d.category,d.expiry_date,d.status
        FROM documents d
        JOIN users u ON u.id=d.user_id
        """
    ),

    "tasks": (
        "Task Report",
        """
        SELECT u.name AS employee,
        t.title,t.deadline,t.priority,t.status
        FROM tasks t
        JOIN users u ON u.id=t.user_id
        """
    ),

    "leaves": (
        "Vacation Report",
        """
        SELECT u.name AS employee,
        l.leave_type,l.start_date,l.end_date,l.status
        FROM leaves l
        JOIN users u ON u.id=l.user_id
        """
    ),

    "tickets": (
        "Ticket Report",
        """
        SELECT u.name AS employee,
        t.subject,t.category,t.priority,t.status
        FROM tickets t
        JOIN users u ON u.id=t.user_id
        """
    )

}

for module in [
    "timesheets", "extra-work", "performance",
    "kpi", "assets", "trainings",
    "transportation", "contracts", "onboarding"
]:

    REPORTS[module] = (
        MODULES[module]["title"] + " Report",
        """
        SELECT u.name AS employee,
        r.title,r.record_date,r.end_date,r.amount,r.status
        FROM employee_records r
        JOIN users u ON u.id=r.user_id
        WHERE r.module='""" + module + """'
        """
    )


@app.route("/AdvancedReports")
@admin_required
def reports():

    return page("Reports", """

    <h1>Company Reports</h1>

    <p class="muted">
    Download company reports compatible with Microsoft Excel.
    </p>

    <div class="metro-grid">

    {% for key,config in reports_data.items() %}

    <a class="metro-tile"
    href="{{ url_for('download_report',report_type=key) }}">

    <div class="metro-icon teal">
    {{ icon('file') }}
    </div>

    <div class="metro-title">{{ config[0] }}</div>

    <span class="muted">Download CSV</span>

    </a>

    {% endfor %}

    </div>

    """, reports_data=REPORTS)


def csv_safe(value):

    if value is None:
        return ""

    if isinstance(value, str):

        if value.lstrip().startswith(
            ("=", "+", "-", "@")
        ) or value.startswith(("\t", "\r", "\n")):

            return "'" + value

    return value


@app.route("/report/<report_type>/download")
@admin_required
def download_report(report_type):

    config = REPORTS.get(report_type)

    if not config:
        abort(404)

    cursor = db().execute(config[1])

    output = io.StringIO()

    writer = csv.writer(output)

    writer.writerow([
        column[0] for column in cursor.description
    ])

    for item in cursor.fetchall():

        writer.writerow([
            csv_safe(value) for value in item
        ])

    audit(f"Exported report: {report_type}")

    return Response(
        "\ufeff" + output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition":
            f"attachment; filename=star_link_{report_type}.csv"
        }
    )


# =========================================================
# AUDIT TRAIL
# =========================================================

@app.route("/AuditTrail")
@app.route("/audit-logs")
@admin_required
def audit_trail():

    return table_page(
        "System Audit Trail",
        rows("""
            SELECT a.*,u.name,u.email
            FROM audit_logs a
            LEFT JOIN users u ON u.id=a.user_id
            ORDER BY a.id DESC LIMIT 1000
        """),
        [
            ("Employee", "name"),
            ("Email", "email"),
            ("Activity", "action"),
            ("Date & Time", "created_at")
        ],
        "Recorded system activities. Audit logs cannot be modified "
        "through the employee portal."
    )


# =========================================================
# PREVIOUS ROUTE COMPATIBILITY
# =========================================================

ALIASES = {
    "/documents": "documents",
    "/attendance": "attendance",
    "/tasks": "tasks",
    "/leaves": "leaves",
    "/support": "support",
    "/Timesheets": "timesheets",
    "/ExtraWork": "extra-work",
    "/Onboarding": "onboarding",
    "/PerformanceManagement": "performance",
    "/Transportation": "transportation"
}


def make_alias(module):

    @login_required
    def alias():

        require(module)

        return redirect(url_for("module_page", module=module))

    return alias


for index, (path, module) in enumerate(ALIASES.items()):

    app.add_url_rule(
        path,
        endpoint=f"legacy_module_{index}",
        view_func=make_alias(module)
    )


@app.route("/announcements")
@login_required
def announcement_alias():

    require("announcements")

    return redirect(url_for("communication"))


@app.route("/settings")
@login_required
def settings_alias():

    return redirect(url_for("password_settings"))


# =========================================================
# ERROR HANDLING
# =========================================================

@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(409)
@app.errorhandler(413)
def handle_error(error):

    messages = {

        400: "Invalid request. Refresh the page and check your information.",

        403: "Access denied. Please contact Human Resources.",

        404: "The requested page or record does not exist.",

        409: "The record has changed. Refresh the page and try again.",

        413: "The uploaded file exceeds the 16 MB request limit."
    }

    return page("Access Notice", """

    <div class="card" style="max-width:650px;margin:40px auto">

    <div class="metro-icon purple" style="width:75px">
    {{ icon('lock') }}
    </div>

    <br>

    <h1>{{ error_code }}</h1>

    <p>{{ error_message }}</p>

    <a class="btn btn-purple" href="/">
    Return to Portal
    </a>

    </div>

    """,
        error_code=error.code,
        error_message=messages.get(error.code, "An error occurred.")
    ), error.code


# =========================================================
# SECURITY HEADERS
# =========================================================

@app.after_request
def security_headers(response):

    response.headers["X-Content-Type-Options"] = "nosniff"

    response.headers["X-Frame-Options"] = "DENY"

    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(self), microphone=()"

    response.headers["Cache-Control"] = "no-store"

    return response


# =========================================================
# HR ADMIN PASSWORD RESET
# =========================================================

def reset_admin_password():

    administrator = row("""
        SELECT id,email FROM users
        WHERE role='Admin'
        ORDER BY id LIMIT 1
    """)

    if not administrator:

        print("Administrator account not found.")
        return

    password = getpass.getpass(
        "Enter new HR administrator password: "
    )

    confirm = getpass.getpass(
        "Confirm password: "
    )

    if not password or password != confirm:

        print("Passwords do not match.")
        return

    save("""
        UPDATE users
        SET password=?,status='Approved',
        must_change_password=0
        WHERE id=? AND role='Admin'
    """, (
        generate_password_hash(password),
        administrator["id"]
    ))

    print("HR password reset successfully.")
    print("Email:", administrator["email"])


# =========================================================
# INITIALIZE DATABASE
# =========================================================



# =========================================================
# STAR LINK WORKFLOW V2
# =========================================================

from PIL import Image, ImageOps

CORE_EMPLOYEE_MODULES = (

    "attendance",
    "timesheets",
    "extra-work",
    "support",
    "leaves",
    "documents",
    "tasks",
    "tickets"

)

WORKFLOW_MODULES = (

    "timesheets",
    "extra-work",
    "leaves",
    "support",
    "tickets"

)

DEFAULT_REQUIRED_DOCUMENTS = (

    "Passport",
    "Emirates ID",
    "Residence Visa"

)

ACTIVE_REQUEST_STATES = (

    "Pending",
    "Open",
    "In Progress"

)


# =========================================================
# DATABASE MIGRATION
# PRESERVE ALL EXISTING TABLES AND RECORDS
# =========================================================

original_init_db = init_db


def init_db():

    original_init_db()

    connection = db()

    connection.execute("BEGIN IMMEDIATE")

    try:

        columns = {

            item[1]

            for item in connection.execute(
                "PRAGMA table_info(users)"
            )

        }

        for name, datatype in [

            ("project", "TEXT"),

            ("onboarded_at", "TEXT"),

            ("required_docs", "TEXT")

        ]:

            if name not in columns:

                connection.execute(

                    f"ALTER TABLE users "
                    f"ADD COLUMN {name} {datatype}"

                )

        # PROFILE PHOTOGRAPHS

        connection.execute("""

            CREATE TABLE IF NOT EXISTS profile_photos (

                user_id INTEGER PRIMARY KEY,

                jpeg BLOB NOT NULL,

                FOREIGN KEY(user_id)
                REFERENCES users(id)
                ON DELETE CASCADE

            )

        """)

        # INDIVIDUAL EMPLOYEE APPROVAL CONFIGURATION

        connection.execute("""

            CREATE TABLE IF NOT EXISTS approval_routes (

                user_id INTEGER NOT NULL,

                module TEXT NOT NULL,

                lead_id INTEGER,

                manager_id INTEGER,

                PRIMARY KEY(user_id,module),

                FOREIGN KEY(user_id)
                REFERENCES users(id),

                FOREIGN KEY(lead_id)
                REFERENCES users(id),

                FOREIGN KEY(manager_id)
                REFERENCES users(id)

            )

        """)

        # APPROVAL DECISIONS

        connection.execute("""

            CREATE TABLE IF NOT EXISTS delegated_approvals (

                module TEXT NOT NULL,

                record_id INTEGER NOT NULL,

                reviewer_id INTEGER NOT NULL,

                decided_at TEXT NOT NULL,

                PRIMARY KEY(
                    module,
                    record_id,
                    reviewer_id
                ),

                FOREIGN KEY(reviewer_id)
                REFERENCES users(id)

            )

        """)

        connection.execute("""

            CREATE INDEX IF NOT EXISTS idx_approval_lead
            ON approval_routes(lead_id)

        """)

        connection.execute("""

            CREATE INDEX IF NOT EXISTS idx_approval_manager
            ON approval_routes(manager_id)

        """)

        # When a request is edited, its previous approvals
        # must be invalidated.

        trigger_config = [

            (
                "employee_records",
                ("timesheets", "extra-work"),
                "title,details,record_date,end_date,amount,user_id"
            ),

            (
                "leaves",
                ("leaves",),
                "leave_type,start_date,end_date,reason,user_id"
            ),

            (
                "support",
                ("support",),
                "category,subject,message,user_id"
            ),

            (
                "tickets",
                ("tickets",),
                "category,subject,description,priority,user_id"
            )

        ]

        for table, modules, fields in trigger_config:

            for module in modules:

                trigger_name = module.replace("-", "_")

                predicate = (

                    f" WHEN OLD.module='{module}'"

                    if table == "employee_records"

                    else ""

                )

                connection.execute(f"""

                    CREATE TRIGGER IF NOT EXISTS
                    sl_reset_{trigger_name}

                    AFTER UPDATE OF {fields}
                    ON {table}

                    {predicate}

                    BEGIN

                        DELETE FROM delegated_approvals

                        WHERE module='{module}'

                        AND record_id=OLD.id;

                    END

                """)

                connection.execute(f"""

                    CREATE TRIGGER IF NOT EXISTS
                    sl_delete_{trigger_name}

                    AFTER DELETE ON {table}

                    {predicate}

                    BEGIN

                        DELETE FROM delegated_approvals

                        WHERE module='{module}'

                        AND record_id=OLD.id;

                    END

                """)

        connection.commit()

    except Exception:

        connection.rollback()

        raise


# =========================================================
# EMPLOYEE DEFAULT PERMISSIONS
# HR CAN STILL GRANT OR REMOVE ACCESS
# =========================================================

def default_workflow_permissions():

    return {

        module: {

            "can_view": 1,

            "can_create": int(
                "create" in supported_actions(module)
            ),

            "can_edit": 0,

            "can_delete": 0

        }

        for module in CORE_EMPLOYEE_MODULES

    }


original_allowed = allowed


def allowed(module, action="view"):

    if not g.user:

        return False

    if admin():

        return True

    # Everyone must have access to their own profile.

    if module == "profile":

        return action in ("view", "edit")

    # Existing explicit HR permissions take priority.

    if (
        module in CORE_EMPLOYEE_MODULES
        and module not in g.permissions
    ):

        return (

            action in ("view", "create")

            and action in supported_actions(module)

        )

    return original_allowed(module, action)


app.jinja_env.globals["allowed"] = allowed


if "Rejected" not in MODULES["support"]["statuses"]:

    MODULES["support"]["statuses"].append("Rejected")


# =========================================================
# REQUIRED DOCUMENT CONFIGURATION
# =========================================================

def required_documents(user):

    if user["required_docs"] is None:

        return DEFAULT_REQUIRED_DOCUMENTS

    return tuple(

        document

        for document in user["required_docs"].split("|")

        if document in DOCUMENT_TYPES

    )


def missing_documents(user):

    valid_documents = set()

    documents = rows("""

        SELECT category, filename, expiry_date

        FROM documents

        WHERE user_id=?

        AND status IN ('Pending','Verified')

    """, (user["id"],))

    for document in documents:

        expiry = document["expiry_date"]

        filename = Path(document["filename"]).name

        if (

            expiry

            and valid_date(expiry)

            and expiry >= today()

            and (UPLOADS / filename).is_file()

        ):

            valid_documents.add(document["category"])

    return [

        category

        for category in required_documents(user)

        if category not in valid_documents

    ]


def has_profile_photo(uid):

    return bool(

        row("""

            SELECT user_id

            FROM profile_photos

            WHERE user_id=?

        """, (uid,))

    )


# =========================================================
# MANDATORY FIRST LOGIN ONBOARDING
# =========================================================

@app.before_request
def mandatory_onboarding():

    if not g.user:

        return

    # HR is exempt from employee onboarding.

    if admin():

        return

    # Temporary passwords must be changed first.

    if g.user["must_change_password"]:

        return

    if g.user["onboarded_at"]:

        return

    allowed_endpoints = {

        "first_onboarding",

        "profile_picture",

        "password_settings",

        "logout",

        "static"

    }

    if request.endpoint in allowed_endpoints:

        return

    return redirect(

        url_for("first_onboarding")

    )


# =========================================================
# PROFILE PICTURE UPLOAD
# =========================================================

def save_profile_picture(uid):

    uploaded = request.files.get("photo")

    if not uploaded or not uploaded.filename:

        abort(400)

    raw = uploaded.read(
        5 * 1024 * 1024 + 1
    )

    if not raw:

        abort(400)

    if len(raw) > 5 * 1024 * 1024:

        abort(413)

    try:

        with Image.open(io.BytesIO(raw)) as image:

            if image.format not in ("JPEG", "PNG"):

                abort(400)

            if image.width * image.height > 20_000_000:

                abort(400)

            image.load()

            image = ImageOps.exif_transpose(
                image
            ).convert("RGB")

            image.thumbnail((600, 600))

            output = io.BytesIO()

            image.save(

                output,

                format="JPEG",

                quality=85

            )

    except (

        OSError,

        ValueError,

        Image.DecompressionBombError

    ):

        abort(400)

    save("""

        INSERT INTO profile_photos

        (user_id,jpeg)

        VALUES (?,?)

        ON CONFLICT(user_id)

        DO UPDATE SET jpeg=excluded.jpeg

    """, (

        uid,

        output.getvalue()

    ))

    audit("Updated profile photograph")


# =========================================================
# PRIVATE PROFILE PHOTOGRAPH
# =========================================================

@app.get("/profile-picture/<int:uid>")
@login_required
def profile_picture(uid):

    # HR can view employee profile pictures.
    # Employees can view only their own picture.

    if not admin() and g.user["id"] != uid:

        abort(404)

    photograph = row("""

        SELECT jpeg

        FROM profile_photos

        WHERE user_id=?

    """, (uid,))

    if not photograph:

        abort(404)

    return Response(

        photograph["jpeg"],

        mimetype="image/jpeg",

        headers={

            "Cache-Control": "private, no-store",

            "X-Content-Type-Options": "nosniff"

        }

    )


# =========================================================
# ONBOARDING DOCUMENT UPLOAD
# =========================================================

def save_onboarding_document(uid, category, expiry):

    uploaded = request.files.get("document")

    if not uploaded or not uploaded.filename:

        abort(400)

    if not valid_date(expiry) or expiry < today():

        flash(
            "A valid, unexpired expiry date is required.",
            "error"
        )

        return False

    original = secure_filename(uploaded.filename)

    extension = Path(original).suffix.lower()

    if extension not in (

        ".pdf",

        ".jpg",

        ".jpeg",

        ".png"

    ):

        abort(400)

    raw = uploaded.read(
        8 * 1024 * 1024 + 1
    )

    if not raw:

        abort(400)

    if len(raw) > 8 * 1024 * 1024:

        abort(413)

    if extension == ".pdf":

        if not raw.startswith(b"%PDF-"):

            abort(400)

    else:

        try:

            with Image.open(io.BytesIO(raw)) as image:

                if image.format not in ("JPEG", "PNG"):

                    abort(400)

                if image.width * image.height > 20_000_000:

                    abort(400)

                image.verify()

        except (

            OSError,

            ValueError,

            Image.DecompressionBombError

        ):

            abort(400)

    filename = uuid.uuid4().hex + extension

    destination = UPLOADS / filename

    try:

        with destination.open("xb") as document:

            document.write(raw)

            document.flush()

            os.fsync(document.fileno())

        insert("documents", {

            "user_id": uid,

            "title": category,

            "category": category,

            "filename": filename,

            "original_name": original,

            "expiry_date": expiry,

            "status": "Pending",

            "uploaded_at": stamp()

        })

    except Exception:

        db().rollback()

        destination.unlink(missing_ok=True)

        raise

    audit(
        "Submitted onboarding document: " + category
    )

    return True


# =========================================================
# FIRST LOGIN ONBOARDING PAGE
# =========================================================

@app.route(
    "/first-onboarding",
    methods=["GET", "POST"]
)
@login_required
def first_onboarding():

    if admin():

        return redirect(url_for("profile"))

    uid = g.user["id"]

    required = required_documents(g.user)

    if request.method == "POST":

        action = field("action")

        # UPLOAD PERSONAL PROFILE PHOTO

        if action == "photo":

            save_profile_picture(uid)

            flash("Profile photograph saved.")

        # UPLOAD REQUIRED DOCUMENT

        elif action == "document":

            category = field("category")

            expiry = field("expiry")

            if category not in required:

                abort(400)

            if save_onboarding_document(
                uid,
                category,
                expiry
            ):

                flash(
                    category + " submitted for HR verification."
                )

        # COMPLETE ONBOARDING

        elif action == "finish":

            missing = missing_documents(g.user)

            if not has_profile_photo(uid):

                flash(

                    "Upload your profile photograph first.",

                    "error"

                )

            elif missing:

                flash(

                    "Required documents are missing: "
                    + ", ".join(missing),

                    "error"

                )

            else:

                save("""

                    UPDATE users

                    SET onboarded_at=?

                    WHERE id=?

                """, (

                    stamp(),

                    uid

                ))

                audit("Completed employee onboarding")

                flash("Welcome to your employee workspace.")

                return redirect(url_for("profile"))

        else:

            abort(400)

        return redirect(
            url_for("first_onboarding")
        )

    missing = missing_documents(g.user)

    photo_exists = has_profile_photo(uid)

    total = len(required) + 1

    completed = (

        len(required)

        - len(missing)

        + int(photo_exists)

    )

    progress = round(
        completed / total * 100
    )

    return page("Employee Onboarding", """

    <div class="page-heading">

        <div>

            <h1>Welcome to STAR LINK</h1>

            <p class="muted">

                Complete your employee profile and
                mandatory documents before accessing
                your workspace.

            </p>

        </div>

    </div>

    <div class="card">

        <h2>Onboarding Progress</h2>

        <strong>{{ completed }} of {{ total }} completed</strong>

        <div class="workflow-progress-track">

            <div
                class="workflow-progress-fill"
                style="width:{{ progress }}%"
            ></div>

        </div>

        <p>{{ progress }}% completed</p>

    </div>

    <div class="workflow-document">

        <h2>1. Profile Photograph</h2>

        {% if photo_exists %}

            <img
                src="{{ url_for('profile_picture',uid=g.user.id) }}"
                style="width:100px;height:100px;object-fit:cover;border-radius:10px"
                alt="Your profile photograph"
            >

            <p class="green">Photograph submitted</p>

        {% endif %}

        <form method="post" enctype="multipart/form-data">

            {{ token() }}

            <input
                type="hidden"
                name="action"
                value="photo"
            >

            <label>Upload your profile photograph</label>

            <input
                type="file"
                name="photo"
                accept="image/jpeg,image/png"
                required
            >

            <p class="muted">

                JPG or PNG, maximum 5 MB.

            </p>

            <button class="btn btn-purple">

                Save Photograph

            </button>

        </form>

    </div>

    {% for category in required %}

        <div class="workflow-document">

            <div class="workflow-request-header">

                <h2>{{ loop.index + 1 }}. {{ category }}</h2>

                {% if category in missing %}

                    {{ badge('Pending') }}

                {% else %}

                    {{ badge('Submitted') }}

                {% endif %}

            </div>

            <form
                method="post"
                enctype="multipart/form-data"
            >

                {{ token() }}

                <input
                    type="hidden"
                    name="action"
                    value="document"
                >

                <input
                    type="hidden"
                    name="category"
                    value="{{ category }}"
                >

                <div class="form-grid">

                    <div>

                        <label>Upload Document *</label>

                        <input
                            type="file"
                            name="document"
                            accept=".pdf,.jpg,.jpeg,.png"
                            required
                        >

                        <p class="muted">

                            PDF, JPG or PNG. Maximum 8 MB.

                        </p>

                    </div>

                    <div>

                        <label>Expiry Date *</label>

                        <input
                            type="date"
                            name="expiry"
                            min="{{ today }}"
                            required
                        >

                    </div>

                </div>

                <br>

                <button class="btn">

                    Submit Document

                </button>

            </form>

        </div>

    {% endfor %}

    <div class="card">

        <div class="workflow-note">

            Your documents are submitted privately
            for HR verification.

        </div>

        <form method="post">

            {{ token() }}

            <input
                type="hidden"
                name="action"
                value="finish"
            >

            <button
                class="btn btn-purple btn-block"
                {% if missing or not photo_exists %}
                    disabled
                    style="opacity:.5"
                {% endif %}
            >

                Complete Onboarding and Open Profile

            </button>

        </form>

    </div>

    """,

        required=required,

        missing=missing,

        photo_exists=photo_exists,

        completed=completed,

        total=total,

        progress=progress

    )


# =========================================================
# REPORTING HIERARCHY HELPERS
# =========================================================

def approval_chain(uid, module):

    route = row("""

        SELECT lead_id, manager_id

        FROM approval_routes

        WHERE user_id=?

        AND module=?

    """, (

        uid,

        module

    ))

    if not route:

        return []

    return [

        reviewer

        for reviewer in (

            route["lead_id"],

            route["manager_id"]

        )

        if reviewer

    ]


def next_reviewer(uid, module, record_id):

    approved_reviewers = {

        item["reviewer_id"]

        for item in rows("""

            SELECT reviewer_id

            FROM delegated_approvals

            WHERE module=?

            AND record_id=?

        """, (

            module,

            record_id

        ))

    }

    for reviewer in approval_chain(uid, module):

        if reviewer not in approved_reviewers:

            return reviewer

    return None


def is_reviewer():

    if not g.user:

        return False

    if admin():

        return True

    return bool(row("""

        SELECT user_id

        FROM approval_routes

        WHERE lead_id=?

        OR manager_id=?

        LIMIT 1

    """, (

        g.user["id"],

        g.user["id"]

    )))


def approval_stage(uid, module, record_id):

    reviewer_id = next_reviewer(

        uid,

        module,

        record_id

    )

    if not reviewer_id:

        return "Awaiting HR approval"

    reviewer = row("""

        SELECT name,status

        FROM users

        WHERE id=?

    """, (reviewer_id,))

    if not reviewer or reviewer["status"] != "Approved":

        return "Waiting for HR to reassign reviewer"

    return "Awaiting " + reviewer["name"]


def routed_record(module, record_id):

    if module not in WORKFLOW_MODULES:

        abort(404)

    table = MODULES[module]["table"]

    sql = f"SELECT * FROM {table} WHERE id=?"

    arguments = [record_id]

    if table == "employee_records":

        sql += " AND module=?"

        arguments.append(module)

    record = row(sql, arguments)

    if not record:

        abort(404)

    return record


# =========================================================
# MY PERSONAL EMPLOYEE PROFILE
# =========================================================

@login_required
def employee_profile_page():

    uid = g.user["id"]

    if request.method == "POST":

        action = field("action")

        if action == "photo":

            save_profile_picture(uid)

            flash("Profile photograph updated.")

        elif action == "phone":

            save("""

                UPDATE users

                SET phone=?

                WHERE id=?

            """, (

                field("phone", 30),

                uid

            ))

            audit("Updated personal profile")

            flash("Profile information updated.")

        else:

            abort(400)

        return redirect(url_for("profile"))

    services = []

    for module in CORE_EMPLOYEE_MODULES:

        if allowed(module):

            services.append({

                "key": module,

                "title": MODULES[module]["title"],

                "icon": MODULES[module]["icon"],

                "color": MODULES[module]["color"]

            })

    reporting = rows("""

        SELECT DISTINCT u.name,u.designation

        FROM users u

        JOIN approval_routes a

        ON u.id=a.lead_id

        OR u.id=a.manager_id

        WHERE a.user_id=?

        ORDER BY u.name

    """, (uid,))

    return page("My Profile", """

    <div class="page-heading">

        <div>

            <h1>My Profile</h1>

            <p class="muted">

                Welcome to your personal STAR LINK workspace.

            </p>

        </div>

    </div>

    <div class="employee-profile-banner">

        {% if photo_exists %}

            <img
                class="employee-profile-picture"
                src="{{ url_for('profile_picture',uid=g.user.id) }}"
                alt="Profile photograph"
            >

        {% else %}

            <div class="employee-profile-placeholder">

                {{ g.user.name[:1] }}

            </div>

        {% endif %}

        <div>

            <h2>{{ g.user.name }}</h2>

            <p>{{ g.user.designation or 'Employee' }}</p>

            <p>{{ g.user.department or 'Not assigned' }}</p>

            <p>Employee ID: {{ g.user.employee_code or '—' }}</p>

            {{ badge(g.user.status) }}

        </div>

    </div>

    <div class="card">

        <h2>Employment Information</h2>

        <div class="employee-details-grid">

            {% for label,key in [

                ('Full Name','name'),

                ('Company Email','email'),

                ('Employee ID','employee_code'),

                ('Designation','designation'),

                ('Department','department'),

                ('Assigned Project','project'),

                ('Joining Date','joining_date'),

                ('Access Role','role'),

                ('Phone Number','phone')

            ] %}

                <div class="employee-detail">

                    <span>{{ label }}</span>

                    <strong>{{ g.user[key] or 'Not assigned' }}</strong>

                </div>

            {% endfor %}

        </div>

        <br>

        <h2>Reporting Managers / Team Leads</h2>

        {% for reviewer in reporting %}

            <p>

                <strong>{{ reviewer.name }}</strong>

                — {{ reviewer.designation or 'Assigned reviewer' }}

            </p>

        {% else %}

            <p class="muted">

                No delegated reviewer assigned.
                Requests are handled by HR.

            </p>

        {% endfor %}

    </div>

    {% if missing %}

        <div class="card">

            <h2>Documents Requiring Attention</h2>

            <p>

                {{ missing|join(', ') }}

            </p>

            <a
                class="btn btn-purple"
                href="{{ url_for('first_onboarding') }}"
            >

                Update Required Documents

            </a>

        </div>

    {% endif %}

    <div class="card">

        <h2>My Workspace</h2>

        <p class="muted">

            Manage your daily work, attendance,
            requests and employee services.

        </p>

        <div class="employee-services-grid">

            {% for service in services %}

                <a
                    class="employee-service"
                    href="{{ url_for('module_page',module=service.key) }}"
                >

                    <div class="{{ service.color }}">

                        <svg viewBox="0 0 100 100">

                            {{ icons[service.icon]|safe }}

                        </svg>

                    </div>

                    <strong>{{ service.title }}</strong>

                    <small>Open Service</small>

                </a>

            {% endfor %}

            <a
                class="employee-service"
                href="{{ url_for('my_workflow_requests') }}"
            >

                <div class="purple">

                    <svg viewBox="0 0 100 100">

                        {{ icons['check']|safe }}

                    </svg>

                </div>

                <strong>My Requests</strong>

                <small>Track approvals</small>

            </a>

        </div>

    </div>

    {% if reviewer %}

        <div class="card">

            <h2>Approval Workspace</h2>

            <p>

                Review employee requests assigned to you.

            </p>

            <a
                class="btn btn-purple"
                href="{{ url_for('team_approvals') }}"
            >

                Open Approval Inbox

            </a>

        </div>

    {% endif %}

    {% if admin() %}

        <div class="card">

            <h2>HR Administration</h2>

            <div class="inline">

                <a
                    class="btn btn-purple"
                    href="{{ url_for('dashboard') }}"
                >

                    HR Dashboard

                </a>

                <a
                    class="btn"
                    href="{{ url_for('workflow_settings') }}"
                >

                    Workflow Configuration

                </a>

                <a
                    class="btn btn-light"
                    href="{{ url_for('workforce') }}"
                >

                    Manage Employees

                </a>

                <a
                    class="btn btn-light"
                    href="{{ url_for('approvals') }}"
                >

                    HR Approvals

                </a>

            </div>

        </div>

    {% endif %}

    <div class="two-columns">

        <div class="card">

            <h2>Update Contact Details</h2>

            <form method="post">

                {{ token() }}

                <input
                    type="hidden"
                    name="action"
                    value="phone"
                >

                <label>Phone Number</label>

                <input
                    name="phone"
                    value="{{ g.user.phone or '' }}"
                    maxlength="30"
                >

                <br><br>

                <button class="btn">

                    Save Contact Details

                </button>

            </form>

        </div>

        <div class="card">

            <h2>Change Profile Photograph</h2>

            <form
                method="post"
                enctype="multipart/form-data"
            >

                {{ token() }}

                <input
                    type="hidden"
                    name="action"
                    value="photo"
                >

                <input
                    type="file"
                    name="photo"
                    accept="image/jpeg,image/png"
                    required
                >

                <br><br>

                <button class="btn">

                    Update Photograph

                </button>

            </form>

        </div>

    </div>

    """,

        photo_exists=has_profile_photo(uid),

        reporting=reporting,

        services=services,

        reviewer=is_reviewer(),

        missing=[] if admin() else missing_documents(g.user)

    )


app.view_functions["profile"] = employee_profile_page


# =========================================================
# EMPLOYEE REQUEST TRACKING PAGE
# =========================================================

@app.get("/my-requests")
@login_required
def my_workflow_requests():

    uid = g.user["id"]

    results = []

    for module in WORKFLOW_MODULES:

        if not allowed(module):

            continue

        config = MODULES[module]

        table = config["table"]

        sql = f"""

            SELECT *

            FROM {table}

            WHERE user_id=?

        """

        arguments = [uid]

        if table == "employee_records":

            sql += " AND module=?"

            arguments.append(module)

        sql += " ORDER BY id DESC"

        for item in rows(sql, arguments):

            results.append({

                "module": module,

                "id": item["id"],

                "type": config["title"],

                "status": item["status"],

                "created": item["created_at"],

                "stage": (

                    approval_stage(uid,module,item["id"])

                    if item["status"] in ACTIVE_REQUEST_STATES

                    else "Decision recorded"

                )

            })

    results.sort(

        key=lambda item: item["created"] or "",

        reverse=True

    )

    return page("My Requests", """

    <h1>My Requests</h1>

    <p class="muted">

        Track your submitted requests and approval progress.

    </p>

    <div class="card">

        <div class="toolbar">

            <h2>Request History</h2>

            {{ search() }}

        </div>

        <div class="table-wrap">

            <table class="workflow-table">

                <thead>

                    <tr>

                        <th>Reference</th>

                        <th>Request Type</th>

                        <th>Submitted</th>

                        <th>Status</th>

                        <th>Approval Stage</th>

                        <th>Details</th>

                    </tr>

                </thead>

                <tbody>

                    {% for item in results %}

                        <tr>

                            <td>#{{ item.id }}</td>

                            <td>{{ item.type }}</td>

                            <td>{{ item.created }}</td>

                            <td>{{ badge(item.status) }}</td>

                            <td>{{ item.stage }}</td>

                            <td>

                                <a
                                    class="btn btn-small btn-light"
                                    href="{{ url_for('module_page',module=item.module) }}"
                                >

                                    View Records

                                </a>

                            </td>

                        </tr>

                    {% else %}

                        <tr>

                            <td colspan="6">

                                You have not submitted any requests.

                            </td>

                        </tr>

                    {% endfor %}

                </tbody>

            </table>

        </div>

    </div>

    """, results=results)


# =========================================================
# HR EMPLOYEE AND APPROVAL CONFIGURATION
# =========================================================

@app.route(
    "/workflow-settings",
    methods=["GET", "POST"]
)
@admin_required
def workflow_settings():

    uid = request.values.get(
        "uid",
        type=int
    )

    employees = rows("""

        SELECT id,name,employee_code

        FROM users

        WHERE role='Employee'

        ORDER BY name

    """)

    employee = None

    if uid:

        employee = row("""

            SELECT *

            FROM users

            WHERE id=?

            AND role='Employee'

        """, (uid,))

        if not employee:

            abort(404)

    candidates = rows("""

        SELECT id,name,designation,department

        FROM users

        WHERE role='Employee'

        AND status='Approved'

        AND id!=?

        ORDER BY name

    """, (uid or 0,))

    if request.method == "POST":

        if not employee:

            abort(400)

        required = [

            category

            for category in DOCUMENT_TYPES

            if category in request.form.getlist(
                "required_documents"
            )

        ]

        valid_ids = {

            candidate["id"]

            for candidate in candidates

        }

        allow_optional = (
            request.form.get("sl_allow_optional") == "1"
        )

        configurations = []

        for module in WORKFLOW_MODULES:

            lead_id = request.form.get(

                module + "_lead",

                type=int

            ) or None

            manager_id = request.form.get(

                module + "_manager",

                type=int

            ) or None

            if lead_id and lead_id not in valid_ids:

                abort(400)

            if manager_id and manager_id not in valid_ids:

                abort(400)

            if lead_id and lead_id == manager_id:

                abort(400)

            if not allow_optional:

                if not lead_id or not manager_id:

                    flash(
                        "Both Team Lead and Reporting Manager "
                        "are required for "
                        + MODULES[module]["title"],
                        "error"
                    )

                    return redirect(
                        url_for(
                            "workflow_settings",
                            uid=uid
                        )
                    )

            configurations.append((

                module,

                lead_id,

                manager_id

            ))

        connection = db()

        connection.execute("BEGIN IMMEDIATE")

        try:

            requirements_changed = (

                set(required)

                != set(required_documents(employee))

            )

            connection.execute("""

                UPDATE users

                SET project=?,

                    required_docs=?,

                    onboarded_at=CASE

                        WHEN ? THEN NULL

                        ELSE onboarded_at

                    END

                WHERE id=?

            """, (

                field("project", 200),

                "|".join(required),

                int(requirements_changed),

                uid

            ))

            for module, lead_id, manager_id in configurations:

                new_chain = [

                    reviewer

                    for reviewer in (lead_id,manager_id)

                    if reviewer

                ]

                old_chain = approval_chain(uid,module)

                old_hr_final = workflow_hr_final(uid, module)

                new_hr_final = (
                    request.form.get(module + "_hr_final") == "1"
                )

                # If HR changes a hierarchy, outstanding
                # requests must follow the new hierarchy.

                if (
                    old_chain != new_chain
                    or old_hr_final != new_hr_final
                ):

                    table = MODULES[module]["table"]

                    sql = f"""

                        SELECT id

                        FROM {table}

                        WHERE user_id=?

                        AND status IN (

                            'Pending',

                            'Open',

                            'In Progress'

                        )

                    """

                    arguments = [uid]

                    if table == "employee_records":

                        sql += " AND module=?"

                        arguments.append(module)

                    connection.execute(

                        "DELETE FROM delegated_approvals "

                        "WHERE module=? "

                        "AND record_id IN (" + sql + ")",

                        [module] + arguments

                    )

                connection.execute("""

                    INSERT INTO approval_routes

                    (

                        user_id,

                        module,

                        lead_id,

                        manager_id

                    )

                    VALUES (?,?,?,?)

                    ON CONFLICT(user_id,module)

                    DO UPDATE SET

                        lead_id=excluded.lead_id,

                        manager_id=excluded.manager_id

                """, (

                    uid,

                    module,

                    lead_id,

                    manager_id

                ))

            for workflow_module in WORKFLOW_MODULES:

                require_hr = int(
                    request.form.get(
                        workflow_module + "_hr_final"
                    ) == "1"
                )

                connection.execute("""

                    UPDATE approval_routes

                    SET hr_final=?

                    WHERE user_id=?
                    AND module=?

                """, (

                    require_hr,

                    uid,

                    workflow_module

                ))

            connection.execute("""

                INSERT INTO sl_approval_policy

                (user_id, allow_optional)

                VALUES (?,?)

                ON CONFLICT(user_id)

                DO UPDATE SET
                    allow_optional=excluded.allow_optional

            """, (

                uid,

                int(allow_optional)

            ))

            connection.commit()

        except Exception:

            connection.rollback()

            raise

        audit(

            f"Updated workflow configuration "
            f"for employee #{uid}"

        )

        flash("Employee configuration saved.")

        return redirect(

            url_for("workflow_settings",uid=uid)

        )

    routes = {}

    if employee:

        routes = {

            item["module"]: dict(item)

            for item in rows("""

                SELECT *

                FROM approval_routes

                WHERE user_id=?

            """, (uid,))

        }

    return page("Workflow Configuration", """

    <h1>Employee Workflow Configuration</h1>

    <p class="muted">

        Configure employee projects, mandatory documents
        and individual approval hierarchies.

    </p>

    <div class="card">

        <h2>Select Employee</h2>

        <form method="get">

            <select
                name="uid"
                onchange="this.form.submit()"
            >

                <option value="">

                    Select an Employee

                </option>

                {% for e in employees %}

                    <option
                        value="{{ e.id }}"
                        {{ 'selected' if uid==e.id else '' }}
                    >

                        {{ e.name }}

                        — {{ e.employee_code }}

                    </option>

                {% endfor %}

            </select>

        </form>

    </div>

    {% if employee %}

        <div class="card">

            <div class="workflow-request-header">

                <div>

                    <h2>{{ employee.name }}</h2>

                    <p>{{ employee.designation }}</p>

                    <p class="muted">

                        {{ employee.department }}

                    </p>

                </div>

                {% if employee.onboarded_at %}

                    {{ badge('Completed') }}

                {% else %}

                    {{ badge('Pending') }}

                {% endif %}

            </div>

            <div class="inline">

                <a
                    class="btn btn-light"
                    href="{{ url_for('employee_edit',user_id=uid) }}"
                >

                    Edit Employee Details

                </a>

                <a
                    class="btn btn-light"
                    href="{{ url_for('permissions',employee=uid) }}"
                >

                    Configure Permissions

                </a>

            </div>

        </div>

        <form method="post">

            {{ token() }}

            <input
                type="hidden"
                name="uid"
                value="{{ uid }}"
            >

            <div class="card">

                <h2>Employment Configuration</h2>

                <label>Assigned Project</label>

                <input
                    name="project"
                    value="{{ employee.project or '' }}"
                    placeholder="Enter project or site name"
                    maxlength="200"
                >

            </div>

            <div class="card">

                <h2>Mandatory Employee Documents</h2>

                <p class="muted">

                    Select the documents this employee
                    must provide with expiry dates.

                </p>

                {% for document in document_types %}

                    <label class="workflow-checkbox">

                        <input
                            type="checkbox"
                            name="required_documents"
                            value="{{ document }}"
                            {{ 'checked' if document in required else '' }}
                        >

                        {{ document }}

                    </label>

                {% endfor %}

            </div>

            <div class="card">

                

<div class="sl-approval-policy">

    <h2>Employee Approval Policy</h2>

    <label class="sl-policy-checkbox">

        <input

            type="checkbox"

            name="sl_allow_optional"

            id="sl-allow-optional"

            value="1"

            {% if sl_optional_stages(uid) %}

                checked

            {% endif %}

        >

        Regular employee — allow optional approval stages

    </label>

    <p class="muted">

        Enable this only for regular employees who are
        allowed to skip the Team Lead or Reporting
        Manager stage.

        Otherwise both stages are mandatory.

    </p>

</div>

<h2>Approval Hierarchy</h2>



                <div class="workflow-note">

                    Employee → Assigned Reviewer → Optional HR Approval.

                    Only employees with an HR-authorized exception may skip an approval stage.
                    If both fields are empty, requests go
                    directly to HR.

                </div>

                {% for module,title in workflow_modules %}

                    <div class="workflow-document">

                        <h2>{{ title }}</h2>

                        <div class="form-grid">

                            {% for role in ['lead','manager'] %}

                                <div>

                                    <label>

                                        {{ 'Team Lead'
                                           if role=='lead'
                                           else 'Reporting Manager' }}

                                    </label>

                                    <select
                                        name="{{ module }}_{{ role }}"

        data-sl-approval-stage="1"

        {% if not sl_optional_stages(uid) %}

            required

        {% endif %}
                                    >

                                        <option value="">

                                            {% if sl_optional_stages(uid) %}

            Skip This Approval Stage

        {% elif role == 'lead' %}

            Select Team Lead

        {% else %}

            Select Reporting Manager

        {% endif %}

                                        </option>

                                        {% for reviewer in candidates %}

                                            <option

                                                value="{{ reviewer.id }}"

                                                {{ 'selected'
                                                if routes.get(module,{}).get(role+'_id')==reviewer.id
                                                else '' }}

                                            >

                                                {{ reviewer.name }}

                                                — {{ reviewer.designation }}

                                                — {{ reviewer.department }}

                                            </option>

                                        {% endfor %}

                                    </select>

                                </div>

                            {% endfor %}

                        </div>

                    </div>

                {% endfor %}

                

    <div class="card">

        <h2>Final Approval Requirements</h2>

        <p class="muted">

            Decide whether each request requires HR approval
            after the assigned team lead or line manager
            has approved it.

        </p>

        <div class="table-wrap">

            <table class="workflow-table">

                <thead>

                    <tr>

                        <th>Request Type</th>

                        <th>Require Final HR Approval</th>

                        <th>Approval Behaviour</th>

                    </tr>

                </thead>

                <tbody>

                    {% for module,title in workflow_modules %}

                        <tr>

                            <td>

                                <strong>{{ title }}</strong>

                            </td>

                            <td>

                                <label style="
                                    display:flex;
                                    align-items:center;
                                    gap:10px;
                                    margin:0;
                                ">

                                    <input

                                        type="checkbox"

                                        name="{{ module }}_hr_final"

                                        value="1"

                                        style="
                                            width:18px;
                                            height:18px;
                                        "

                                        {% if routes.get(module,{}).get('hr_final',0) %}

                                            checked

                                        {% endif %}

                                    >

                                    Require HR

                                </label>

                            </td>

                            <td>

                                <small class="muted">

                                    Unchecked: The last assigned
                                    manager or team lead gives
                                    final approval.

                                </small>

                            </td>

                        </tr>

                    {% endfor %}

                </tbody>

            </table>

        </div>

        <div class="workflow-note">

            If no manager or team lead is assigned,
            the request goes directly to HR.

            HR retains administrator override access
            for every request.

        </div>

    </div>

    
<button class="btn btn-purple btn-block">

                    Save Employee Configuration

                </button>

            </div>

        </form>

    {% else %}

        <div class="card empty">

            Select an employee to configure their workspace
            and approval hierarchy.

        </div>

    {% endif %}

    """,

        uid=uid,

        employees=employees,

        employee=employee,

        candidates=candidates,

        routes=routes,

        document_types=DOCUMENT_TYPES,

        required=(

            required_documents(employee)

            if employee

            else []

        ),

        workflow_modules=[

            (module,MODULES[module]["title"])

            for module in WORKFLOW_MODULES

        ]

    )


# =========================================================
# MANAGER / TEAM LEAD / HR APPROVAL INBOX
# =========================================================

@app.get("/team-approvals")
@login_required
def team_approvals():

    if not is_reviewer():

        abort(403)

    inbox = []

    for module in WORKFLOW_MODULES:

        config = MODULES[module]

        table = config["table"]

        sql = f"""

            SELECT r.*,u.name AS employee_name

            FROM {table} r

            JOIN users u ON u.id=r.user_id

            WHERE r.status IN (

                'Pending',

                'Open',

                'In Progress'

            )

        """

        arguments = []

        if table == "employee_records":

            sql += " AND r.module=?"

            arguments.append(module)

        if not admin():

            sql += """

                AND EXISTS (

                    SELECT 1

                    FROM approval_routes a

                    WHERE a.user_id=r.user_id

                    AND a.module=?

                    AND (

                        a.lead_id=?

                        OR a.manager_id=?

                    )

                )

            """

            arguments.extend([

                module,

                g.user["id"],

                g.user["id"]

            ])

        for item in rows(sql,arguments):

            reviewer_id = next_reviewer(

                item["user_id"],

                module,

                item["id"]

            )

            # Managers and team leads see only requests
            # currently assigned to them.

            if not admin():

                if reviewer_id != g.user["id"]:

                    continue

            inbox.append({

                "module": module,

                "id": item["id"],

                "employee": item["employee_name"],

                "title": config["title"],

                "status": item["status"],

                "stage": approval_stage(

                    item["user_id"],

                    module,

                    item["id"]

                ),

                "ready_for_hr": reviewer_id is None,

                "details": [

                    (

                        f["label"],

                        item[f["name"]]

                    )

                    for f in config["fields"]

                ]

            })

    return page("Approval Inbox", """

    <div class="page-heading">

        <div>

            <h1>Approval Inbox</h1>

            <p class="muted">

                Review and approve employee requests.

            </p>

        </div>

        {% if admin() %}

            <a
                class="btn btn-purple"
                href="{{ url_for('workflow_settings') }}"
            >

                Configure Approval Hierarchy

            </a>

        {% endif %}

    </div>

    <div class="stat-grid">

        <div class="stat">

            Pending Requests

            <div class="stat-number">

                {{ inbox|length }}

            </div>

        </div>

        <div class="stat">

            Approval Workspace

            <div style="font-size:19px;margin-top:12px">

                {{ 'HR Administration'
                   if admin()
                   else 'Assigned Reviewer' }}

            </div>

        </div>

    </div>

    {% for item in inbox %}

        <div class="workflow-request">

            <div class="workflow-request-header">

                <div>

                    <h2>{{ item.title }}</h2>

                    <strong>{{ item.employee }}</strong>

                    <p class="muted">

                        Request #{{ item.id }}

                    </p>

                </div>

                <span class="workflow-waiting">

                    {{ item.stage }}

                </span>

            </div>

            <div class="employee-details-grid">

                {% for label,value in item.details %}

                    <div class="employee-detail">

                        <span>{{ label }}</span>

                        <strong>{{ value or '—' }}</strong>

                    </div>

                {% endfor %}

            </div>

            <br>

            {% if admin() %}

                {% if item.ready_for_hr %}

                    <a
                        class="btn btn-purple"
                        href="{{ url_for(
                            'review_record',
                            module=item.module,
                            record_id=item.id
                        ) }}"
                    >

                        HR Final Review

                    </a>

                {% else %}

                    <p class="muted">

                        The assigned reviewer must
                        complete their approval first.

                    </p>

                {% endif %}

            {% else %}

                <form
                    method="post"
                    action="{{ url_for(
                        'delegated_decision',
                        module=item.module,
                        record_id=item.id
                    ) }}"
                >

                    {{ token() }}

                    <label>Approval Remarks</label>

                    <textarea
                        name="remarks"
                        maxlength="1000"
                        placeholder="Enter comments or reason for rejection"
                    ></textarea>

                    <br><br>

                    <div class="inline">

                        <button
                            class="btn"
                            name="decision"
                            value="Approved"
                        >

                            Approve and Forward

                        </button>

                        <button
                            class="btn btn-red"
                            name="decision"
                            value="Rejected"
                        >

                            Reject Request

                        </button>

                    </div>

                </form>

            {% endif %}

        </div>

    {% else %}

        <div class="card empty">

            <h2>No Pending Requests</h2>

            There are currently no requests assigned to you.

        </div>

    {% endfor %}

    {% if admin() %}

        <div class="card">

            <h2>Other HR Approvals</h2>

            <p>

                Review employee registrations, documents
                and other company records.

            </p>

            <a
                class="btn"
                href="{{ url_for('approvals') }}"
            >

                Open Central HR Approvals

            </a>

        </div>

    {% endif %}

    """, inbox=inbox)


# =========================================================
# MANAGER AND TEAM LEAD APPROVAL DECISIONS
# =========================================================

@app.post(
    "/team-approvals/<module>/<int:record_id>"
)
@login_required
def delegated_decision(module, record_id):

    # HR final approval is handled by the original HR route.

    if admin():

        abort(403)

    result = field("decision")

    remarks = field("remarks",1000)

    final_approval = False

    if result not in ("Approved","Rejected"):

        abort(400)

    if result == "Rejected" and not remarks:

        flash(

            "Please enter the reason for rejection.",

            "error"

        )

        return redirect(url_for("team_approvals"))

    connection = db()

    connection.execute("BEGIN IMMEDIATE")

    try:

        record = routed_record(module,record_id)

        if record["status"] not in ACTIVE_REQUEST_STATES:

            abort(409)

        reviewer_id = next_reviewer(

            record["user_id"],

            module,

            record_id

        )

        if reviewer_id != g.user["id"]:

            abort(403)

        if result == "Approved":

            connection.execute("""

                INSERT INTO delegated_approvals

                (

                    module,

                    record_id,

                    reviewer_id,

                    decided_at

                )

                VALUES (?,?,?,?)

            """, (

                module,

                record_id,

                g.user["id"],

                stamp()

            ))

            remaining_reviewer = next_reviewer(

                record["user_id"],

                module,

                record_id

            )

            require_hr = workflow_hr_final(

                record["user_id"],

                module

            )

            # The last assigned reviewer becomes
            # the final approver when HR is not required.

            if remaining_reviewer is None and not require_hr:

                table = MODULES[module]["table"]

                sql = (

                    f"UPDATE {table} "

                    "SET status=? "

                    "WHERE id=?"

                )

                parameters = [

                    "Approved",

                    record_id

                ]

                if table == "employee_records":

                    sql += " AND module=?"

                    parameters.append(module)

                connection.execute(

                    sql,

                    parameters

                )

                final_approval = True

        else:

            table = MODULES[module]["table"]

            connection.execute(

                f"UPDATE {table} "

                "SET status='Rejected' "

                "WHERE id=?",

                (record_id,)

            )

        # STORE APPROVAL HISTORY

        connection.execute("""

            INSERT INTO approval_history

            (

                module,

                record_id,

                employee_id,

                decision,

                decided_by,

                remarks,

                created_at

            )

            VALUES (?,?,?,?,?,?,?)

        """, (

            module,

            record_id,

            record["user_id"],

            ("Approved" if final_approval else "Delegated " + result),

            g.user["id"],

            remarks,

            stamp()

        ))

        connection.commit()

    except Exception:

        connection.rollback()

        raise

    audit(

        f"{result} {module} request #{record_id} "

        "as assigned reviewer"

    )

    if result == "Approved":

        flash(

            (
                "Request fully approved. No further HR approval is required."
                if final_approval
                else "Request approved and forwarded to the next approval stage."
            )

        )

    else:

        flash("Request rejected.")

    return redirect(

        url_for("team_approvals")

    )


# =========================================================
# PROTECT FINAL HR APPROVAL
# =========================================================

original_review_record = app.view_functions[
    "review_record"
]


@admin_required
def guarded_hr_review(module,record_id):

    if module in WORKFLOW_MODULES:

        if request.method == "POST":

            db().execute("BEGIN IMMEDIATE")

        record = routed_record(

            module,

            record_id

        )

        reviewer_id = next_reviewer(

            record["user_id"],

            module,

            record_id

        )

        if reviewer_id is not None and (

            record["status"] in ACTIVE_REQUEST_STATES

            or request.method == "POST"

        ):

            db().rollback()

            flash(

                "The assigned team lead or manager "
                "must complete their approval first.",

                "error"

            )

            return redirect(

                url_for("team_approvals")

            )

    return original_review_record(

        module,

        record_id

    )


app.view_functions[
    "review_record"
] = guarded_hr_review


# =========================================================
# ADD WORKFLOW NAVIGATION TO THE EXISTING SIDEBAR
# =========================================================

original_visible_menu = visible_menu


def visible_menu():

    menu = original_visible_menu()

    if not g.user:

        return menu

    if g.user["must_change_password"]:

        return menu

    if not admin() and not g.user["onboarded_at"]:

        return []

    menu.append({

        "name": "Employee Workspace",

        "items": [

            {

                "label": "My Profile",

                "link": url_for("profile"),

                "icon": "user",

                "color": "purple"

            },

            {

                "label": "My Requests",

                "link": url_for("my_workflow_requests"),

                "icon": "check",

                "color": "green"

            }

        ]

    })

    if is_reviewer():

        approval_items = [

            {

                "label": "Approval Inbox",

                "link": url_for("team_approvals"),

                "icon": "check",

                "color": "green"

            }

        ]

        if admin():

            approval_items.append({

                "label": "Workflow Configuration",

                "link": url_for("workflow_settings"),

                "icon": "settings",

                "color": "purple"

            })

        menu.append({

            "name": "Approval Workflow",

            "items": approval_items

        })

    return menu


# =========================================================
# JINJA GLOBALS
# =========================================================

app.jinja_env.globals.update(

    allowed=allowed,

    workflow_modules=WORKFLOW_MODULES,

    active_request_states=ACTIVE_REQUEST_STATES,

    approval_stage=approval_stage

)





# STAR LINK REQUIRED PROFILE V1

# =========================================================
# DATABASE MIGRATION
# =========================================================

_previous_profile_init = init_db


def init_db():

    _previous_profile_init()

    connection = db()

    connection.execute("BEGIN IMMEDIATE")

    try:

        columns = {

            r[1]

            for r in connection.execute(
                "PRAGMA table_info(users)"
            )

        }

        if "profile_completed_at" not in columns:

            connection.execute(
                "ALTER TABLE users "
                "ADD COLUMN profile_completed_at TEXT"
            )

        connection.commit()

    except Exception:

        connection.rollback()

        raise


# =========================================================
# REQUIRED EMPLOYMENT INFORMATION
# =========================================================

PROFILE_FIELDS = (

    ("name", "Full Name", "text", 150),

    ("email", "Company Email", "email", 200),

    ("employee_code", "Employee ID", "text", 60),

    ("designation", "Designation", "text", 150),

    ("department", "Department", "text", 150),

    ("project", "Assigned Project (if any)", "text", 200),

    ("joining_date", "Joining Date", "date", 10),

    ("phone", "Phone Number", "tel", 30),

)


# =========================================================
# CHECK IF PROFILE INFORMATION IS COMPLETE
# =========================================================

# STAR LINK MANDATORY PROFILE PHOTO V1

def required_profile_photo_exists(uid):

    return bool(row("""

        SELECT user_id
        FROM profile_photos
        WHERE user_id=?
        AND length(jpeg)>0

    """, (uid,)))


def profile_required(user):

    # Initial profile setup must be completed.

    if not user["profile_completed_at"]:
        return True

    # Every mandatory field must contain information.

    for key, label, kind, limit in PROFILE_FIELDS:

        if key == "project":
            continue

        if not str(user[key] or "").strip():
            return True

    # Profile photograph is mandatory.

    if not required_profile_photo_exists(user["id"]):
        return True

    return False


# =========================================================
# VALIDATE AND PROCESS PROFILE PHOTOGRAPH
# =========================================================

def prepare_required_profile_photo(uploaded):

    from PIL import Image, ImageOps

    if not uploaded or not uploaded.filename:

        raise ValueError(
            "Please upload your profile photograph."
        )

    raw = uploaded.read(5 * 1024 * 1024 + 1)

    if not raw:

        raise ValueError(
            "The selected photograph is empty."
        )

    if len(raw) > 5 * 1024 * 1024:

        raise ValueError(
            "Profile photograph must be smaller than 5 MB."
        )

    try:

        with Image.open(io.BytesIO(raw)) as image:

            if image.format not in ("JPEG", "PNG"):

                raise ValueError(
                    "Only JPG and PNG photographs are allowed."
                )

            if (
                image.width < 32
                or image.height < 32
                or image.width * image.height > 20_000_000
            ):

                raise ValueError(
                    "Invalid photograph dimensions."
                )

            image.load()

            image = ImageOps.exif_transpose(
                image
            ).convert("RGB")

            image.thumbnail((600, 600))

            output = io.BytesIO()

            image.save(
                output,
                format="JPEG",
                quality=85,
                optimize=True
            )

            return output.getvalue()

    except (
        OSError,
        Image.DecompressionBombError
    ) as error:

        raise ValueError(
            "Invalid photograph. Select a valid JPG or PNG image."
        ) from error


# =========================================================
# PROFILE EDITING PERMISSIONS
# =========================================================

def editable_profile_field(user, key):

    # HR can edit all their employment information.

    if admin():

        return True

    # Employees can edit personal information.

    if key in ("name", "phone"):

        return True

    # Employee login email is controlled by HR.

    if key == "email":

        return False

    # Employees may provide missing employment information
    # during their initial profile setup.
    # Existing official information is controlled by HR.

    return (

        not user["profile_completed_at"]

        and not user[key]

    )


# =========================================================
# MANDATORY PROFILE COMPLETION AFTER LOGIN
# =========================================================

@app.before_request


def required_profile_gate():

    if not g.user:

        return

    # Temporary password change comes first.

    if g.user["must_change_password"]:

        return

    # Allow users to log out or change their passwords.

    if request.endpoint in (

        "logout",

        "password_settings",

        "static"

    ):

        return

    incomplete = profile_required(g.user)

    # Allow users with completed profiles to access
    # their permitted workspace normally.

    if not incomplete:

        session.pop(
            "_sl_profile_form_opened",
            None
        )

        session.pop(
            "_sl_profile_navigation_warning",
            None
        )

        return

    # Open the mandatory form without a warning
    # during the initial login.

    if request.endpoint == "profile_details":

        session["_sl_profile_form_opened"] = True

        return

    # Prevent navigation to another portal page.
    # Only show the warning after the form was opened.

    if session.get("_sl_profile_form_opened"):

        session["_sl_profile_navigation_warning"] = True

    return redirect(

        url_for("profile_details")

    )


# =========================================================
# PROFILE SETUP BEFORE DOCUMENT ONBOARDING
# =========================================================

_profile_hooks = app.before_request_funcs[None]

_profile_hooks.remove(required_profile_gate)

for _index, _hook in enumerate(_profile_hooks):

    if _hook.__name__ in (

        "onboarding_gate",

        "mandatory_onboarding"

    ):

        _existing_onboarding = _hook

        def _onboarding_after_profile():

            if request.endpoint != "profile_details":

                return _existing_onboarding()

        _profile_hooks[_index] = (
            _onboarding_after_profile
        )

        _profile_hooks.insert(
            _index,
            required_profile_gate
        )

        break

else:

    raise RuntimeError(
        "Existing onboarding hook was not found."
    )


# =========================================================
# COMPLETE AND EDIT PERSONAL PROFILE
# =========================================================

@app.route(
    "/profile-details",
    methods=["GET", "POST"]
)
@login_required
def profile_details():

    user = g.user

    first = profile_required(user)

    values = {

        key: str(user[key] or "")

        for key, _, _, _ in PROFILE_FIELDS

    }

    editable = {

        key: editable_profile_field(user, key)

        for key, _, _, _ in PROFILE_FIELDS

    }

    errors = []

    photo_exists = required_profile_photo_exists(user["id"])

    photo_bytes = None


    # =====================================================
    # SAVE PROFILE INFORMATION
    # =====================================================

    if request.method == "POST":

        uploaded_photo = request.files.get("profile_photo")

        if uploaded_photo and uploaded_photo.filename:

            try:

                photo_bytes = prepare_required_profile_photo(
                    uploaded_photo
                )

            except ValueError as error:

                errors.append(str(error))

        elif not photo_exists:

            errors.append(
                "Your profile photograph is mandatory."
            )



        for key, label, kind, limit in PROFILE_FIELDS:

            if editable[key]:

                values[key] = request.form.get(
                    key, ""
                ).strip()

            if key != "project" and not values[key]:

                errors.append(

                    label + " is required. "
                    "Contact HR if this field is locked."

                )

            if len(values[key]) > limit:

                errors.append(
                    label + " is too long."
                )


        # COMPANY EMAIL VALIDATION

        values["email"] = values["email"].lower()

        if not re.fullmatch(

            r"[^\s@]+@[^\s@]+\.[^\s@]+",

            values["email"]

        ):

            errors.append(
                "Enter a valid company email."
            )


        # JOINING DATE VALIDATION

        if not valid_date(values["joining_date"]):

            errors.append(
                "Enter a valid joining date."
            )


        # PHONE NUMBER VALIDATION

        if (

            not re.fullmatch(
                r"\+?[0-9 ()-]{7,30}",
                values["phone"]
            )

            or not 7 <= sum(
                c.isdigit()
                for c in values["phone"]
            ) <= 15

        ):

            errors.append(
                "Enter a valid phone number."
            )


        # REQUIRE PASSWORD BEFORE CHANGING LOGIN EMAIL

        if (

            values["email"] != user["email"].lower()

            and not check_password_hash(

                user["password"],

                request.form.get(
                    "current_password", ""
                )

            )

        ):

            errors.append(

                "Enter your current password "
                "to change your login email."

            )


        # DEPARTMENT VALIDATION

        if (

            not admin()

            and editable["department"]

            and not row(

                "SELECT id FROM departments "
                "WHERE name=?",

                (values["department"],)

            )

        ):

            errors.append(

                "Enter an existing department name "
                "or contact HR."

            )


        # =================================================
        # STORE INFORMATION IN THE DATABASE
        # =================================================

        if not errors:

            connection = db()

            connection.execute(
                "BEGIN IMMEDIATE"
            )

            try:

                duplicate = connection.execute(

                    "SELECT id FROM users "
                    "WHERE id!=? AND "
                    "(lower(email)=lower(?) OR "
                    "lower(employee_code)=lower(?))",

                    (

                        user["id"],

                        values["email"],

                        values["employee_code"]

                    )

                ).fetchone()


                if duplicate:

                    errors.append(

                        "Company email or Employee ID "
                        "is already in use."

                    )

                    connection.rollback()


                else:

                    # Only permitted fields can be updated.
                    # Access role cannot be changed here.

                    keys = [

                        key

                        for key, _, _, _ in PROFILE_FIELDS

                        if editable[key]

                    ]

                    connection.execute(

                        "UPDATE users SET "

                        + ",".join(
                            key + "=?"
                            for key in keys
                        )

                        + ",profile_completed_at=? "
                        "WHERE id=?",

                        [values[key] for key in keys]

                        + [stamp(), user["id"]]

                    )


                    # Register HR-entered departments.

                    if admin():

                        connection.execute(

                            "INSERT OR IGNORE "
                            "INTO departments(name) "
                            "VALUES (?)",

                            (values["department"],)

                        )


                    # AUDIT TRAIL

                    connection.execute(

                        "INSERT INTO audit_logs "
                        "(user_id,action,created_at) "
                        "VALUES (?,?,?)",

                        (

                            user["id"],

                            "Completed or updated personal profile",

                            stamp()

                        )

                    )

                    # Save profile picture in the same transaction.

                    if photo_bytes is not None:

                        connection.execute("""

                            INSERT INTO profile_photos
                            (user_id,jpeg)

                            VALUES (?,?)

                            ON CONFLICT(user_id)
                            DO UPDATE SET jpeg=excluded.jpeg

                        """, (

                            user["id"],

                            photo_bytes

                        ))

                    connection.commit()

                    flash(

                        "Profile information "
                        "saved successfully."

                    )

                    return redirect(
                        url_for("profile")
                    )


            except sqlite3.IntegrityError:

                connection.rollback()

                errors.append(

                    "Email or Employee ID "
                    "is already in use."

                )


            except Exception:

                connection.rollback()

                raise


    # =====================================================
    # PROFESSIONAL PROFILE FORM
    # =====================================================

    return page(

        "Complete Profile" if first else "Edit Profile",

        """

        <style>

        .profile-setup-heading {

            background: linear-gradient(
                120deg,
                #30254f,
                #51416e
            );

            color: white;
            padding: 30px;
            border-radius: 10px;
            margin-bottom: 24px;

        }

        .profile-setup-heading h1 {

            color: white;
            font-size: 27px;
            margin-bottom: 10px;

        }

        .profile-setup-heading p {

            color: #e2dced;
            margin: 0;

        }

        .profile-setup-form {

            max-width: 1100px;
            margin: 0 auto;

        }

        .profile-setup-grid {

            display: grid;

            grid-template-columns:
                repeat(2, minmax(0, 1fr));

            gap: 22px;

        }

        .profile-setup-field {

            min-width: 0;

        }

        .profile-setup-field label {

            display: block;
            margin-bottom: 9px;
            font-weight: 650;
            color: #17191d;

        }

        .profile-setup-field input {

            width: 100%;
            height: 48px;
            border: 1px solid #d8d8e2;
            border-radius: 7px;
            background: #ffffff;
            padding: 12px 14px;

        }

        .profile-setup-field input:focus {

            border-color: #7754ac;
            outline: none;

        }

        .profile-setup-field small {

            display: block;
            margin-top: 6px;

        }

        .profile-setup-field input[readonly] {

            background: #f3f1f7;
            color: #555555;

        }

        .required-star {

            color: #c63245;

        }

        .profile-setup-note {

            background: #f3f0f9;
            border: 1px solid #ded6ed;
            border-radius: 8px;
            padding: 16px;
            color: #30254f;
            margin: 20px 0;

        }

        .profile-setup-error {

            background: #fff0f0;
            border: 1px solid #f2c8cd;
            color: #b42318;
            border-radius: 7px;
            padding: 12px;

        }

        .profile-setup-actions {

            display: flex;
            justify-content: flex-end;
            flex-wrap: wrap;
            gap: 12px;
            border-top: 1px solid #e5e5ec;
            padding-top: 22px;
            margin-top: 26px;

        }

        @media (max-width: 650px) {

            .profile-setup-grid {

                grid-template-columns: 1fr;

            }

            .profile-setup-heading {

                padding: 22px;

            }

            .profile-setup-actions .btn {

                width: 100%;

            }

        }

        </style>


        <div class="profile-setup-form">

            <div class="profile-setup-heading">

                <h1>

                    {{ 'Complete Your Profile'
                       if first
                       else 'Edit Profile Information' }}

                </h1>

                <p>

                    {% if first %}

                        Welcome to STAR LINK.
                        Complete your employment information
                        before accessing your workspace.

                    {% else %}

                        Update your personal and
                        employment information.

                    {% endif %}

                </p>

            </div>


            <div class="card">

                <h2>Employment Information</h2>

                <p class="muted">

                    Fields marked with * are mandatory.

                </p>

                <div class="profile-setup-note">

                    Please provide accurate employment
                    information. Your profile details
                    will be saved to your account.

                </div>


                {% for error in errors %}

                    <p class="profile-setup-error"
                       role="alert">

                        {{ error }}

                    </p>

                {% endfor %}


                <form method="post" enctype="multipart/form-data">

                    {{ token() }}

                    

<style>

.profile-photo-section {

    background: #f8f7fb;
    border: 1px solid #dedbe7;
    border-radius: 12px;
    padding: 26px;
    margin-bottom: 28px;
    text-align: center;

}

.profile-photo-preview {

    width: 115px;
    height: 115px;
    border-radius: 50%;
    object-fit: cover;
    object-position: center;
    border: 4px solid #ded6ed;
    display: block;
    margin: 0 auto 15px;

}

.profile-photo-placeholder {

    width: 115px;
    height: 115px;
    border-radius: 50%;
    background: #e8e3f1;
    color: #7754ac;
    display: grid;
    place-items: center;
    font-size: 40px;
    margin: 0 auto 15px;

}

.profile-photo-upload {

    max-width: 420px;
    margin: 15px auto;

}

.profile-photo-upload input {

    width: 100%;
    background: white;
    border: 1px dashed #a99bc5;
    padding: 13px;
    border-radius: 8px;

}

.profile-photo-upload input::file-selector-button {

    border: none;
    background: #30254f;
    color: white;
    padding: 10px 14px;
    border-radius: 6px;
    margin-right: 10px;
    cursor: pointer;

}

.profile-photo-section h2 {

    margin: 8px 0;

}

.profile-photo-section p {

    margin: 8px 0;

}

.profile-photo-required {

    color: #c63245;
    font-weight: 700;

}

.profile-photo-saved {

    color: #347c49;
    font-weight: 650;

}

</style>


<div class="profile-photo-section">

    <img

        id="required-profile-preview"

        class="profile-photo-preview"

        alt="Selected profile photograph"

        hidden

    >

    <div

        id="required-profile-placeholder"

        class="profile-photo-placeholder"

    >

        &#128100;

    </div>

    <h2>

        Profile Photograph

        <span class="profile-photo-required">

            *

        </span>

    </h2>

    {% if photo_exists %}

        <p class="profile-photo-saved">

            Profile photograph already saved.

        </p>

        <p class="muted">

            Upload another photograph if you want to change it.

        </p>

    {% else %}

        <p class="muted">

            You must upload your profile photograph
            before entering the workspace.

        </p>

    {% endif %}


    <div class="profile-photo-upload">

        <label for="mandatory-profile-photo">

            {{ 'Change Profile Photograph'
               if photo_exists
               else 'Upload Profile Photograph' }}

        </label>

        <input

            type="file"

            id="mandatory-profile-photo"

            name="profile_photo"

            accept="image/jpeg,image/png"

            {% if not photo_exists %}

                required

            {% endif %}

        >

    </div>

    <p class="muted">

        Accepted formats: JPG and PNG.
        Maximum file size: 5 MB.

    </p>

    <p

        id="profile-photo-message"

        role="status"

        class="profile-photo-saved"

    ></p>

</div>


<script>

(() => {

    const input = document.getElementById(
        "mandatory-profile-photo"
    );

    const preview = document.getElementById(
        "required-profile-preview"
    );

    const placeholder = document.getElementById(
        "required-profile-placeholder"
    );

    const message = document.getElementById(
        "profile-photo-message"
    );

    let previewURL = null;

    input.addEventListener("change", () => {

        input.setCustomValidity("");

        message.textContent = "";

        if (previewURL) {

            URL.revokeObjectURL(previewURL);

            previewURL = null;

        }

        preview.hidden = true;

        placeholder.hidden = false;

        const file = input.files[0];

        if (!file) {

            preview.removeAttribute("src");

            return;

        }

        if (
            !["image/jpeg", "image/png"].includes(
                file.type
            )
        ) {

            input.value = "";

            message.textContent =
                "Select a JPG or PNG photograph.";

            return;

        }

        if (file.size > 5 * 1024 * 1024) {

            input.value = "";

            message.textContent =
                "Photograph must be smaller than 5 MB.";

            return;

        }

        previewURL = URL.createObjectURL(file);

        preview.src = previewURL;

        preview.hidden = false;

        placeholder.hidden = true;

        message.textContent =
            "Photograph selected successfully.";

    });

})();

</script>


<div class="profile-setup-grid">


                        {% for key,label,kind,limit in fields %}

                            <div class="profile-setup-field">

                                <label for="profile-{{ key }}">

                                    {{ label }}

                                    {% if key != 'project' %}

                                        <span class="required-star">

                                            *

                                        </span>

                                    {% endif %}

                                </label>


                                <input

                                    id="profile-{{ key }}"

                                    name="{{ key }}"

                                    type="{{ kind }}"

                                    maxlength="{{ limit }}"

                                    value="{{ values[key] }}"

                                    placeholder="Enter {{ label }}"

                                    {% if key != 'project' %}

                                        required

                                    {% endif %}

                                    {% if not editable[key] %}

                                        readonly

                                    {% endif %}

                                >


                                {% if not editable[key] %}

                                    <small class="muted">

                                        Managed by HR

                                    </small>

                                {% endif %}


                                {% if key == 'project' %}

                                    <small class="muted">

                                        Leave empty if no project
                                        is currently assigned.

                                    </small>

                                {% endif %}

                            </div>

                        {% endfor %}


                        <!-- PROTECTED ACCESS ROLE -->

                        <div class="profile-setup-field">

                            <label>Access Role</label>

                            <input

                                value="{{ g.user.role }}"

                                readonly

                            >

                            <small class="muted">

                                Access roles are controlled by HR.

                            </small>

                        </div>


                        {% if admin() %}

                            <div class="profile-setup-field">

                                <label>

                                    Current Password

                                </label>

                                <input

                                    name="current_password"

                                    type="password"

                                    autocomplete="current-password"

                                    placeholder="Required if changing email"

                                >

                                <small class="muted">

                                    Only required when changing
                                    your login email.

                                </small>

                            </div>

                        {% endif %}

                    </div>


                    <div class="profile-setup-actions">

                        {% if not first %}

                            <a

                                class="btn btn-light"

                                href="{{ url_for('profile') }}"

                            >

                                Cancel

                            </a>

                        {% endif %}


                        <button

                            type="submit"

                            class="btn btn-purple"

                        >

                            {{ 'Save Profile & Continue'
                               if first
                               else 'Save Changes' }}

                        </button>

                    </div>

                </form>

            </div>

        </div>

        """,

        first=first,

        fields=PROFILE_FIELDS,

        values=values,

        editable=editable,

        errors=errors,
        photo_exists=photo_exists

    )


# =========================================================
# ADD EDIT PROFILE BUTTON TO EXISTING PROFILE PAGE
# =========================================================

_old_profile_page_renderer = page


def page(title, content, **context):

    if (

        request.endpoint == "profile"

        and request.method == "GET"

    ):

        content = """

        <div style="

            display:flex;

            justify-content:flex-end;

            margin-bottom:16px;

        ">

            <a

                class="btn btn-purple"

                href="{{ url_for('profile_details') }}"

            >

                Edit Profile Information

            </a>

        </div>

        """ + content

    return _old_profile_page_renderer(

        title,

        content,

        **context

    )


# =========================================================
# CONNECT EXISTING PROFILE TO NEW EDITOR
# =========================================================

_previous_profile_view = app.view_functions[
    "profile"
]


@login_required
def profile_with_required_details():

    # Preserve profile photograph uploads.
    # Use the validated editor for profile information.

    if (

        request.method == "POST"

        and request.form.get("action") != "photo"

    ):

        return redirect(
            url_for("profile_details")
        )

    return _previous_profile_view()


app.view_functions[
    "profile"
] = profile_with_required_details





# STAR LINK WORKSPACE HEADER V1

def header_photo_available():

    if not g.user:
        return False

    if g.user["must_change_password"]:
        return False

    # Preserve the mandatory first-login profile setup.

    if (
        "profile_required" in globals()
        and profile_required(g.user)
    ):
        return False

    photograph = row(
        """
        SELECT user_id
        FROM profile_photos
        WHERE user_id=?
        """,
        (g.user["id"],)
    )

    return photograph is not None


app.jinja_env.globals.update(
    header_photo_available=header_photo_available
)





# STAR LINK EMPLOYEE PROFILE MANAGEMENT V1

from PIL import Image, ImageOps

# =========================================================
# EMPLOYEE INFORMATION FIELDS
# =========================================================

MANAGED_PROFILE_FIELDS = (

    ("name", "Full Name", "text", 150),

    ("email", "Company Email", "email", 200),

    ("employee_code", "Employee ID", "text", 60),

    ("designation", "Designation", "text", 150),

    ("department", "Department", "text", 150),

    ("project", "Assigned Project", "text", 200),

    ("joining_date", "Joining Date", "date", 10),

    ("phone", "Phone Number", "tel", 30)

)


# =========================================================
# PRIVATE PROFILE PHOTO CHECK
# =========================================================

def managed_photo_exists(uid):

    return bool(row("""

        SELECT user_id
        FROM profile_photos
        WHERE user_id=?
        AND length(jpeg)>0

    """, (uid,)))


# =========================================================
# MANDATORY FIRST LOGIN PROFILE
# =========================================================

def profile_required(user):

    if not user["profile_completed_at"]:
        return True

    for key, label, kind, limit in MANAGED_PROFILE_FIELDS:

        if key != "project":

            if not str(user[key] or "").strip():
                return True

    return not managed_photo_exists(user["id"])


# =========================================================
# VALIDATE PROFILE PHOTO
# =========================================================

def managed_profile_photo(uploaded):

    raw = uploaded.read(5 * 1024 * 1024 + 1)

    if not raw:

        raise ValueError(
            "The selected photograph is empty."
        )

    if len(raw) > 5 * 1024 * 1024:

        raise ValueError(
            "Profile photograph must not exceed 5 MB."
        )

    try:

        with Image.open(io.BytesIO(raw)) as image:

            if image.format not in ("JPEG", "PNG"):

                raise ValueError(
                    "Only JPG and PNG images are allowed."
                )

            if (
                image.width < 32
                or image.height < 32
                or image.width * image.height > 20_000_000
            ):

                raise ValueError(
                    "Invalid photograph dimensions."
                )

            image.load()

            image = ImageOps.exif_transpose(
                image
            ).convert("RGB")

            image.thumbnail((600, 600))

            result = io.BytesIO()

            image.save(
                result,
                format="JPEG",
                quality=85
            )

            return result.getvalue()

    except (
        OSError,
        Image.DecompressionBombError
    ) as error:

        raise ValueError(
            "Select a valid JPG or PNG photograph."
        ) from error


# =========================================================
# PROFILE MANAGEMENT CSS
# =========================================================

MANAGED_PROFILE_STYLE = """

<style>

.mp-heading {

    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 14px;
    margin-bottom: 22px;

}

.mp-banner {

    background: linear-gradient(
        120deg,
        #30254f,
        #51416e
    );

    color: white;
    padding: 30px;
    border-radius: 12px;
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 24px;
    margin-bottom: 24px;

}

.mp-banner h2 {

    color: white;
    font-size: 26px;
    margin: 0 0 8px;

}

.mp-banner p {

    color: #e4ddee;
    margin: 6px 0;

}

.mp-photo {

    width: 110px;
    height: 110px;
    object-fit: cover;
    border-radius: 50%;
    display: block;
    border: 3px solid #ded6ed;

}

.mp-placeholder {

    width: 110px;
    height: 110px;
    background: #e9e4f1;
    color: #30254f;
    display: grid;
    place-items: center;
    font-size: 38px;
    font-weight: 700;
    border-radius: 50%;

}

.mp-grid {

    display: grid;
    grid-template-columns:
        repeat(3, minmax(0, 1fr));

    gap: 18px;

}

.mp-detail {

    background: #f7f6fa;
    border: 1px solid #dedde7;
    border-radius: 8px;
    padding: 18px;
    min-width: 0;

}

.mp-detail span {

    display: block;
    color: #667080;
    font-size: 13px;
    margin-bottom: 8px;

}

.mp-detail strong {

    color: #17191d;
    font-size: 16px;
    overflow-wrap: anywhere;

}

.mp-form-grid {

    display: grid;
    grid-template-columns:
        repeat(2, minmax(0, 1fr));

    gap: 22px;

}

.mp-form-grid input {

    width: 100%;
    padding: 13px;
    min-height: 45px;
    border: 1px solid #d8d8e2;
    border-radius: 7px;

}

.mp-photo-box {

    background: #f8f7fb;
    border: 1px solid #dedbe7;
    border-radius: 10px;
    padding: 24px;
    margin-bottom: 25px;
    text-align: center;

}

.mp-photo-box img,
.mp-photo-box .mp-placeholder {

    margin: 0 auto 15px;

}

.mp-upload {

    max-width: 430px;
    margin: 15px auto;

}

.mp-actions {

    display: flex;
    justify-content: flex-end;
    flex-wrap: wrap;
    gap: 10px;
    border-top: 1px solid #e1e1e8;
    margin-top: 25px;
    padding-top: 20px;

}

.mp-error {

    background: #fff0f0;
    color: #b42318;
    border: 1px solid #efc7cc;
    padding: 12px;
    border-radius: 7px;

}

.mp-table td {

    white-space: normal !important;
    vertical-align: middle !important;
    padding: 15px !important;

}

.mp-table th {

    background: #f3f1f7;
    color: #30254f;

}

.mp-table .inline {

    gap: 8px;

}

.mp-search {

    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 20px;

}

.mp-search input {

    max-width: 550px;

}

@media (max-width: 900px) {

    .mp-grid {

        grid-template-columns:
            repeat(2, minmax(0, 1fr));

    }

}

@media (max-width: 600px) {

    .mp-grid,
    .mp-form-grid {

        grid-template-columns: 1fr;

    }

    .mp-banner {

        padding: 20px;

    }

    .mp-actions .btn {

        width: 100%;

    }

}

</style>

"""


# =========================================================
# SHARED PROFILE EDITOR
# =========================================================

@login_required
def managed_profile_editor(uid=None):

    actor = g.user

    target_id = uid if uid is not None else actor["id"]

    own_profile = target_id == actor["id"]

    # Only HR can access another employee's editor.

    if not own_profile and not admin():

        abort(403)

    employee = row(

        "SELECT * FROM users WHERE id=?",

        (target_id,)

    )

    if not employee:

        abort(404)

    first = own_profile and profile_required(employee)

    photo_exists = managed_photo_exists(target_id)

    values = {

        key: str(employee[key] or "")

        for key, label, kind, limit
        in MANAGED_PROFILE_FIELDS

    }

    values["nationality"] = sl_normalize_country(
        values.get("nationality", "")
    )

    errors = []

    photo_bytes = None

    current_identity_documents = identity_documents(target_id)

    identity_mandatory = (

        own_profile

        and not employee["identity_profile_v2_completed_at"]

    )

    pending_identity_uploads = []

    created_identity_files = []

    identity_committed = False

    # Employees must complete all required fields.
    # HR can prepare an incomplete new employee account
    # before that employee finishes onboarding.

    require_complete = (

        own_profile

        or bool(employee["profile_completed_at"])

    )

    if request.method == "POST":

        # =================================================
        # VALIDATE PERSONAL AND EMPLOYMENT INFORMATION
        # =================================================

        for key, label, kind, limit in MANAGED_PROFILE_FIELDS:

            values[key] = request.form.get(
                key, ""
            ).strip()

            mandatory = (

                key in ("name", "email")

                or (
                    require_complete
                    and key != "project"
                )

            )

            if mandatory and not values[key]:

                errors.append(
                    label + " is required."
                )

            if len(values[key]) > limit:

                errors.append(
                    label + " is too long."
                )

        values["nationality"] = sl_normalize_country(
            values.get("nationality", "")
        )

        if values["nationality"] not in SL_COUNTRIES:

            errors.append(
                "Please select a valid nationality."
            )

        values["email"] = values["email"].lower()

        if not re.fullmatch(

            r"[^\s@]+@[^\s@]+\.[^\s@]+",

            values["email"]

        ):

            errors.append(
                "Enter a valid company email."
            )

        if (

            values["joining_date"]

            and not valid_date(
                values["joining_date"]
            )

        ):

            errors.append(
                "Enter a valid joining date."
            )

        if values["phone"]:

            if (

                not re.fullmatch(
                    r"\+?[0-9 ()-]{7,30}",
                    values["phone"]
                )

                or not 7 <= sum(
                    c.isdigit()
                    for c in values["phone"]
                ) <= 15

            ):

                errors.append(
                    "Enter a valid phone number."
                )


        # =================================================
        # PROTECT LOGIN EMAIL CHANGES
        # =================================================

        if values["email"] != employee["email"].lower():

            if not check_password_hash(

                actor["password"],

                request.form.get(
                    "current_password", ""
                )

            ):

                errors.append(

                    "Enter your own current password "
                    "to change the login email."

                )


        # =================================================
        # VALIDATE PROFILE PHOTOGRAPH
        # =================================================

        uploaded = request.files.get(
            "profile_photo"
        )

        if uploaded and uploaded.filename:

            try:

                photo_bytes = managed_profile_photo(
                    uploaded
                )

            except ValueError as error:

                errors.append(str(error))

        elif own_profile and not photo_exists:

            errors.append(
                "Your profile photograph is mandatory."
            )


        # =================================================
        # VALIDATE DEPARTMENT
        # =================================================

        if not admin() and values["department"]:

            if not row(

                "SELECT id FROM departments "
                "WHERE name=?",

                (values["department"],)

            ):

                errors.append(

                    "Select an existing department "
                    "or contact HR."

                )


        # =================================================
        # SAVE PROFILE TO DATABASE
        # =================================================

        pending_identity_uploads = validate_identity_submission(

            current_identity_documents,

            identity_mandatory,

            errors

        )

        if not errors:

            connection = db()

            connection.execute("BEGIN IMMEDIATE")

            try:

                duplicate = connection.execute("""

                    SELECT id
                    FROM users
                    WHERE id != ?

                    AND (

                        lower(email)=lower(?)

                        OR (

                            ? != ''

                            AND lower(employee_code)=lower(?)

                        )

                    )

                """, (

                    target_id,

                    values["email"],

                    values["employee_code"],

                    values["employee_code"]

                )).fetchone()

                if duplicate:

                    errors.append(

                        "This email or Employee ID "
                        "is already assigned to another account."

                    )

                    connection.rollback()

                else:

                    keys = [

                        key

                        for key, label, kind, limit
                        in MANAGED_PROFILE_FIELDS

                    ]

                    changed = [

                        key for key in keys

                        if values[key] != str(employee[key] or "")

                    ]

                    sql = (

                        "UPDATE users SET "

                        + ",".join(
                            key + "=?" for key in keys
                        )

                    )

                    parameters = [

                        values[key] for key in keys

                    ]

                    # Only the employee's own completed
                    # submission marks onboarding information
                    # as personally completed.

                    if own_profile:

                        sql += ",profile_completed_at=?"

                        parameters.append(stamp())

                    sql += " WHERE id=?"

                    parameters.append(target_id)

                    connection.execute(
                        sql,
                        parameters
                    )


                    # =================================================
                    # SAVE PROFILE PHOTO
                    # =================================================

                    if photo_bytes is not None:

                        connection.execute("""

                            INSERT INTO profile_photos
                            (user_id,jpeg)

                            VALUES (?,?)

                            ON CONFLICT(user_id)

                            DO UPDATE SET jpeg=excluded.jpeg

                        """, (

                            target_id,

                            photo_bytes

                        ))

                        changed.append("profile_photo")


                    # HR can register a new department.

                    if admin() and values["department"]:

                        connection.execute("""

                            INSERT OR IGNORE
                            INTO departments(name)
                            VALUES (?)

                        """, (

                            values["department"],

                        ))


                    # =================================================
                    # RECORD WHO EDITED THE PROFILE
                    # =================================================

                    connection.execute("""

                        INSERT INTO audit_logs

                        (user_id,action,created_at)

                        VALUES (?,?,?)

                    """, (

                        actor["id"],

                        f"Updated profile for employee "
                        f"#{target_id}. Changed fields: "
                        + ", ".join(changed),

                        stamp()

                    ))

                    store_identity_documents(

                        connection,

                        target_id,

                        pending_identity_uploads,

                        created_identity_files

                    )

                    if pending_identity_uploads:

                        connection.execute(

                            "INSERT INTO audit_logs "
                            "(user_id,action,created_at) "
                            "VALUES (?,?,?)",

                            (

                                actor["id"],

                                f"Updated identity documents "
                                f"for employee #{target_id}",

                                stamp()

                            )

                        )

                    if own_profile:

                        connection.execute("""

                            UPDATE users

                            SET identity_profile_v2_completed_at=
                                COALESCE(
                                    identity_profile_v2_completed_at,
                                    ?
                                )

                            WHERE id=?

                        """, (

                            stamp(),

                            target_id

                        ))

                    connection.commit()

                    identity_committed = True

                    flash(
                        "Profile information saved successfully."
                    )

                    if own_profile:

                        return redirect(

                            url_for(
                                "dashboard" if first else "profile"
                            )

                        )

                    return redirect(

                        url_for(
                            "hr_employee_profile",
                            uid=target_id
                        )

                    )

            except sqlite3.IntegrityError:

                connection.rollback()

                if not identity_committed:

                    cleanup_identity_files(created_identity_files)

                errors.append(
                    "Email or Employee ID already exists."
                )

            except Exception:

                connection.rollback()

                if not identity_committed:

                    cleanup_identity_files(created_identity_files)

                raise


    # =====================================================
    # EMPLOYEE PROFILE EDIT FORM
    # =====================================================

    departments = rows("""

        SELECT name
        FROM departments
        ORDER BY name

    """)

    return page(

        "Complete Profile" if first else "Edit Profile",

        MANAGED_PROFILE_STYLE + """

        <div class="mp-heading">

            <div>

                <h1>

                    {{ 'Complete Your Profile'
                       if first
                       else 'Edit Profile Information' }}

                </h1>

                <p class="muted">

                    {{ employee.name }}

                    ·

                    {{ employee.employee_code or 'New Employee' }}

                </p>

            </div>

            {% if not own_profile %}

                <a
                    class="btn btn-light"
                    href="{{ url_for('hr_employee_profiles') }}"
                >

                    Back to Employee Profiles

                </a>

            {% endif %}

        </div>


        <div class="card">

            {% for error in errors %}

                <p class="mp-error" role="alert">

                    {{ error }}

                </p>

            {% endfor %}


            <form

                method="post"

                enctype="multipart/form-data"

            >

                {{ token() }}


                <!-- PROFILE PHOTO -->

                <div class="mp-photo-box">

                    {% if photo_exists and not first %}

                        <img

                            class="mp-photo"

                            id="mp-preview"

                            src="{{ url_for(
                                'profile_picture',
                                uid=employee.id
                            ) }}"

                            alt="Profile photograph"

                        >

                    {% else %}

                        <div

                            class="mp-placeholder"

                            id="mp-placeholder"

                        >

                            {{ employee.name[:1]|upper }}

                        </div>

                        <img

                            class="mp-photo"

                            id="mp-preview"

                            alt="Profile photograph"

                            hidden

                        >

                    {% endif %}


                    <h2>Profile Photograph *</h2>

                    <p class="muted">

                        {{ 'Change your existing photograph.'
                           if photo_exists
                           else 'Upload a profile photograph.' }}

                    </p>

                    <div class="mp-upload">

                        <input

                            type="file"

                            name="profile_photo"

                            id="mp-file"

                            accept="image/jpeg,image/png"

                            {% if own_profile and not photo_exists %}

                                required

                            {% endif %}

                        >

                    </div>

                    <p class="muted">

                        JPG or PNG. Maximum 5 MB.

                    </p>

                </div>


                <!-- EMPLOYMENT INFORMATION -->

                <h2>Employment Information</h2>

                <p class="muted">

                    Fields marked with * are required.

                </p>

                <div class="mp-form-grid">

                    {% for key,label,kind,limit in fields %}

                        <div>

                            {% set mandatory =
                                key in ['name','email']
                                or (require_complete and key!='project')
                            %}

                            <label for="mp-{{ key }}">

                                {{ label }}

                                {% if mandatory %}

                                    <span style="color:#c63245">

                                        *

                                    </span>

                                {% endif %}

                            </label>


                            

{% if key == 'nationality' %}

    <select

        name="nationality"

        id="mp-nationality"

        required

        class="sl-country-select"

    >

        <option value="">

            Select Nationality

        </option>

        {% for country in sl_countries %}

            <option

                value="{{ country }}"

                {{ 'selected'
                   if values[key] == country
                   else '' }}

            >

                {{ country }}

            </option>

        {% endfor %}

    </select>

{% elif key == 'department' and not admin() %}



                                <select

                                    name="{{ key }}"

                                    id="mp-{{ key }}"

                                    {% if mandatory %}required{% endif %}

                                >

                                    <option value="">

                                        Select Department

                                    </option>

                                    {% for department in departments %}

                                        <option

                                            value="{{ department.name }}"

                                            {{ 'selected'
                                                if values[key]==department.name
                                                else '' }}

                                        >

                                            {{ department.name }}

                                        </option>

                                    {% endfor %}

                                </select>

                            {% else %}

                                <input

                                    name="{{ key }}"

                                    id="mp-{{ key }}"

                                    type="{{ kind }}"

                                    value="{{ values[key] }}"

                                    maxlength="{{ limit }}"

                                    placeholder="Enter {{ label }}"

                                    {% if mandatory %}required{% endif %}

                                >

                            {% endif %}


                            {% if key == 'project' %}

                                <small class="muted">

                                    Optional

                                </small>

                            {% endif %}

                        </div>

                    {% endfor %}


                    <!-- ACCESS ROLE IS PROTECTED -->

                    <div>

                        <label>Access Role</label>

                        <input

                            value="{{ employee.role }}"

                            readonly

                            style="background:#f3f1f7"

                        >

                        <small class="muted">

                            Managed separately by HR.

                        </small>

                    </div>


                    <!-- PASSWORD FOR EMAIL CHANGES -->

                    <div>

                        <label>Your Current Password</label>

                        <input

                            name="current_password"

                            type="password"

                            autocomplete="current-password"

                            placeholder="Required only for email changes"

                        >

                        <small class="muted">

                            Required only when changing the login email.

                        </small>

                    </div>

                </div>


                

<!-- MANDATORY PERSONAL DOCUMENTS -->

    <!-- STAR LINK DOCUMENT NAVIGATION WARNING -->

    {% if identity_navigation_warning and first %}

        <script>

        window.addEventListener("load", function () {

            {% set missing_docs =
                current_identity_documents
                | rejectattr('valid')
                | list
            %}

            {% if identity_mandatory and missing_docs %}

                window.alert(

                    "Complete Your Profile\n\n" +

                    "Please upload all mandatory documents " +

                    "and enter their expiry dates before " +

                    "accessing another page."

                );

            {% else %}

                window.alert(

                    "Complete Your Profile\n\n" +

                    "Please complete all mandatory profile " +

                    "information before continuing."

                );

            {% endif %}

        }, {once: true});

        </script>

    {% endif %}

    





<div class="sl-document-section">

    <h2>Identity & Insurance Documents</h2>

    <p class="muted">

        Upload your required documents and expiry dates.
        Previously saved documents do not need to be uploaded again.

    </p>

    <div class="table-wrap">

        <table class="sl-document-table">

            <thead>

                <tr>

                    <th>Document</th>

                    <th>Saved Expiry Date</th>

                    <th>Verification Status</th>

                    <th>Upload / Renew Document</th>

                    <th>Document Copy</th>

                </tr>

            </thead>

            <tbody>

                {% for doc in current_identity_documents %}

                {% set must_upload =
                    identity_mandatory and not doc.valid
                %}

                {% set show_renewal =
                    doc.available and not must_upload
                %}

                <tr>

                    <td>

                        <span class="sl-document-name">

                            {{ doc.label }}

                        </span>

                        <span style="color:#c63245">*</span>

                    </td>


                    <!-- SAVED EXPIRY DATE -->

                    <td>

                        {{ doc.expiry or '—' }}

                        {% if doc.available %}

                            <small style="
                                color:{{ doc.expiry_color }};
                                font-weight:650;
                            ">

                                {{ doc.expiry_status }}

                            </small>

                        {% endif %}

                    </td>


                    <!-- HR VERIFICATION STATUS -->

                    <td>

                        {% if doc.available %}

                            {{ badge(doc.status) }}

                        {% else %}

                            —

                        {% endif %}

                    </td>


                    <!-- DIRECT DOCUMENT UPLOAD -->

                    <td>

                        {% if show_renewal %}

                            <details class="sl-document-renewal">

                                <summary>

                                    Replace / Renew

                                </summary>

                        {% endif %}


                        <div class="sl-document-upload">

                            <div>

                                <label for="sl-file-{{ doc.key }}">

                                    Document Copy

                                    {% if must_upload %}*{% endif %}

                                </label>

                                <input

                                    type="file"

                                    id="sl-file-{{ doc.key }}"

                                    name="identity_file_{{ doc.key }}"

                                    accept=".pdf,.jpg,.jpeg,.png"

                                    {% if must_upload %}

                                        required

                                    {% endif %}

                                >

                                <small class="muted">

                                    PDF, JPG or PNG · Max 3 MB

                                </small>

                            </div>


                            <div>

                                <label for="sl-expiry-{{ doc.key }}">

                                    Expiry Date *

                                </label>

                                <input

                                    type="date"

                                    id="sl-expiry-{{ doc.key }}"

                                    name="identity_expiry_{{ doc.key }}"

                                    min="{{ identity_today }}"

                                    {% if must_upload %}

                                        required

                                    {% endif %}

                                >

                            </div>

                        </div>


                        {% if show_renewal %}

                            </details>

                        {% endif %}

                    </td>


                    <!-- DOWNLOAD SAVED DOCUMENT -->

                    <td>

                        {% if doc.available and not first %}

                            <a

                                class="btn btn-small btn-light"

                                href="{{ url_for(
                                    'identity_document_copy',
                                    record_id=doc.id
                                ) }}"

                            >

                                Download Copy

                            </a>

                        {% else %}

                            —

                        {% endif %}

                    </td>

                </tr>

                {% endfor %}

            </tbody>

        </table>

    </div>

    <div class="sl-document-note">

        Existing documents are retained.

        Upload a new copy only when a document is missing
        or requires replacement.

    </div>

</div>


<div class="mp-actions">

                    {% if not first %}

                        <a

                            class="btn btn-light"

                            href="{{
                                url_for('profile')
                                if own_profile
                                else url_for(
                                    'hr_employee_profile',
                                    uid=employee.id
                                )
                            }}"

                        >

                            Cancel

                        </a>

                    {% endif %}


                    <button

                        class="btn btn-purple"

                        type="submit"

                    >

                        {{ 'Save Profile & Continue'
                           if first
                           else 'Save Changes' }}

                    </button>

                </div>

            </form>

        </div>


        <script>

        (() => {

            const file = document.getElementById(
                'mp-file'
            );

            const preview = document.getElementById(
                'mp-preview'
            );

            const placeholder = document.getElementById(
                'mp-placeholder'
            );

            let previewURL = null;

            file.addEventListener('change', () => {

                file.setCustomValidity('');

                const selected = file.files[0];

                if (!selected) return;

                if (
                    !['image/jpeg','image/png']
                    .includes(selected.type)
                ) {

                    file.setCustomValidity(
                        'Select a JPG or PNG image.'
                    );

                    file.reportValidity();

                    return;

                }

                if (selected.size > 5 * 1024 * 1024) {

                    file.setCustomValidity(
                        'Maximum image size is 5 MB.'
                    );

                    file.reportValidity();

                    return;

                }

                if (previewURL) {

                    URL.revokeObjectURL(previewURL);

                }

                previewURL = URL.createObjectURL(selected);

                preview.src = previewURL;

                preview.hidden = false;

                if (placeholder) {

                    placeholder.hidden = true;

                }

            });

        })();

        </script>

        """,

        employee=employee,

        own_profile=own_profile,

        first=first,

        fields=MANAGED_PROFILE_FIELDS,

        require_complete=require_complete,

        values=values,

        errors=errors,

        photo_exists=photo_exists,

        departments=departments,

        current_identity_documents=current_identity_documents,

        identity_mandatory=identity_mandatory,

        identity_today=today(),

        identity_navigation_warning=session.pop(

            "_sl_profile_navigation_warning",

            False

        )

    )


# =========================================================
# REPLACE EXISTING PERSONAL PROFILE EDITOR
# =========================================================

app.view_functions[
    "profile_details"
] = managed_profile_editor


# =========================================================
# HR ONLY - ALL EMPLOYEE PROFILES
# =========================================================

@app.get("/hr/employee-profiles")
@admin_required
def hr_employee_profiles():

    query = request.args.get(
        "q", ""
    ).strip()[:150]

    search_value = "%" + query + "%"

    employees = rows("""

        SELECT

            id,

            name,

            email,

            employee_code,

            designation,

            department,

            project,

            status

        FROM users

        WHERE

            name LIKE ?

            OR employee_code LIKE ?

            OR department LIKE ?

            OR designation LIKE ?

            OR email LIKE ?

        ORDER BY name

    """, (

        search_value,

        search_value,

        search_value,

        search_value,

        search_value

    ))

    return page(

        "Employee Profiles",

        MANAGED_PROFILE_STYLE + """

        <div class="mp-heading">

            <div>

                <h1>Employee Profiles</h1>

                <p class="muted">

                    View and manage all employee profiles.

                    HR administrator access only.

                </p>

            </div>

            <a

                class="btn btn-purple"

                href="{{ url_for('add_employee') }}"

            >

                + Add Employee

            </a>

        </div>


        <div class="stat-grid">

            <div class="stat">

                Total Employees

                <div class="stat-number">

                    {{ total }}

                </div>

            </div>

            <div class="stat">

                Matching Profiles

                <div class="stat-number">

                    {{ employees|length }}

                </div>

            </div>

        </div>


        <div class="card">

            <h2>Employee Directory</h2>

            <form method="get" class="mp-search">

                <input

                    name="q"

                    value="{{ query }}"

                    placeholder="Search name, ID, department or email..."

                >

                <button class="btn btn-purple">

                    Search

                </button>

                <a

                    class="btn btn-light"

                    href="{{ url_for('hr_employee_profiles') }}"

                >

                    Clear

                </a>

            </form>


            <div class="table-wrap">

                <table class="mp-table">

                    <thead>

                        <tr>

                            <th>Employee Name</th>

                            <th>Employee ID</th>

                            <th>Designation</th>

                            <th>Department</th>

                            <th>Project</th>

                            <th>Status</th>

                            <th>Actions</th>

                        </tr>

                    </thead>

                    <tbody>

                        {% for employee in employees %}

                            <tr>

                                <td>

                                    <strong>

                                        {{ employee.name }}

                                    </strong>

                                    <br>

                                    <small class="muted">

                                        {{ employee.email }}

                                    </small>

                                </td>

                                <td>

                                    {{ employee.employee_code or '—' }}

                                </td>

                                <td>

                                    {{ employee.designation or '—' }}

                                </td>

                                <td>

                                    {{ employee.department or '—' }}

                                </td>

                                <td>

                                    {{ employee.project or '—' }}

                                </td>

                                <td>

                                    {{ badge(employee.status) }}

                                </td>

                                <td>

                                    <div class="inline">

                                        <a

                                            class="btn btn-small btn-light"

                                            href="{{ url_for(
                                                'hr_employee_profile',
                                                uid=employee.id
                                            ) }}"

                                        >

                                            View Profile

                                        </a>

                                        <a

                                            class="btn btn-small btn-purple"

                                            href="{{ url_for(
                                                'hr_edit_employee_profile',
                                                uid=employee.id
                                            ) }}"

                                        >

                                            Edit

                                        </a>

                                    </div>

                                </td>

                            </tr>

                        {% else %}

                            <tr>

                                <td colspan="7">

                                    No matching employees found.

                                </td>

                            </tr>

                        {% endfor %}

                    </tbody>

                </table>

            </div>

        </div>

        """,

        employees=employees,

        query=query,

        total=count(

            "SELECT COUNT(*) FROM users"

        )

    )


# =========================================================
# HR ONLY - VIEW INDIVIDUAL EMPLOYEE PROFILE
# =========================================================

@app.get("/hr/employee-profiles/<int:uid>")
@admin_required
def hr_employee_profile(uid):

    employee = row(

        "SELECT * FROM users WHERE id=?",

        (uid,)

    )

    if not employee:

        abort(404)

    return page(

        "Employee Profile",

        MANAGED_PROFILE_STYLE + """

        <div class="mp-heading">

            <a

                class="btn btn-light"

                href="{{ url_for('hr_employee_profiles') }}"

            >

                Back to Employee Profiles

            </a>

            <a

                class="btn btn-purple"

                href="{{ url_for(
                    'hr_edit_employee_profile',
                    uid=employee.id
                ) }}"

            >

                Edit Employee Profile

            </a>

        </div>


        <div class="mp-banner">

            {% if has_photo %}

                <img

                    class="mp-photo"

                    src="{{ url_for(
                        'profile_picture',
                        uid=employee.id
                    ) }}"

                    alt="Employee photograph"

                >

            {% else %}

                <div class="mp-placeholder">

                    {{ employee.name[:1]|upper }}

                </div>

            {% endif %}

            <div>

                <h2>{{ employee.name }}</h2>

                <p>{{ employee.designation or 'Not assigned' }}</p>

                <p>{{ employee.department or 'Not assigned' }}</p>

                {{ badge(employee.status) }}

            </div>

        </div>


        <div class="card">

            <h2>Employment Information</h2>

            <div class="mp-grid">

                {% for key,label,kind,limit in fields %}

                    <div class="mp-detail">

                        <span>{{ label }}</span>

                        <strong>

                            {{ employee[key] or 'Not assigned' }}

                        </strong>

                    </div>

                {% endfor %}


                <div class="mp-detail">

                    <span>Access Role</span>

                    <strong>

                        {{ employee.role }}

                    </strong>

                </div>


                <div class="mp-detail">

                    <span>Profile Completion</span>

                    <strong>

                        {{ 'Completed'
                           if complete
                           else 'Pending' }}

                    </strong>

                </div>

            </div>

        </div>


        <div class="card">

            <h2>HR Administration</h2>

            <p class="muted">

                Configure this employee's permissions
                and approval responsibilities.

            </p>

            <div class="inline">

                <a

                    class="btn btn-purple"

                    href="{{ url_for(
                        'permissions',
                        employee=employee.id
                    ) }}"

                >

                    Manage Permissions

                </a>

                {% if employee.role == 'Employee' %}

                    <a

                        class="btn"

                        href="{{ url_for(
                            'workflow_settings',
                            uid=employee.id
                        ) }}"

                    >

                        Workflow Configuration

                    </a>

                {% endif %}

            </div>

        </div>

        """,

        employee=employee,

        fields=MANAGED_PROFILE_FIELDS,

        has_photo=managed_photo_exists(uid),

        complete=not profile_required(employee)

    )


# =========================================================
# HR ONLY - EDIT ANY EMPLOYEE PROFILE
# =========================================================

@app.route(

    "/hr/employee-profiles/<int:uid>/edit",

    methods=["GET", "POST"]

)
@admin_required
def hr_edit_employee_profile(uid):

    return managed_profile_editor(uid)


# =========================================================
# ADD EMPLOYEE PROFILES TO HR DASHBOARD AND SIDEBAR
# =========================================================

_previous_employee_menu = visible_menu


def visible_menu():

    menu = _previous_employee_menu()

    if not g.user:

        return menu

    if g.user["must_change_password"]:

        return menu

    if profile_required(g.user):

        return []

    if admin():

        menu.append({

            "name": "Employee Profile Management",

            "items": [

                {

                    "label": "Employee Profiles",

                    "link": url_for(
                        "hr_employee_profiles"
                    ),

                    "icon": "users",

                    "color": "purple"

                }

            ]

        })

    return menu


# =========================================================
# ADD BUTTONS TO PERSONAL PROFILE
# =========================================================

_previous_managed_page = page


def page(title, content, **context):

    if (

        g.user

        and request.endpoint == "profile"

        and request.method == "GET"

    ):

        content = """

        <div style="

            display:flex;

            justify-content:flex-end;

            flex-wrap:wrap;

            gap:10px;

            margin-bottom:16px;

        ">

            <a

                class="btn btn-purple"

                href="{{ url_for('profile_details') }}"

            >

                Edit My Information

            </a>

            {% if admin() %}

                <a

                    class="btn"

                    href="{{ url_for('hr_employee_profiles') }}"

                >

                    View All Employee Profiles

                </a>

            {% endif %}

        </div>

        """ + content

    return _previous_managed_page(

        title,

        content,

        **context

    )





# STAR LINK EMPLOYEE IDENTITY DOCUMENTS V1

from PIL import Image
from datetime import date, timedelta


# =========================================================
# NEW PERSONAL INFORMATION FIELDS
# =========================================================

MANAGED_PROFILE_FIELDS += (

    ("nationality", "Nationality", "text", 100),

    ("current_address", "Current Residential Address", "text", 500),

)


# =========================================================
# MANDATORY DOCUMENT TYPES
# =========================================================

IDENTITY_DOCUMENT_TYPES = (

    ("emirates_id", "Emirates ID", "Emirates ID"),

    ("passport", "Passport Copy", "Passport"),

    ("health_insurance", "Health Insurance", "Medical Insurance"),

    ("uae_visa", "UAE Residence Visa Copy", "Residence Visa"),

)


# =========================================================
# DATABASE MIGRATION
# =========================================================

_identity_previous_init = init_db


def init_db():

    _identity_previous_init()

    connection = db()

    connection.execute("BEGIN IMMEDIATE")

    try:

        columns = {
            item[1]
            for item in connection.execute(
                "PRAGMA table_info(users)"
            )
        }

        for name in (

            "nationality",

            "current_address",

            "identity_profile_v2_completed_at"

        ):

            if name not in columns:

                connection.execute(

                    f"ALTER TABLE users "
                    f"ADD COLUMN {name} TEXT"

                )

        connection.commit()

    except Exception:

        connection.rollback()

        raise


# =========================================================
# GET CURRENT EMPLOYEE DOCUMENTS
# =========================================================

def identity_documents(uid):

    documents = rows("""

        SELECT id, category, filename,
               original_name, expiry_date,
               status, uploaded_at

        FROM documents

        WHERE user_id=?

        ORDER BY id DESC

    """, (uid,))

    results = []

    for key, label, category in IDENTITY_DOCUMENT_TYPES:

        categories = {category}

        if key == "health_insurance":

            categories.add("Health Insurance")

        document = next(

            (
                item for item in documents
                if item["category"] in categories
            ),

            None

        )

        result = {

            "key": key,

            "label": label,

            "category": category,

            "id": None,

            "expiry": "",

            "status": "Not Submitted",

            "expiry_status": "Missing",

            "expiry_color": "#c63245",

            "available": False,

            "valid": False

        }

        if document:

            filename = document["filename"] or ""

            available = bool(

                filename

                and Path(filename).name == filename

                and (UPLOADS / filename).is_file()

            )

            expiry = document["expiry_date"] or ""

            result.update({

                "id": document["id"],

                "expiry": expiry,

                "status": document["status"],

                "available": available

            })

            if available and valid_date(expiry):

                remaining = (

                    date.fromisoformat(expiry)

                    - date.fromisoformat(today())

                ).days

                if remaining < 0:

                    result["expiry_status"] = "Expired"

                    result["expiry_color"] = "#c63245"

                elif remaining <= 30:

                    result["expiry_status"] = "Expiring Soon"

                    result["expiry_color"] = "#b7791f"

                else:

                    result["expiry_status"] = "Valid"

                    result["expiry_color"] = "#347c49"

                result["valid"] = (

                    remaining >= 0

                    and document["status"] in (

                        "Pending",

                        "Verified"

                    )

                )

            elif available:

                result["expiry_status"] = "Expiry Date Required"

        results.append(result)

    return results


# =========================================================
# MANDATORY PERSONAL INFORMATION
# =========================================================

_identity_previous_profile_required = profile_required


def profile_required(user):

    if _identity_previous_profile_required(user):

        return True

    if not str(user["nationality"] or "").strip():

        return True

    if not str(user["current_address"] or "").strip():

        return True

    # Ask for the documents during initial setup.
    # Do not block every future login when a document expires.

    return not bool(
        user["identity_profile_v2_completed_at"]
    )


# =========================================================
# VALIDATE DOCUMENT UPLOADS
# =========================================================

def prepare_identity_document(uploaded):

    original = secure_filename(uploaded.filename or "")

    extension = Path(original).suffix.lower()

    if extension not in (".pdf", ".jpg", ".jpeg", ".png"):

        raise ValueError(
            "Only PDF, JPG and PNG documents are allowed."
        )

    raw = uploaded.read(3 * 1024 * 1024 + 1)

    if not raw:

        raise ValueError("The document is empty.")

    if len(raw) > 3 * 1024 * 1024:

        raise ValueError(
            "Each document must be smaller than 3 MB."
        )

    if extension == ".pdf":

        if not raw.startswith(b"%PDF-"):

            raise ValueError("Invalid PDF document.")

    else:

        try:

            with Image.open(io.BytesIO(raw)) as image:

                expected = (

                    "PNG" if extension == ".png"

                    else "JPEG"

                )

                if image.format != expected:

                    raise ValueError(
                        "Image contents do not match its extension."
                    )

                if image.width * image.height > 20_000_000:

                    raise ValueError(
                        "Document image is too large."
                    )

                image.verify()

        except (

            OSError,

            Image.DecompressionBombError

        ) as error:

            raise ValueError(
                "Invalid document image."
            ) from error

    return {

        "raw": raw,

        "original_name": original,

        "filename": uuid.uuid4().hex + extension

    }


# =========================================================
# VALIDATE ALL MANDATORY DOCUMENTS BEFORE SAVING
# =========================================================

def validate_identity_submission(
    current_documents,
    mandatory,
    errors
):

    uploads = []

    for document in current_documents:

        key = document["key"]

        uploaded = request.files.get(
            "identity_file_" + key
        )

        expiry = request.form.get(

            "identity_expiry_" + key,

            ""

        ).strip()

        if uploaded and uploaded.filename:

            if not valid_date(expiry) or expiry < today():

                errors.append(

                    document["label"]

                    + ": Enter a valid, unexpired expiry date."

                )

                continue

            try:

                prepared = prepare_identity_document(uploaded)

                prepared["category"] = document["category"]

                prepared["expiry"] = expiry

                uploads.append(prepared)

            except ValueError as error:

                errors.append(

                    document["label"] + ": " + str(error)

                )

        elif mandatory and not document["valid"]:

            errors.append(

                document["label"]

                + " and its expiry date are mandatory."

            )

        elif expiry and expiry != document["expiry"]:

            errors.append(

                document["label"]

                + ": Upload the renewed document "
                  "when changing its expiry date."

            )

    return uploads


# =========================================================
# SAVE DOCUMENTS IN THE EXISTING DOCUMENT SYSTEM
# =========================================================

def store_identity_documents(
    connection,
    uid,
    uploads,
    created_files
):

    for document in uploads:

        destination = UPLOADS / document["filename"]

        with destination.open("xb") as file:

            created_files.append(destination)

            file.write(document["raw"])

            file.flush()

            os.fsync(file.fileno())

        connection.execute("""

            INSERT INTO documents (

                user_id,

                title,

                category,

                filename,

                original_name,

                expiry_date,

                status,

                uploaded_at

            )

            VALUES (?,?,?,?,?,?,'Pending',?)

        """, (

            uid,

            document["category"],

            document["category"],

            document["filename"],

            document["original_name"],

            document["expiry"],

            stamp()

        ))


# =========================================================
# CLEAN UP FILES IF DATABASE SAVING FAILS
# =========================================================

def cleanup_identity_files(files):

    for file in files:

        file.unlink(missing_ok=True)


# =========================================================
# SECURE DOCUMENT DOWNLOAD
# ONLY HR AND THE DOCUMENT OWNER
# =========================================================

@app.get("/profile-document/<int:record_id>")
@login_required
def identity_document_copy(record_id):

    document = row("""

        SELECT *

        FROM documents

        WHERE id=?

        AND category IN (

            'Emirates ID',

            'Passport',

            'Medical Insurance',

            'Health Insurance', 'Residence Visa'

        )

    """, (record_id,))

    if not document:

        abort(404)

    if not admin() and document["user_id"] != g.user["id"]:

        abort(404)

    filename = document["filename"] or ""

    if not filename or Path(filename).name != filename:

        abort(404)

    if not (UPLOADS / filename).is_file():

        abort(404)

    audit(
        f"Accessed personal document #{record_id}"
    )

    response = send_from_directory(

        str(UPLOADS),

        filename,

        as_attachment=True,

        download_name=secure_filename(
            document["original_name"]
        ),

        mimetype="application/octet-stream"

    )

    response.headers["Cache-Control"] = "private, no-store"

    response.headers["X-Content-Type-Options"] = "nosniff"

    return response


# =========================================================
# PROFESSIONAL PERSONAL INFORMATION SECTION
# =========================================================

IDENTITY_PROFILE_PANEL = """

<div class="card">

    <h2>Personal Information & Documents</h2>

    {% if identity_own %}

    <div class="employee-details-grid">

        <div class="employee-detail">

            <span>Nationality</span>

            <strong>
                {{ identity_owner.nationality or 'Not provided' }}
            </strong>

        </div>

        <div class="employee-detail"
             style="grid-column:span 1">

            <span>Current Residential Address</span>

            <strong style="white-space:normal">
                {{ identity_owner.current_address or 'Not provided' }}
            </strong>

        </div>

    </div>

    <br>

    {% endif %}

    <h2>Identity & Insurance Documents</h2>

    <p class="muted">

        Review your current documents, expiry dates
        and HR verification status.

    </p>

    <div class="table-wrap">

        <table class="identity-document-table">

            <thead>

                <tr>

                    <th>Document</th>

                    <th>Expiry Date</th>

                    <th>Expiry Status</th>

                    <th>HR Verification</th>

                    <th>Document Copy</th>

                </tr>

            </thead>

            <tbody>

                {% for doc in identity_records %}

                <tr>

                    <td><strong>{{ doc.label }}</strong></td>

                    <td>{{ doc.expiry or 'Not provided' }}</td>

                    <td>

                        <span style="
                            color:{{ doc.expiry_color }};
                            font-weight:650;
                        ">

                            {{ doc.expiry_status }}

                        </span>

                    </td>

                    <td>{{ badge(doc.status) }}</td>

                    <td>

                        {% if doc.available %}

                            <a
                                class="btn btn-small btn-light"
                                href="{{ url_for(
                                    'identity_document_copy',
                                    record_id=doc.id
                                ) }}"
                            >

                                Download Copy

                            </a>

                        {% else %}

                            <span class="muted">
                                Not available
                            </span>

                        {% endif %}

                    </td>

                </tr>

                {% endfor %}

            </tbody>

        </table>

    </div>

    <br>

    <a
        class="btn btn-purple"
        href="{{ url_for('profile_details')
                 if identity_own
                 else url_for(
                     'hr_edit_employee_profile',
                     uid=identity_owner.id
                 ) }}"
    >

        Update Personal Information & Documents

    </a>

</div>

"""


# =========================================================
# SHOW DOCUMENTS ON PERSONAL AND HR PROFILE PAGES
# =========================================================

_identity_previous_page = page


def page(title, content, **context):

    if (

        g.user

        and request.method == "GET"

        and request.endpoint in (

            "profile",

            "hr_employee_profile"

        )

    ):

        own = request.endpoint == "profile"

        if not own and not admin():

            abort(403)

        uid = (

            g.user["id"]

            if own

            else request.view_args["uid"]

        )

        employee = row(

            "SELECT id,nationality,current_address "
            "FROM users WHERE id=?",

            (uid,)

        )

        if employee:

            context.update({

                "identity_owner": employee,

                "identity_records": identity_documents(uid),

                "identity_own": own

            })

            content += IDENTITY_PROFILE_PANEL

    return _identity_previous_page(

        title,

        content,

        **context

    )





# STAR LINK ADMIN APPROVAL CONTROL V1

# =========================================================
# AVAILABLE APPROVAL STATUSES
# =========================================================

for module in ("tasks", "support"):

    if module in MODULES:

        extra_statuses = (
            ["Approved", "Rejected"]
            if module == "tasks"
            else ["Rejected"]
        )

        for status in extra_statuses:

            if status not in MODULES[module]["statuses"]:

                MODULES[module]["statuses"].append(status)


HR_ACTIVE_STATUSES = (
    "Pending",
    "Open",
    "In Progress"
)


# =========================================================
# APPROVAL INTERFACE DESIGN
# =========================================================

HR_APPROVAL_STYLE = """

<style>

.hr-approval-heading {
    background: linear-gradient(
        120deg, #30254f, #51416e
    );
    color: white;
    border-radius: 12px;
    padding: 28px;
    margin-bottom: 24px;
}

.hr-approval-heading h1 {
    color: white;
    margin: 0 0 10px;
}

.hr-approval-heading p {
    color: #e6dff0;
    margin: 0;
}

.hr-approval-grid {
    display: grid;
    grid-template-columns:
        repeat(auto-fit, minmax(180px, 1fr));
    gap: 15px;
    margin-bottom: 25px;
}

.hr-approval-stat {
    background: white;
    border: 1px solid #dedce6;
    border-radius: 9px;
    padding: 20px;
    color: #17191d;
}

.hr-approval-stat strong {
    display: block;
    font-size: 27px;
    margin-top: 9px;
    color: #30254f;
}

.hr-approval-filters {
    display: grid;
    grid-template-columns:
        2fr 1fr 2fr auto;
    align-items: end;
    gap: 15px;
}

.hr-approval-table {
    min-width: 850px;
    width: 100%;
    border-collapse: collapse;
}

.hr-approval-table th {
    background: #f3f1f7;
    color: #30254f;
    padding: 15px;
    text-align: left;
}

.hr-approval-table td {
    white-space: normal !important;
    vertical-align: middle !important;
    padding: 15px !important;
    border-bottom: 1px solid #e5e5ec;
}

.hr-approval-table tbody tr:hover {
    background: #faf9fc;
}

.hr-approval-details {
    background: #f8f7fb;
    border: 1px solid #dedce6;
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 12px;
    overflow-wrap: anywhere;
}

.hr-approval-details label {
    color: #667080;
    margin-bottom: 7px;
}

.hr-approval-warning {
    background: #fff6e5;
    border: 1px solid #ebd5a7;
    border-radius: 8px;
    color: #805500;
    padding: 16px;
    margin: 15px 0;
}

.hr-approval-check {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-top: 12px;
}

.hr-approval-check input {
    width: 18px;
    height: 18px;
    flex-shrink: 0;
}

.hr-approval-actions {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    flex-wrap: wrap;
    gap: 10px;
    margin-top: 25px;
}

.identity-approval-control {
    display: flex;
    flex-direction: column;
    gap: 8px;
    min-width: 160px;
}

.identity-approval-control select {
    padding: 9px;
    border: 1px solid #d8d5e1;
    border-radius: 6px;
    font-size: 13px;
    background: white;
    color: #17191d;
}

.identity-approval-control input {
    padding: 9px;
    font-size: 12px;
}

.identity-approval-control button {
    width: 100%;
}

@media (max-width: 800px) {

    .hr-approval-filters {
        grid-template-columns: 1fr;
    }

    .hr-approval-heading {
        padding: 20px;
    }

}

</style>

"""


# =========================================================
# GET APPROVAL MODULES
# =========================================================

def hr_approval_modules():

    return {

        module: config

        for module, config in MODULES.items()

        if module != "attendance"

        and config.get("statuses")

        and config.get("table")

    }


# =========================================================
# SECURE RECORD LOOKUP
# =========================================================

def hr_approval_record(module, record_id):

    config = hr_approval_modules().get(module)

    if not config:

        abort(404)

    table = config["table"]

    sql = f"""

        SELECT r.*, u.name AS employee_name

        FROM {table} r

        JOIN users u ON u.id = r.user_id

        WHERE r.id = ?

    """

    args = [record_id]

    if table == "employee_records":

        sql += " AND r.module=?"

        args.append(module)

    record = row(sql, args)

    if not record:

        abort(404)

    return config, record


# =========================================================
# CHECK MANAGER / TEAM LEAD APPROVAL
# =========================================================

def hr_waiting_reviewer(module, record):

    routed = globals().get(

        "WORKFLOW_MODULES",

        globals().get("ROUTED", ())

    )

    if module not in routed:

        return None

    if record["status"] not in HR_ACTIVE_STATUSES:

        return None

    helper = globals().get("next_reviewer")

    if not helper:

        return None

    reviewer_id = helper(

        record["user_id"],

        module,

        record["id"]

    )

    if not reviewer_id:

        return None

    reviewer = row(

        "SELECT id,name,status FROM users WHERE id=?",

        (reviewer_id,)

    )

    return {

        "id": reviewer_id,

        "name": (
            reviewer["name"]
            if reviewer
            else "Assigned reviewer"
        )

    }


# =========================================================
# CENTRAL ADMIN APPROVAL MANAGER
# =========================================================

@admin_required
def hr_all_approvals():

    modules = hr_approval_modules()

    module = request.args.get(
        "module",
        "documents"
    )

    if module not in modules:

        abort(404)

    config = modules[module]

    selected_status = request.args.get(
        "status",
        ""
    )

    query = request.args.get(
        "q",
        ""
    ).strip()[:150]

    page_number = max(
        1,
        request.args.get("page", 1, type=int)
    )

    table = config["table"]

    clauses = []

    args = []

    if table == "employee_records":

        clauses.append("r.module=?")

        args.append(module)

    if selected_status:

        if selected_status not in config["statuses"]:

            abort(400)

        clauses.append("r.status=?")

        args.append(selected_status)

    if query:

        search_value = "%" + query + "%"

        clauses.append("""

            (
                u.name LIKE ?
                OR u.employee_code LIKE ?
                OR u.department LIKE ?
            )

        """)

        args.extend(
            [search_value] * 3
        )

    where = (
        " WHERE " + " AND ".join(clauses)
        if clauses
        else ""
    )

    base_query = f"""

        FROM {table} r

        JOIN users u

        ON u.id=r.user_id

    """ + where

    total = count(

        "SELECT COUNT(*) " + base_query,

        args

    )

    limit = 50

    total_pages = max(
        1,
        (total + limit - 1) // limit
    )

    page_number = min(
        page_number,
        total_pages
    )

    records = rows(

        """

        SELECT r.*, u.name AS employee_name,
               u.employee_code

        """

        + base_query

        + " ORDER BY r.id DESC LIMIT ? OFFSET ?",

        args + [

            limit,

            (page_number - 1) * limit

        ]

    )

    results = []

    for item in records:

        details = [

            str(item[f["name"]] or "")

            for f in config["fields"]

            if item[f["name"]] is not None

        ]

        waiting = hr_waiting_reviewer(
            module,
            item
        )

        results.append({

            "id": item["id"],

            "employee": item["employee_name"],

            "employee_code": item["employee_code"],

            "summary": " · ".join(details)[:220],

            "status": item["status"],

            "waiting": waiting["name"] if waiting else ""

        })

    stats = []

    for status in config["statuses"]:

        sql = (

            f"SELECT COUNT(*) FROM {table} "

            "WHERE status=?"

        )

        parameters = [status]

        if table == "employee_records":

            sql += " AND module=?"

            parameters.append(module)

        stats.append({

            "label": status,

            "value": count(sql, parameters)

        })

    return page(

        "Approval Management",

        HR_APPROVAL_STYLE + """

        <div class="hr-approval-heading">

            <h1>HR Approval Management</h1>

            <p>

                Manage and update every employee request
                from one centralized workspace.

            </p>

        </div>


        <div class="card">

            <h2>Approval Administration</h2>

            <div class="inline">

                <a
                    class="btn btn-purple"
                    href="{{ url_for('workforce') }}"
                >

                    Employee Registration Approvals

                </a>

                <a
                    class="btn btn-light"
                    href="{{ url_for('approval_history') }}"
                >

                    Approval History

                </a>

                <a
                    class="btn btn-light"
                    href="{{ url_for('workflow_settings') }}"
                >

                    Approval Hierarchy

                </a>

            </div>

        </div>


        <div class="hr-approval-grid">

            {% for stat in stats %}

                <a
                    class="hr-approval-stat"
                    href="{{ url_for(
                        'approvals',
                        module=module,
                        status=stat.label,
                        q=query
                    ) }}"
                >

                    {{ stat.label }}

                    <strong>{{ stat.value }}</strong>

                </a>

            {% endfor %}

        </div>


        <div class="card">

            <h2>Filter Employee Requests</h2>

            <form method="get">

                <div class="hr-approval-filters">

                    <div>

                        <label>Portal Module</label>

                        <select
                            name="module"
                            onchange="
                                this.form.elements.status.value='';
                                this.form.submit();
                            "
                        >

                            {% for key,c in modules.items() %}

                                <option
                                    value="{{ key }}"
                                    {{ 'selected' if key==module else '' }}
                                >

                                    {{ c.title }}

                                </option>

                            {% endfor %}

                        </select>

                    </div>


                    <div>

                        <label>Approval Status</label>

                        <select name="status">

                            <option value="">All Statuses</option>

                            {% for status in config.statuses %}

                                <option

                                    value="{{ status }}"

                                    {{ 'selected'
                                       if status==selected_status
                                       else '' }}

                                >

                                    {{ status }}

                                </option>

                            {% endfor %}

                        </select>

                    </div>


                    <div>

                        <label>Search Employee</label>

                        <input

                            name="q"

                            value="{{ query }}"

                            placeholder="Name, employee ID or department"

                        >

                    </div>


                    <button class="btn btn-purple">

                        Search

                    </button>

                </div>

            </form>

        </div>


        <div class="card">

            <div class="toolbar">

                <h2>{{ config.title }} — All Requests</h2>

                <span class="muted">

                    {{ total }} matching records

                </span>

            </div>

            <div class="table-wrap">

                <table class="hr-approval-table">

                    <thead>

                        <tr>

                            <th>Employee</th>

                            <th>Reference</th>

                            <th>Request Information</th>

                            <th>Current Status</th>

                            <th>Approval Stage</th>

                            <th>Actions</th>

                        </tr>

                    </thead>

                    <tbody>

                        {% for item in results %}

                            <tr>

                                <td>

                                    <strong>

                                        {{ item.employee }}

                                    </strong>

                                    <br>

                                    <small class="muted">

                                        {{ item.employee_code or '—' }}

                                    </small>

                                </td>

                                <td>#{{ item.id }}</td>

                                <td>{{ item.summary }}</td>

                                <td>{{ badge(item.status) }}</td>

                                <td>

                                    {% if item.waiting %}

                                        Waiting for {{ item.waiting }}

                                    {% elif item.status in active %}

                                        HR Review

                                    {% else %}

                                        Decision Recorded

                                    {% endif %}

                                </td>

                                <td>

                                    <a

                                        class="btn btn-small btn-purple"

                                        href="{{ url_for(
                                            'review_record',
                                            module=module,
                                            record_id=item.id
                                        ) }}"

                                    >

                                        Change Status

                                    </a>

                                </td>

                            </tr>

                        {% else %}

                            <tr>

                                <td colspan="6">

                                    No matching requests found.

                                </td>

                            </tr>

                        {% endfor %}

                    </tbody>

                </table>

            </div>


            <div class="hr-approval-actions">

                {% if page_number > 1 %}

                    <a

                        class="btn btn-light"

                        href="{{ url_for(
                            'approvals',
                            module=module,
                            status=selected_status,
                            q=query,
                            page=page_number-1
                        ) }}"

                    >

                        Previous

                    </a>

                {% endif %}

                <span class="muted">

                    Page {{ page_number }} of {{ total_pages }}

                </span>

                {% if page_number < total_pages %}

                    <a

                        class="btn btn-light"

                        href="{{ url_for(
                            'approvals',
                            module=module,
                            status=selected_status,
                            q=query,
                            page=page_number+1
                        ) }}"

                    >

                        Next

                    </a>

                {% endif %}

            </div>

        </div>

        """,

        modules=modules,

        module=module,

        config=config,

        selected_status=selected_status,

        query=query,

        results=results,

        stats=stats,

        active=HR_ACTIVE_STATUSES,

        total=total,

        page_number=page_number,

        total_pages=total_pages

    )


# =========================================================
# ADMIN APPROVAL EDITOR
# =========================================================

@admin_required
def hr_admin_review(module, record_id):

    config, item = hr_approval_record(
        module,
        record_id
    )

    waiting = hr_waiting_reviewer(
        module,
        item
    )

    # =====================================================
    # SAVE HR DECISION
    # =====================================================

    if request.method == "POST":

        status = field("status", 80)

        remarks = field("remarks", 1000)

        expected_status = field(
            "expected_status",
            80
        )

        if status not in config["statuses"]:

            abort(400)

        if not remarks:

            flash(

                "Enter a reason for this HR decision.",

                "error"

            )

            return redirect(

                url_for(
                    "review_record",
                    module=module,
                    record_id=record_id
                )

            )

        connection = db()

        connection.execute("BEGIN IMMEDIATE")

        try:

            # Re-read inside the transaction.

            config, item = hr_approval_record(

                module,

                record_id

            )

            old_status = item["status"]

            if old_status != expected_status:

                connection.rollback()

                flash(

                    "This request has changed. "
                    "Review its latest status before continuing.",

                    "error"

                )

                return redirect(

                    url_for(

                        "review_record",

                        module=module,

                        record_id=record_id

                    )

                )

            waiting = hr_waiting_reviewer(

                module,

                item

            )

            # Require explicit HR authorization to bypass
            # the currently assigned reviewer.

            override = bool(

                waiting

                and status != old_status

                and status not in HR_ACTIVE_STATUSES

            )

            if override:

                if field("override") != "1":

                    connection.rollback()

                    flash(

                        "Confirm the HR override before "
                        "bypassing the assigned reviewer.",

                        "error"

                    )

                    return redirect(

                        url_for(

                            "review_record",

                            module=module,

                            record_id=record_id

                        )

                    )

            data = {

                "status": status

            }

            if module in ("support", "tickets"):

                if "reply" in request.form:

                    data["reply"] = field("reply", 5000)

            if module == "tickets":

                if "assigned_to" in request.form:

                    data["assigned_to"] = field(
                        "assigned_to",
                        200
                    )

            keys = list(data)

            table = config["table"]

            sql = (

                f"UPDATE {table} SET "

                + ",".join(
                    key + "=?"
                    for key in keys
                )

                + " WHERE id=?"

            )

            args = [

                data[key] for key in keys

            ] + [record_id]

            if table == "employee_records":

                sql += " AND module=?"

                args.append(module)

            connection.execute(sql, args)


            # =============================================
            # RESET APPROVALS WHEN A REQUEST IS REOPENED
            # =============================================

            if (

                status in HR_ACTIVE_STATUSES

                and old_status not in HR_ACTIVE_STATUSES

            ):

                has_delegated_table = connection.execute(

                    "SELECT name FROM sqlite_master "

                    "WHERE type='table' "

                    "AND name='delegated_approvals'"

                ).fetchone()

                if has_delegated_table:

                    connection.execute("""

                        DELETE FROM delegated_approvals

                        WHERE module=?

                        AND record_id=?

                    """, (

                        module,

                        record_id

                    ))


            # =============================================
            # SAVE COMPLETE APPROVAL HISTORY
            # =============================================

            history_remark = (

                f"{old_status} -> {status}. "

                + remarks

            )

            if override:

                history_remark = (

                    "HR OVERRIDE: "

                    f"Bypassed reviewer #{waiting['id']}. "

                    + history_remark

                )

            connection.execute("""

                INSERT INTO approval_history

                (

                    module,

                    record_id,

                    employee_id,

                    decision,

                    decided_by,

                    remarks,

                    created_at

                )

                VALUES (?,?,?,?,?,?,?)

            """, (

                module,

                record_id,

                item["user_id"],

                status,

                g.user["id"],

                history_remark,

                stamp()

            ))


            # =============================================
            # HR AUDIT TRAIL
            # =============================================

            connection.execute("""

                INSERT INTO audit_logs

                (user_id, action, created_at)

                VALUES (?,?,?)

            """, (

                g.user["id"],

                f"HR changed {module} #{record_id}: "

                + history_remark,

                stamp()

            ))

            connection.commit()

        except Exception:

            connection.rollback()

            raise

        flash(

            "HR decision updated successfully."

        )


        # =============================================
        # RETURN TO THE EMPLOYEE PROFILE IF NEEDED
        # =============================================

        destination = field("return_to", 50)

        if destination == "employee_profile":

            return redirect(

                url_for(

                    "hr_employee_profile",

                    uid=item["user_id"]

                )

            )

        if destination == "my_profile":

            if item["user_id"] == g.user["id"]:

                return redirect(url_for("profile"))

        return redirect(

            url_for(

                "approvals",

                module=module

            )

        )


    # =====================================================
    # APPROVAL HISTORY FOR THIS RECORD
    # =====================================================

    history = rows("""

        SELECT h.*,u.name AS reviewer

        FROM approval_history h

        LEFT JOIN users u

        ON u.id=h.decided_by

        WHERE h.module=?

        AND h.record_id=?

        ORDER BY h.id DESC

    """, (

        module,

        record_id

    ))


    # =====================================================
    # HR DECISION EDITOR INTERFACE
    # =====================================================

    return page(

        "HR Decision",

        HR_APPROVAL_STYLE + """

        <div class="hr-approval-heading">

            <h1>Manage HR Decision</h1>

            <p>

                {{ config.title }}

                — Request #{{ item.id }}

            </p>

        </div>

        <div class="two-columns">

            <div class="card">

                <h2>Employee Information</h2>

                <div class="hr-approval-details">

                    <label>Employee</label>

                    <strong>{{ item.employee_name }}</strong>

                </div>

                <div class="hr-approval-details">

                    <label>Current Approval Status</label>

                    {{ badge(item.status) }}

                </div>

                {% for f in config.fields %}

                    <div class="hr-approval-details">

                        <label>{{ f.label }}</label>

                        <strong>

                            {{ item[f.name] or '—' }}

                        </strong>

                    </div>

                {% endfor %}


                {% if module=='documents' %}

                    <a

                        class="btn btn-purple"

                        href="{{ url_for(
                            'download_document',
                            record_id=item.id
                        ) }}"

                    >

                        Download Document Copy

                    </a>

                {% endif %}

            </div>


            <div class="card">

                <h2>Update Approval Status</h2>

                <form method="post">

                    {{ token() }}

                    <input

                        type="hidden"

                        name="expected_status"

                        value="{{ item.status }}"

                    >


                    <div class="form-group">

                        <label>New Approval Status *</label>

                        <select name="status" required>

                            {% for status in config.statuses %}

                                <option

                                    value="{{ status }}"

                                    {{ 'selected'
                                       if item.status==status
                                       else '' }}

                                >

                                    {{ status }}

                                </option>

                            {% endfor %}

                        </select>

                    </div>


                    {% if module=='tickets' %}

                        <div class="form-group">

                            <label>Assigned Department / Officer</label>

                            <input

                                name="assigned_to"

                                value="{{ item.assigned_to or '' }}"

                            >

                        </div>

                    {% endif %}


                    {% if module in ['support','tickets'] %}

                        <div class="form-group">

                            <label>HR Response to Employee</label>

                            <textarea name="reply">{{ item.reply or '' }}</textarea>

                        </div>

                    {% endif %}


                    <div class="form-group">

                        <label>Reason / HR Remarks *</label>

                        <textarea

                            name="remarks"

                            maxlength="1000"

                            placeholder="Enter the reason for this decision"

                            required

                        >{{ latest_hr_remark(module,item.id) }}</textarea>

                        <small class="muted">

                            Recorded in the approval history.

                        </small>

                    </div>


                    {% if waiting %}

                        <div class="hr-approval-warning">

                            <strong>

                                Outstanding Delegated Approval

                            </strong>

                            <p>

                                This request is waiting for

                                {{ waiting.name }}.

                            </p>

                            <label class="hr-approval-check">

                                <input

                                    type="checkbox"

                                    name="override"

                                    value="1"

                                >

                                Authorize HR override

                            </label>

                        </div>

                    {% endif %}


                    <div class="hr-approval-actions">

                        <a

                            class="btn btn-light"

                            href="{{ url_for(
                                'approvals',
                                module=module
                            ) }}"

                        >

                            Cancel

                        </a>

                        <button

                            class="btn btn-purple"

                            type="submit"

                        >

                            Save HR Decision

                        </button>

                    </div>

                </form>

            </div>

        </div>


        <div class="card">

            <h2>Previous Approval Decisions</h2>

            <div class="table-wrap">

                <table class="hr-approval-table">

                    <thead>

                        <tr>

                            <th>Date</th>

                            <th>Decision</th>

                            <th>Reviewed By</th>

                            <th>Remarks</th>

                        </tr>

                    </thead>

                    <tbody>

                        {% for h in history %}

                            <tr>

                                <td>{{ h.created_at }}</td>

                                <td>{{ badge(h.decision) }}</td>

                                <td>{{ h.reviewer or '—' }}</td>

                                <td>{{ h.remarks or '—' }}</td>

                            </tr>

                        {% else %}

                            <tr>

                                <td colspan="4">

                                    No previous decisions.

                                </td>

                            </tr>

                        {% endfor %}

                    </tbody>

                </table>

            </div>

        </div>

        """,

        config=config,

        item=item,

        module=module,

        waiting=waiting,

        history=history

    )


# =========================================================
# REPLACE EXISTING HR APPROVAL ENDPOINTS
# =========================================================

app.view_functions["approvals"] = hr_all_approvals

app.view_functions["review_record"] = hr_admin_review


# =========================================================
# EDITABLE HR VERIFICATION IN EMPLOYEE PROFILE
# =========================================================

old_verification = "{{ badge(doc.status) }}"

new_verification = r"""

{% if admin() and doc.id and doc.available %}

    <form

        method="post"

        class="identity-approval-control"

        action="{{ url_for(
            'review_record',
            module='documents',
            record_id=doc.id
        ) }}"

    >

        {{ token() }}

        <input

            type="hidden"

            name="expected_status"

            value="{{ doc.status }}"

        >

        <input

            type="hidden"

            name="return_to"

            value="{{ 'my_profile'
                       if identity_own
                       else 'employee_profile' }}"

        >

        <select

            name="status"

            aria-label="Document verification status"

            required

        >

            {% for status in [
                'Pending',
                'Verified',
                'Rejected'
            ] %}

                <option

                    value="{{ status }}"

                    {{ 'selected'
                       if doc.status==status
                       else '' }}

                >

                    {{ status }}

                </option>

            {% endfor %}

        </select>

        <input

            name="remarks"

            maxlength="500"

            
placeholder="Verification remarks"
value="{{ latest_hr_remark('documents',doc.id) }}"


            aria-label="Verification remarks"

            required

        >

        <button

            class="btn btn-small btn-purple"

            type="submit"

        >

            Save Decision

        </button>

    </form>

{% else %}

    {{ badge(doc.status) }}

{% endif %}

"""

if IDENTITY_PROFILE_PANEL.count(old_verification) != 1:

    raise RuntimeError(
        "HR verification column could not be located."
    )

IDENTITY_PROFILE_PANEL = (

    HR_APPROVAL_STYLE

    + IDENTITY_PROFILE_PANEL.replace(

        old_verification,

        new_verification,

        1

    )

)


# =========================================================
# CENTRAL APPROVAL ACCESS FROM ALL HR DASHBOARDS
# =========================================================

_previous_hr_approval_page = page


def page(title, content, **context):

    if (

        g.user

        and admin()

        and request.method == "GET"

        and request.endpoint in (

            "dashboard",

            "team_approvals",

            "approval_history",

            "approved",

            "analytics",

            "workforce",

            "module_page"

        )

    ):

        content = """

        <div style="

            display:flex;

            justify-content:flex-end;

            flex-wrap:wrap;

            gap:10px;

            margin-bottom:16px;

        ">

            <a

                class="btn btn-purple"

                href="{{ url_for('approvals') }}"

            >

                Manage All Approvals

            </a>

        </div>

        """ + content

    return _previous_hr_approval_page(

        title,

        content,

        **context

    )


# =========================================================
# ADMIN PORTAL NAVIGATION
# =========================================================

_previous_hr_approval_menu = visible_menu


def visible_menu():

    menu = _previous_hr_approval_menu()

    if not g.user or not admin():

        return menu

    if g.user["must_change_password"]:

        return menu

    if profile_required(g.user):

        return menu

    menu.append({

        "name": "HR Approval Management",

        "items": [

            {

                "label": "Manage All Approvals",

                "link": url_for("approvals"),

                "icon": "check",

                "color": "green"

            },

            {

                "label": "Employee Registrations",

                "link": url_for("workforce"),

                "icon": "users",

                "color": "purple"

            },

            {

                "label": "Approval History",

                "link": url_for("approval_history"),

                "icon": "clock",

                "color": "teal"

            }

        ]

    })

    return menu





# STAR LINK ACCOUNT MANAGEMENT V4

import re


# =========================================================
# FETCH THE LAST SAVED HR VERIFICATION REMARK
# =========================================================

def latest_hr_remark(module, record_id):

    # Confidential remarks are available only to HR.

    if not g.user or not admin():
        return ""

    item = row("""

        SELECT h.remarks

        FROM approval_history h

        JOIN users u
        ON u.id=h.decided_by

        WHERE h.module=?

        AND h.record_id=?

        AND u.role='Admin'

        AND h.remarks IS NOT NULL

        AND trim(h.remarks)!=''

        ORDER BY h.id DESC

        LIMIT 1

    """, (module, record_id))

    if not item:
        return ""

    remarks = item["remarks"]

    # Remove the automatically generated status prefix
    # while retaining the administrator's actual remarks.

    remarks = re.sub(

        r"^HR OVERRIDE: Bypassed reviewer #\d+\. ",

        "",

        remarks

    )

    remarks = re.sub(

        r"^[^.\n]+ -> [^.\n]+\. ",

        "",

        remarks,

        count=1

    )

    return remarks


app.jinja_env.globals.update(
    latest_hr_remark=latest_hr_remark
)


# =========================================================
# CHECK EXISTING ONBOARDING DOCUMENTS
# =========================================================

def remaining_onboarding_documents(user):

    # Reuse the existing HR-configured requirements.

    helper = (
        globals().get("missing_documents")
        or globals().get("missing_docs")
    )

    if not helper:
        raise RuntimeError(
            "Existing document validation function not found."
        )

    return list(helper(user))


def account_has_profile_picture(uid):

    return bool(row("""

        SELECT user_id

        FROM profile_photos

        WHERE user_id=?

        AND length(jpeg)>0

    """, (uid,)))


# =========================================================
# REMOVE DUPLICATE FIRST-TIME ONBOARDING
# =========================================================

_previous_first_onboarding = app.view_functions[
    "first_onboarding"
]


@login_required
def streamlined_onboarding():

    user = g.user

    uid = user["id"]

    if admin():

        return redirect(
            url_for("dashboard")
        )

    # Users who already completed onboarding do not
    # have to repeat it when editing their information.

    if user["onboarded_at"]:

        return redirect(
            url_for("profile_details")
        )

    # Preserve the mandatory employment profile.

    if profile_required(user):

        return redirect(
            url_for("profile_details")
        )

    missing = remaining_onboarding_documents(user)

    has_photo = account_has_profile_picture(uid)

    # All required documents already exist.
    # Complete onboarding automatically.

    if not missing and has_photo:

        connection = db()

        try:

            connection.execute("""

                UPDATE users

                SET onboarded_at=?

                WHERE id=?

            """, (stamp(), uid))

            connection.execute("""

                INSERT INTO audit_logs

                (user_id,action,created_at)

                VALUES (?,?,?)

            """, (

                uid,

                "Completed employee onboarding",

                stamp()

            ))

            connection.commit()

        except Exception:

            connection.rollback()

            raise

        flash(
            "Profile and documents completed successfully."
        )

        return redirect(
            url_for("dashboard")
        )

    # Continue using the existing validated upload
    # handler for any additional documents HR requires.

    if request.method == "POST":

        return _previous_first_onboarding()

    # Show ONLY outstanding documents.
    # Never display another upload form for saved documents.

    return page(

        "Complete Onboarding",

        """

        <div class="card">

            <h1>Complete Your Onboarding</h1>

            <p class="muted">

                Your employment information and previously
                uploaded documents have been saved.

                Complete only the remaining requirements below.

            </p>

        </div>

        {% if not has_photo %}

            <div class="card">

                <h2>Profile Photograph</h2>

                <form
                    method="post"
                    enctype="multipart/form-data"
                >

                    {{ token() }}

                    <input
                        type="hidden"
                        name="action"
                        value="photo"
                    >

                    <input
                        type="file"
                        name="photo"
                        accept="image/jpeg,image/png"
                        required
                    >

                    <br><br>

                    <button class="btn btn-purple">

                        Save Profile Photograph

                    </button>

                </form>

            </div>

        {% endif %}


        {% for category in missing %}

            <div class="saved-document-card">

                <h2>{{ category }}</h2>

                <form

                    method="post"

                    enctype="multipart/form-data"

                >

                    {{ token() }}

                    <input

                        type="hidden"

                        name="action"

                        value="document"

                    >

                    <input

                        type="hidden"

                        name="category"

                        value="{{ category }}"

                    >

                    <div class="identity-upload-grid">

                        <div>

                            <label>Document Copy *</label>

                            <input

                                type="file"

                                name="document"

                                accept=".pdf,.jpg,.jpeg,.png"

                                required

                            >

                        </div>

                        <div>

                            <label>Expiry Date *</label>

                            <input

                                type="date"

                                name="expiry"

                                min="{{ current_day }}"

                                required

                            >

                        </div>

                    </div>

                    <br>

                    <button class="btn btn-purple">

                        Submit Document

                    </button>

                </form>

            </div>

        {% endfor %}

        """,

        missing=missing,

        has_photo=has_photo,

        current_day=today()

    )


app.view_functions[
    "first_onboarding"
] = streamlined_onboarding


# =========================================================
# HR ONLY - RESET ANY ACCOUNT PASSWORD
# =========================================================

@app.route(

    "/hr/reset-account-password/<int:uid>",

    methods=["GET", "POST"]

)
@admin_required
def hr_reset_account_password(uid):

    employee = row("""

        SELECT id,name,email,employee_code,role

        FROM users

        WHERE id=?

    """, (uid,))

    if not employee:

        abort(404)

    errors = []

    if request.method == "POST":

        password = request.form.get(
            "new_password", ""
        )

        confirm = request.form.get(
            "confirm_password", ""
        )

        force_change = int(

            request.form.get(
                "force_change"
            ) == "1"

        )

        # HR does not need the employee's current password.

        if not 12 <= len(password) <= 128:

            errors.append(

                "The new password must contain "
                "between 12 and 128 characters."

            )

        if password != confirm:

            errors.append(

                "The passwords do not match."

            )

        if not errors:

            connection = db()

            try:

                connection.execute(
                    "BEGIN IMMEDIATE"
                )

                connection.execute("""

                    UPDATE users

                    SET password=?,

                        must_change_password=?

                    WHERE id=?

                """, (

                    generate_password_hash(password),

                    force_change,

                    uid

                ))

                # Store the action but NEVER the password.

                connection.execute("""

                    INSERT INTO audit_logs

                    (user_id,action,created_at)

                    VALUES (?,?,?)

                """, (

                    g.user["id"],

                    f"HR reset password for account #{uid}",

                    stamp()

                ))

                connection.commit()

            except Exception:

                connection.rollback()

                raise

            # If HR changes their own password,
            # require a new login.

            if uid == g.user["id"]:

                session.clear()

                flash(

                    "Your password has been updated. "
                    "Sign in using your new password."

                )

                return redirect(
                    url_for("login")
                )

            flash(

                "Employee password reset successfully. "
                "Share the temporary password securely."

            )

            return redirect(

                url_for(
                    "hr_employee_profile",
                    uid=uid
                )

            )

    return page(

        "Reset Account Password",

        """

        <div class="card account-reset-card">

            <h1>Reset Employee Password</h1>

            <p class="muted">

                HR administrator access only.
                The employee's current password is not required.

            </p>

            <div class="account-reset-details">

                <strong>{{ employee.name }}</strong>

                <p>{{ employee.email }}</p>

                <p>
                    Employee ID:
                    {{ employee.employee_code or '—' }}
                </p>

            </div>

            {% for error in errors %}

                <p class="account-reset-error">

                    {{ error }}

                </p>

            {% endfor %}


            <form method="post">

                {{ token() }}

                <div class="form-group">

                    <label>New Password *</label>

                    <input

                        type="password"

                        name="new_password"

                        minlength="12"

                        maxlength="128"

                        autocomplete="new-password"

                        required

                    >

                </div>


                <div class="form-group">

                    <label>Confirm New Password *</label>

                    <input

                        type="password"

                        name="confirm_password"

                        minlength="12"

                        maxlength="128"

                        autocomplete="new-password"

                        required

                    >

                </div>


                <label class="account-reset-checkbox">

                    <input

                        type="checkbox"

                        name="force_change"

                        value="1"

                        checked

                    >

                    Require a password change at next login

                </label>


                <div class="account-reset-notice">

                    Resetting this password will invalidate
                    the account's previous password and
                    existing login sessions.

                </div>


                <div class="inline">

                    <button

                        class="btn btn-purple"

                        type="submit"

                    >

                        Reset Password

                    </button>

                    <a

                        class="btn btn-light"

                        href="{{ url_for(
                            'hr_employee_profile',
                            uid=employee.id
                        ) }}"

                    >

                        Cancel

                    </a>

                </div>

            </form>

        </div>

        """,

        employee=employee,

        errors=errors

    )


# =========================================================
# ADD PASSWORD MANAGEMENT TO HR EMPLOYEE PROFILES
# =========================================================

_account_previous_page = page


def page(title, content, **context):

    if (

        g.user

        and admin()

        and request.method == "GET"

        and request.endpoint in (

            "hr_employee_profile",

            "profile"

        )

    ):

        target_id = (

            request.view_args["uid"]

            if request.endpoint == "hr_employee_profile"

            else g.user["id"]

        )

        content += """

        <div class="card">

            <h2>Account & Login Management</h2>

            <p class="muted">

                Every employee has a separate account
                connected to their registered email address.

            </p>

            <a

                class="btn btn-purple"

                href="{{ url_for(
                    'hr_reset_account_password',
                    uid=reset_account_uid
                ) }}"

            >

                Reset Account Password

            </a>

        </div>

        """

        context["reset_account_uid"] = target_id

    return _account_previous_page(

        title,

        content,

        **context

    )





# STAR LINK DARK THEME V1

@app.post("/personal-theme")
@login_required
def sl_save_theme():

    theme = request.form.get("theme", "").strip()

    if theme not in ("Light", "Dark"):

        abort(400)

    # Only update the signed-in employee's theme.
    # All other preferences remain unchanged.

    save("""

        INSERT INTO preferences

        (user_id, theme)

        VALUES (?,?)

        ON CONFLICT(user_id)

        DO UPDATE SET

            theme=excluded.theme

    """, (

        g.user["id"],

        theme

    ))

    return {

        "theme": theme

    }




# STAR LINK PROFESSIONAL DESIGN V1

SL_PROFESSIONAL_STYLE = '\n\n<style>\n\n/* GLOBAL ALIGNMENT */\n\nhtml body * {\n    box-sizing: border-box;\n}\n\nhtml body .card {\n    min-width: 0;\n    border-radius: 10px;\n}\n\nhtml body .toolbar,\nhtml body .page-heading {\n    display: flex;\n    align-items: center;\n    justify-content: space-between;\n    flex-wrap: wrap;\n    gap: 14px;\n}\n\nhtml body .toolbar h2 {\n    margin: 0;\n}\n\nhtml body .form-grid,\nhtml body .two-columns,\nhtml body .mp-form-grid,\nhtml body .identity-upload-grid {\n    grid-template-columns: repeat(2, minmax(0, 1fr));\n    gap: 20px;\n}\n\nhtml body .form-group {\n    min-width: 0;\n}\n\nhtml body input,\nhtml body select,\nhtml body textarea {\n    max-width: 100%;\n}\n\nhtml body label {\n    line-height: 1.5;\n}\n\n\n/* =====================================================\n   GLOBAL TABLE DESIGN\n   ===================================================== */\n\nhtml body .table-wrap {\n    width: 100%;\n    max-width: 100%;\n    overflow-x: auto;\n    border-radius: 8px;\n    -webkit-overflow-scrolling: touch;\n}\n\nhtml body table {\n    width: 100%;\n    border-collapse: collapse;\n    border-spacing: 0;\n    table-layout: auto;\n    font-size: 13px;\n}\n\nhtml body .table-wrap > table {\n    min-width: 640px;\n}\n\n\n/* TABLE HEADER */\n\nhtml body table th {\n    background: #f3f1f7;\n    color: #30254f;\n    font-size: 13px;\n    font-weight: 700;\n    padding: 14px 16px !important;\n    text-align: left;\n    vertical-align: middle !important;\n    white-space: nowrap;\n    border-bottom: 1px solid #dedce6;\n}\n\n\n/* TABLE CELLS */\n\nhtml body table td {\n    padding: 15px 16px !important;\n    text-align: left;\n    vertical-align: top !important;\n    white-space: normal !important;\n    overflow-wrap: anywhere;\n    line-height: 1.5 !important;\n    height: auto !important;\n    color: #17191d;\n    border-bottom: 1px solid #e7e8ee;\n}\n\n\n/* REMOVE EXCESSIVE EMPTY SPACES */\n\nhtml body table td.prewrap,\nhtml body table td .prewrap {\n    white-space: normal !important;\n    overflow-wrap: anywhere;\n}\n\nhtml body table td > div {\n    white-space: normal !important;\n}\n\nhtml body table td p {\n    margin: 0 0 6px !important;\n}\n\nhtml body table td small {\n    display: block;\n    margin-top: 4px;\n    line-height: 1.4;\n    white-space: normal !important;\n}\n\n\n/* COMPACT TABLE ROWS */\n\nhtml body table tbody tr {\n    height: auto !important;\n    min-height: 0 !important;\n    background: #ffffff;\n}\n\nhtml body table tbody tr:nth-child(even) {\n    background: #f9f8fb;\n}\n\nhtml body table tbody tr:hover {\n    background: #f2f0f7;\n}\n\n\n/* EMPLOYEE NAME COLUMN */\n\nhtml body table td:first-child {\n    font-weight: 550;\n}\n\n\n/* =====================================================\n   TABLE ACTION BUTTONS\n   ===================================================== */\n\nhtml body table td .inline {\n\n    display: flex !important;\n\n    flex-direction: row !important;\n\n    align-items: center !important;\n\n    justify-content: flex-start !important;\n\n    flex-wrap: wrap;\n\n    gap: 7px !important;\n\n    margin: 0 !important;\n\n}\n\nhtml body table td form {\n    margin: 0;\n}\n\nhtml body table td .btn {\n    min-height: 34px;\n    padding: 8px 12px;\n    font-size: 12px;\n    line-height: 1.4;\n    border-radius: 6px;\n    white-space: nowrap;\n    margin: 0;\n}\n\nhtml body table td .badge {\n    display: inline-block;\n    padding: 6px 11px;\n    font-size: 12px;\n    line-height: 1.4;\n    white-space: nowrap;\n    border-radius: 18px;\n}\n\n\n/* =====================================================\n   EMPLOYEE PERMISSION TABLE\n   ===================================================== */\n\nhtml body table.permission-table th:not(:first-child),\nhtml body table.permission-table td:not(:first-child) {\n    text-align: center;\n    vertical-align: middle !important;\n}\n\nhtml body table.permission-table td:first-child {\n    vertical-align: middle !important;\n}\n\nhtml body table.permission-table input[type=checkbox] {\n    width: 18px;\n    height: 18px;\n    margin: 0;\n    cursor: pointer;\n}\n\n\n/* =====================================================\n   ATTENDANCE PHOTO TABLE\n   ===================================================== */\n\nhtml body table.attendance-table {\n    min-width: 740px;\n    table-layout: auto;\n}\n\nhtml body table.attendance-table td img {\n    width: 145px !important;\n    height: 100px !important;\n    max-width: 100%;\n    object-fit: cover;\n    border-radius: 8px;\n    display: block;\n    margin: 7px 0 5px !important;\n}\n\nhtml body table.attendance-table td:nth-child(3),\nhtml body table.attendance-table td:nth-child(4) {\n    font-variant-numeric: tabular-nums;\n}\n\nhtml body table.attendance-table td small {\n    font-size: 11px;\n    margin-top: 4px;\n}\n\n\n/* =====================================================\n   DOCUMENT MANAGEMENT TABLE\n   ===================================================== */\n\n.sl-document-section {\n    margin-top: 28px;\n    padding-top: 24px;\n    border-top: 1px solid #dedce6;\n}\n\n.sl-document-section h2 {\n    margin: 0 0 10px;\n}\n\n.sl-document-section > p {\n    margin-bottom: 20px;\n}\n\nhtml body table.sl-document-table {\n    min-width: 820px;\n}\n\nhtml body table.sl-document-table td {\n    vertical-align: middle !important;\n}\n\n.sl-document-name {\n    font-weight: 700;\n    font-size: 14px;\n}\n\n.sl-document-upload {\n    display: grid;\n    grid-template-columns: minmax(0, 1fr);\n    gap: 10px;\n    min-width: 235px;\n}\n\n.sl-document-upload label {\n    display: block;\n    margin-bottom: 5px;\n    font-size: 12px;\n    font-weight: 650;\n}\n\n.sl-document-upload input {\n    width: 100%;\n    border: 1px solid #d8d8e2;\n    border-radius: 7px;\n    padding: 9px;\n    font-size: 12px;\n    min-height: 40px;\n    background: #ffffff;\n}\n\n.sl-document-upload input::file-selector-button {\n    background: #30254f;\n    color: white;\n    border: 0;\n    border-radius: 5px;\n    padding: 8px 10px;\n    margin-right: 8px;\n    cursor: pointer;\n}\n\n.sl-document-renewal {\n    margin: 0;\n}\n\n.sl-document-renewal summary {\n    display: inline-block;\n    background: #f0edf7;\n    color: #30254f;\n    border: 1px solid #ded6ed;\n    border-radius: 6px;\n    font-size: 12px;\n    font-weight: 650;\n    padding: 8px 12px;\n    cursor: pointer;\n}\n\n.sl-document-renewal[open] summary {\n    margin-bottom: 12px;\n}\n\n.sl-document-renewal summary::-webkit-details-marker {\n    display: none;\n}\n\n.sl-document-note {\n    background: #f3f0f9;\n    border: 1px solid #ded6ed;\n    border-radius: 8px;\n    padding: 14px;\n    margin-top: 18px;\n    color: #30254f;\n    line-height: 1.5;\n}\n\n\n/* =====================================================\n   DARK MODE TABLE ALIGNMENT\n   ===================================================== */\n\nhtml body.dark table {\n    background: #211f2c !important;\n    color: #f1eef7 !important;\n}\n\nhtml body.dark table th {\n    background: #302b40 !important;\n    color: #f1eef7 !important;\n    border-color: #3b374a !important;\n}\n\nhtml body.dark table td {\n    color: #f1eef7 !important;\n    border-color: #3b374a !important;\n}\n\nhtml body.dark table tbody tr {\n    background: #211f2c !important;\n}\n\nhtml body.dark table tbody tr:nth-child(even) {\n    background: #272433 !important;\n}\n\nhtml body.dark table tbody tr:hover {\n    background: #302b40 !important;\n}\n\nhtml body.dark .sl-document-section {\n    border-color: #3b374a;\n}\n\nhtml body.dark .sl-document-note {\n    background: #30283f;\n    color: #e0d4f5;\n    border-color: #49405d;\n}\n\nhtml body.dark .sl-document-renewal summary {\n    background: #353044;\n    color: #f1eef7;\n    border-color: #494358;\n}\n\nhtml body.dark .sl-document-upload input {\n    background: #191823;\n    color: #f1eef7;\n    border-color: #494358;\n}\n\n\n/* =====================================================\n   MOBILE RESPONSIVENESS\n   ===================================================== */\n\n@media (max-width: 768px) {\n\n    html body table th,\n    html body table td {\n        padding: 12px !important;\n        font-size: 12px;\n    }\n\n    html body table td .btn {\n        font-size: 11px;\n        padding: 7px 10px;\n    }\n\n    html body table.attendance-table td img {\n        width: 110px !important;\n        height: 80px !important;\n    }\n\n    html body .toolbar {\n        align-items: stretch;\n    }\n\n    html body .toolbar input {\n        width: 100%;\n    }\n\n}\n\n@media (max-width: 650px) {\n\n    html body .form-grid,\n    html body .two-columns,\n    html body .mp-form-grid,\n    html body .identity-upload-grid {\n        grid-template-columns: 1fr;\n    }\n\n    html body .card {\n        padding: 16px;\n    }\n\n    html body .page-heading {\n        align-items: flex-start;\n    }\n\n}\n\n</style>\n\n'


# Apply the styling to the complete shared website layout.

if "</head>" not in LAYOUT:
    raise RuntimeError("Website layout head was not found.")

LAYOUT = LAYOUT.replace(
    "</head>",
    SL_PROFESSIONAL_STYLE + "</head>",
    1
)




# STAR LINK LINE MANAGER APPROVAL V1

_previous_manager_init = init_db


def init_db():

    _previous_manager_init()

    connection = db()

    connection.execute("BEGIN IMMEDIATE")

    try:

        columns = {

            item[1]

            for item in connection.execute(

                "PRAGMA table_info(approval_routes)"

            )

        }

        if "hr_final" not in columns:

            connection.execute("""

                ALTER TABLE approval_routes

                ADD COLUMN hr_final INTEGER

                NOT NULL DEFAULT 0

            """)

        connection.commit()

    except Exception:

        connection.rollback()

        raise


# =========================================================
# DETERMINE WHETHER HR FINAL APPROVAL IS REQUIRED
# =========================================================

def workflow_hr_final(uid, module):

    configuration = row("""

        SELECT hr_final

        FROM approval_routes

        WHERE user_id=?

        AND module=?

    """, (

        uid,

        module

    ))

    return bool(

        configuration and configuration["hr_final"]

    )


# =========================================================
# SUPPORT FINAL MANAGER APPROVAL IN EVERY ROUTED MODULE
# =========================================================

for workflow_module in WORKFLOW_MODULES:

    config = MODULES[workflow_module]

    statuses = list(

        config.get("statuses") or []

    )

    if "Approved" not in statuses:

        statuses.append("Approved")

    config["statuses"] = statuses


# =========================================================
# DISPLAY THE CORRECT APPROVAL STAGE
# =========================================================

def approval_stage(uid, module, record_id):

    record = routed_record(module, record_id)

    if record["status"] == "Approved":

        return "Final approval completed"

    if record["status"] == "Rejected":

        return "Request rejected"

    reviewer_id = next_reviewer(

        uid,

        module,

        record_id

    )

    if reviewer_id:

        reviewer = row("""

            SELECT name,status

            FROM users

            WHERE id=?

        """, (reviewer_id,))

        if not reviewer or reviewer["status"] != "Approved":

            return "Waiting for HR to reassign reviewer"

        return "Awaiting " + reviewer["name"]

    # Unassigned requests, explicitly configured HR reviews
    # and older unfinished requests remain with HR.

    return "Awaiting HR approval"


app.jinja_env.globals.update(

    approval_stage=approval_stage

)





# STAR LINK MULTIPLE APPROVERS V1

SL_APPROVAL_MODULES = (

    "timesheets",

    "extra-work",

    "leaves",

    "support",

    "tickets"

)

SL_ACTIVE_STATES = (

    "Pending",

    "Open",

    "In Progress"

)


# =========================================================
# DATABASE MIGRATION
# =========================================================

_sl_previous_init = init_db


def init_db():

    _sl_previous_init()

    connection = db()

    connection.execute("BEGIN IMMEDIATE")

    try:

        # HR-controlled eligible approvers.

        connection.execute("""

            CREATE TABLE IF NOT EXISTS
            sl_eligible_approvers (

                user_id INTEGER NOT NULL,

                module TEXT NOT NULL,

                reviewer_id INTEGER NOT NULL,

                PRIMARY KEY(
                    user_id,
                    module,
                    reviewer_id
                )

            )

        """)

        # Individual approval sequence for each request.

        connection.execute("""

            CREATE TABLE IF NOT EXISTS
            sl_request_approvers (

                module TEXT NOT NULL,

                record_id INTEGER NOT NULL,

                reviewer_id INTEGER NOT NULL,

                position INTEGER NOT NULL,

                PRIMARY KEY(
                    module,
                    record_id,
                    reviewer_id
                ),

                UNIQUE(
                    module,
                    record_id,
                    position
                )

            )

        """)

        connection.execute("""

            CREATE INDEX IF NOT EXISTS
            sl_request_reviewer_index

            ON sl_request_approvers(reviewer_id)

        """)

        # Remove routing records when a request is deleted.
        # Reset previous decisions when request details change
        # using the existing workflow's approval reset triggers.

        for module in SL_APPROVAL_MODULES:

            table = MODULES[module]["table"]

            name = module.replace("-", "_")

            predicate = (

                f" WHEN OLD.module='{module}'"

                if table == "employee_records"

                else ""

            )

            connection.execute(f"""

                CREATE TRIGGER IF NOT EXISTS
                sl_remove_reviewers_{name}

                AFTER DELETE ON {table}

                {predicate}

                BEGIN

                    DELETE FROM sl_request_approvers

                    WHERE module='{module}'

                    AND record_id=OLD.id;

                END

            """)

        connection.commit()

    except Exception:

        connection.rollback()

        raise


# =========================================================
# EXISTING DEFAULT APPROVAL ROUTE
# =========================================================

def sl_default_chain(uid, module):

    route = row("""

        SELECT lead_id, manager_id

        FROM approval_routes

        WHERE user_id=?

        AND module=?

    """, (uid, module))

    if not route:
        return []

    return list(dict.fromkeys(

        reviewer

        for reviewer in (

            route["lead_id"],

            route["manager_id"]

        )

        if reviewer and reviewer != uid

    ))


# =========================================================
# HR-AUTHORIZED APPROVER OPTIONS
# =========================================================

def sl_eligible_people(uid, module):

    return rows("""

        SELECT u.id, u.name,
               u.designation, u.department

        FROM users u

        WHERE u.status='Approved'

        AND u.role='Employee'

        AND u.id!=?

        AND (

            EXISTS (

                SELECT 1

                FROM sl_eligible_approvers a

                WHERE a.user_id=?

                AND a.module=?

                AND a.reviewer_id=u.id

            )

            OR EXISTS (

                SELECT 1

                FROM approval_routes r

                WHERE r.user_id=?

                AND r.module=?

                AND (

                    r.lead_id=u.id

                    OR r.manager_id=u.id

                )

            )

        )

        ORDER BY u.name

    """, (

        uid,

        uid,

        module,

        uid,

        module

    ))


def sl_form_approvers(module):

    if not g.user:
        return []

    if module not in SL_APPROVAL_MODULES:
        return []

    if admin():

        # HR can select any active employee as a reviewer.

        return rows("""

            SELECT id,name,designation,department

            FROM users

            WHERE status='Approved'

            AND role='Employee'

            ORDER BY name

        """)

    return sl_eligible_people(
        g.user["id"],
        module
    )


# =========================================================
# CREATE REQUEST AND SAVE SELECTED APPROVERS
# IN ONE DATABASE TRANSACTION
# =========================================================

def sl_insert_request(module, table, data):

    if module not in SL_APPROVAL_MODULES:

        return insert(table, data)

    uid = data["user_id"]

    submitted = [

        value

        for value in request.form.getlist("sl_reviewer")

        if value

    ]

    if len(submitted) > 10:

        abort(400)

    try:

        selected = [

            int(value)

            for value in submitted

        ]

    except (TypeError, ValueError):

        abort(400)

    if len(selected) != len(set(selected)):

        abort(400)

    if uid in selected:

        abort(400)

    connection = db()

    try:

        connection.execute("BEGIN IMMEDIATE")

        if admin():

            allowed_ids = {

                item["id"]

                for item in sl_form_approvers(module)

                if item["id"] != uid

            }

        else:

            if uid != g.user["id"]:

                abort(403)

            allowed_ids = {

                item["id"]

                for item in sl_eligible_people(uid, module)

            }

            # Employees must choose at least one reviewer
            # whenever authorized choices exist.

            # Use the configured approval route when no additional reviewer is selected.
            pass

        # Prevent forged or unauthorized reviewer selection.

        if any(
            reviewer not in allowed_ids
            for reviewer in selected
        ):

            abort(403)

        # Use the existing route when no choices were made.

        if not selected:

            selected = sl_default_chain(uid, module)

        # Required reviewers cannot be removed by employees.

        mandatory_reviewers = sl_required_reviewers(
            uid,
            module
        )

        # Mandatory Team Lead and Reporting Manager
        # always appear first, followed by additional
        # approvers selected by the employee.

        selected = list(dict.fromkeys(

            mandatory_reviewers + selected

        ))

        # Reject inactive or unauthorized reviewers.

        if any(
            reviewer not in allowed_ids
            for reviewer in selected
        ):

            abort(
                403,
                description="An assigned reviewer is unavailable."
            )

        if len(selected) > 10:

            abort(
                400,
                description="A maximum of 10 approvers is allowed."
            )

        # Every new request begins in its initial state.

        data = dict(data)

        data["status"] = MODULES[module]["initial"]

        keys = list(data)

        placeholders = ",".join(
            "?" for key in keys
        )

        cursor = connection.execute(

            f"INSERT INTO {table} "

            f"({','.join(keys)}) "

            f"VALUES ({placeholders})",

            [data[key] for key in keys]

        )

        record_id = cursor.lastrowid

        for position, reviewer in enumerate(
            selected,
            start=1
        ):

            connection.execute("""

                INSERT INTO sl_request_approvers

                (
                    module,
                    record_id,
                    reviewer_id,
                    position
                )

                VALUES (?,?,?,?)

            """, (

                module,

                record_id,

                reviewer,

                position

            ))

        connection.commit()

        return record_id

    except Exception:

        connection.rollback()

        raise


# =========================================================
# GET THE APPROVAL SEQUENCE FOR A REQUEST
# =========================================================

def sl_request_chain(uid, module, record_id):

    reviewers = rows("""

        SELECT reviewer_id

        FROM sl_request_approvers

        WHERE module=?

        AND record_id=?

        ORDER BY position

    """, (

        module,

        record_id

    ))

    if reviewers:

        return [

            item["reviewer_id"]

            for item in reviewers

        ]

    return sl_default_chain(
        uid,
        module
    )


# =========================================================
# FIND NEXT REQUIRED APPROVER
# =========================================================

_sl_previous_next_reviewer = next_reviewer


def next_reviewer(uid, module, record_id):

    if module not in SL_APPROVAL_MODULES:

        return _sl_previous_next_reviewer(
            uid,
            module,
            record_id
        )

    completed = {

        item["reviewer_id"]

        for item in rows("""

            SELECT reviewer_id

            FROM delegated_approvals

            WHERE module=?

            AND record_id=?

        """, (

            module,

            record_id

        ))

    }

    for reviewer in sl_request_chain(
        uid,
        module,
        record_id
    ):

        if reviewer not in completed:

            return reviewer

    return None


# =========================================================
# APPROVAL STATUS DISPLAY
# =========================================================

_sl_previous_approval_stage = approval_stage


def approval_stage(uid, module, record_id):

    if module not in SL_APPROVAL_MODULES:

        return _sl_previous_approval_stage(
            uid,
            module,
            record_id
        )

    record = sl_get_request(module, record_id)

    if record["status"] == "Approved":

        return "All required approvals completed"

    if record["status"] == "Rejected":

        return "Request rejected"

    if record["status"] not in SL_ACTIVE_STATES:

        return "Decision recorded"

    reviewer_id = next_reviewer(
        uid,
        module,
        record_id
    )

    chain = sl_request_chain(
        uid,
        module,
        record_id
    )

    if reviewer_id:

        reviewer = row("""

            SELECT name,status

            FROM users

            WHERE id=?

        """, (reviewer_id,))

        if not reviewer or reviewer["status"] != "Approved":

            return "HR must resolve inactive reviewer"

        position = chain.index(reviewer_id) + 1

        return (

            f"Awaiting {reviewer['name']} "

            f"({position}/{len(chain)})"

        )

    return "Awaiting HR approval"


# =========================================================
# SECURE REQUEST LOOKUP
# =========================================================

def sl_get_request(module, record_id):

    if module not in SL_APPROVAL_MODULES:

        abort(404)

    table = MODULES[module]["table"]

    sql = f"""

        SELECT *

        FROM {table}

        WHERE id=?

    """

    args = [record_id]

    if table == "employee_records":

        sql += " AND module=?"

        args.append(module)

    record = row(sql, args)

    if not record:

        abort(404)

    return record


# =========================================================
# RECOGNIZE NEWLY SELECTED REVIEWERS
# WITHOUT GIVING THEM HR PERMISSIONS
# =========================================================

_sl_previous_is_reviewer = is_reviewer


def is_reviewer():

    if not g.user:

        return False

    if _sl_previous_is_reviewer():

        return True

    return bool(row("""

        SELECT 1

        FROM sl_request_approvers

        WHERE reviewer_id=?

        LIMIT 1

    """, (g.user["id"],)))


# =========================================================
# MANAGER / TEAM LEAD APPROVAL INBOX
# =========================================================

@login_required
def sl_approval_inbox():

    if not is_reviewer():

        abort(403)

    inbox = []

    for module in SL_APPROVAL_MODULES:

        config = MODULES[module]

        table = config["table"]

        sql = f"""

            SELECT r.*,u.name AS employee_name

            FROM {table} r

            JOIN users u

            ON u.id=r.user_id

            WHERE r.status IN (

                'Pending',

                'Open',

                'In Progress'

            )

        """

        args = []

        if table == "employee_records":

            sql += " AND r.module=?"

            args.append(module)

        for item in rows(sql, args):

            current = next_reviewer(

                item["user_id"],

                module,

                item["id"]

            )

            if not admin():

                if current != g.user["id"]:

                    continue

                if item["user_id"] == g.user["id"]:

                    continue

            inbox.append({

                "module": module,

                "id": item["id"],

                "employee": item["employee_name"],

                "title": config["title"],

                "status": item["status"],

                "stage": approval_stage(

                    item["user_id"],

                    module,

                    item["id"]

                ),

                "details": [

                    (

                        f["label"],

                        item[f["name"]]

                    )

                    for f in config["fields"]

                ]

            })

    return page(

        "Approval Inbox",

        """

        <h1>Approval Inbox</h1>

        <p class="muted">

            Review the requests assigned to you.

        </p>

        <div class="card">

            <h2>Pending Requests</h2>

            <div class="table-wrap">

                <table>

                    <thead>

                        <tr>

                            <th>Employee</th>

                            <th>Request</th>

                            <th>Details</th>

                            <th>Approval Stage</th>

                            <th>Decision</th>

                        </tr>

                    </thead>

                    <tbody>

                        {% for item in inbox %}

                        <tr>

                            <td>

                                <strong>

                                    {{ item.employee }}

                                </strong>

                            </td>

                            <td>

                                {{ item.title }}

                                <small>#{{ item.id }}</small>

                            </td>

                            <td>

                                {% for label,value in item.details %}

                                    <p>

                                        <strong>{{ label }}:</strong>

                                        {{ value if value is not none else '—' }}

                                    </p>

                                {% endfor %}

                            </td>

                            <td>

                                {{ badge(item.status) }}

                                <small>{{ item.stage }}</small>

                            </td>

                            <td>

                                {% if admin() %}

                                    <a
                                        class="btn btn-purple"
                                        href="{{ url_for(
                                            'review_record',
                                            module=item.module,
                                            record_id=item.id
                                        ) }}"
                                    >

                                        HR Review

                                    </a>

                                {% else %}

                                    <form

                                        method="post"

                                        action="{{ sl_decision_url(
                                            item.module,
                                            item.id
                                        ) }}"

                                    >

                                        {{ token() }}

                                        <textarea

                                            name="remarks"

                                            maxlength="1000"

                                            placeholder="Approval remarks"

                                        ></textarea>

                                        <div class="inline">

                                            <button

                                                class="btn"

                                                name="decision"

                                                value="Approved"

                                            >

                                                Approve

                                            </button>

                                            <button

                                                class="btn btn-red"

                                                name="decision"

                                                value="Rejected"

                                            >

                                                Reject

                                            </button>

                                        </div>

                                    </form>

                                {% endif %}

                            </td>

                        </tr>

                        {% else %}

                        <tr>

                            <td colspan="5">

                                No requests awaiting your approval.

                            </td>

                        </tr>

                        {% endfor %}

                    </tbody>

                </table>

            </div>

        </div>

        """,

        inbox=inbox

    )


# =========================================================
# SUPPORT EXISTING APPROVAL URL FORMAT
# =========================================================

def sl_decision_url(module, record_id):

    rule = next(

        rule

        for rule in app.url_map.iter_rules()

        if rule.endpoint == "delegated_decision"

    )

    parameter = (

        "record_id"

        if "record_id" in rule.arguments

        else "rid"

    )

    return url_for(

        "delegated_decision",

        module=module,

        **{parameter: record_id}

    )


# =========================================================
# INDIVIDUAL APPROVER DECISION
# =========================================================

@login_required
def sl_approver_decision(
    module,
    record_id=None,
    rid=None
):

    record_id = (

        record_id

        if record_id is not None

        else rid

    )

    if admin():

        abort(403)

    result = field("decision")

    remarks = field("remarks", 1000)

    if result not in ("Approved", "Rejected"):

        abort(400)

    if result == "Rejected" and not remarks:

        flash(

            "Enter a reason for rejecting the request.",

            "error"

        )

        return redirect(

            url_for("team_approvals")

        )

    connection = db()

    connection.execute("BEGIN IMMEDIATE")

    final_approval = False

    try:

        record = sl_get_request(
            module,
            record_id
        )

        uid = record["user_id"]

        if uid == g.user["id"]:

            abort(403)

        if record["status"] not in SL_ACTIVE_STATES:

            abort(409)

        if next_reviewer(
            uid,
            module,
            record_id
        ) != g.user["id"]:

            abort(403)

        if result == "Approved":

            connection.execute("""

                INSERT INTO delegated_approvals

                (
                    module,
                    record_id,
                    reviewer_id,
                    decided_at
                )

                VALUES (?,?,?,?)

            """, (

                module,

                record_id,

                g.user["id"],

                stamp()

            ))

            remaining = next_reviewer(

                uid,

                module,

                record_id

            )

            require_hr = workflow_hr_final(
                uid,
                module
            )

            if remaining is None and not require_hr:

                final_approval = True

        # Only the final reviewer completes approval.
        # Any selected reviewer can reject the request.

        if result == "Rejected" or final_approval:

            table = MODULES[module]["table"]

            sql = f"""

                UPDATE {table}

                SET status=?

                WHERE id=?

            """

            args = [

                result,

                record_id

            ]

            if table == "employee_records":

                sql += " AND module=?"

                args.append(module)

            connection.execute(sql, args)

        decision_name = (

            "Approved"

            if final_approval

            else "Delegated " + result

        )

        connection.execute("""

            INSERT INTO approval_history

            (
                module,
                record_id,
                employee_id,
                decision,
                decided_by,
                remarks,
                created_at
            )

            VALUES (?,?,?,?,?,?,?)

        """, (

            module,

            record_id,

            uid,

            decision_name,

            g.user["id"],

            remarks,

            stamp()

        ))

        connection.execute("""

            INSERT INTO audit_logs

            (user_id,action,created_at)

            VALUES (?,?,?)

        """, (

            g.user["id"],

            f"{result} {module} request #{record_id}",

            stamp()

        ))

        connection.commit()

    except Exception:

        connection.rollback()

        raise

    if result == "Rejected":

        flash("Request rejected.")

    elif final_approval:

        flash("All required approvals completed.")

    else:

        flash(
            "Approved and forwarded to the next reviewer."
        )

    return redirect(

        url_for("team_approvals")

    )


# =========================================================
# CONNECT NEW APPROVAL HANDLERS
# =========================================================

app.view_functions[
    "team_approvals"
] = sl_approval_inbox

app.view_functions[
    "delegated_decision"
] = sl_approver_decision


# =========================================================
# HR ONLY - CONFIGURE ELIGIBLE APPROVERS
# =========================================================

@app.route(

    "/hr/eligible-approvers",

    methods=["GET", "POST"]

)
@admin_required
def sl_manage_approvers():

    uid = request.values.get(
        "uid",
        type=int
    )

    module = request.values.get(

        "module",

        "leaves"

    )

    if module not in SL_APPROVAL_MODULES:

        abort(400)

    employees = rows("""

        SELECT id,name,employee_code

        FROM users

        WHERE role='Employee'

        ORDER BY name

    """)

    employee = None

    if uid:

        employee = row("""

            SELECT id,name,employee_code

            FROM users

            WHERE id=?

            AND role='Employee'

        """, (uid,))

        if not employee:

            abort(404)

    candidates = rows("""

        SELECT id,name,designation,department

        FROM users

        WHERE role='Employee'

        AND status='Approved'

        AND id!=?

        ORDER BY name

    """, (uid or 0,))

    if request.method == "POST":

        if not employee:

            abort(400)

        try:

            chosen = {

                int(value)

                for value in request.form.getlist(
                    "reviewers"
                )

            }

        except ValueError:

            abort(400)

        eligible = {

            item["id"]

            for item in candidates

        }

        if not chosen.issubset(eligible):

            abort(400)

        connection = db()

        connection.execute("BEGIN IMMEDIATE")

        try:

            connection.execute("""

                DELETE FROM sl_eligible_approvers

                WHERE user_id=?

                AND module=?

            """, (

                uid,

                module

            ))

            for reviewer in chosen:

                connection.execute("""

                    INSERT INTO sl_eligible_approvers

                    (
                        user_id,
                        module,
                        reviewer_id
                    )

                    VALUES (?,?,?)

                """, (

                    uid,

                    module,

                    reviewer

                ))

            connection.commit()

        except Exception:

            connection.rollback()

            raise

        audit(

            f"Configured selectable approvers "

            f"for employee #{uid}, module {module}"

        )

        flash("Authorized approvers saved.")

        return redirect(

            url_for(

                "sl_manage_approvers",

                uid=uid,

                module=module

            )

        )

    selected = {

        item["reviewer_id"]

        for item in rows("""

            SELECT reviewer_id

            FROM sl_eligible_approvers

            WHERE user_id=?

            AND module=?

        """, (

            uid or 0,

            module

        ))

    }

    defaults = (

        sl_default_chain(uid, module)

        if uid

        else []

    )

    return page(

        "Manage Eligible Approvers",

        """

        <h1>Manage Eligible Approvers</h1>

        <p class="muted">

            Choose the people each employee is allowed
            to select when submitting requests.

        </p>

        <div class="card">

            <form method="get">

                <div class="form-grid">

                    <div>

                        <label>Employee</label>

                        <select
                            name="uid"
                            onchange="this.form.submit()"
                        >

                            <option value="">
                                Select Employee
                            </option>

                            {% for person in employees %}

                                <option

                                    value="{{ person.id }}"

                                    {{ 'selected'
                                       if uid==person.id
                                       else '' }}

                                >

                                    {{ person.name }}

                                    — {{ person.employee_code }}

                                </option>

                            {% endfor %}

                        </select>

                    </div>

                    <div>

                        <label>Request Type</label>

                        <select
                            name="module"
                            onchange="this.form.submit()"
                        >

                            {% for key,title in modules %}

                                <option

                                    value="{{ key }}"

                                    {{ 'selected'
                                       if module==key
                                       else '' }}

                                >

                                    {{ title }}

                                </option>

                            {% endfor %}

                        </select>

                    </div>

                </div>

            </form>

        </div>

        {% if employee %}

        <div class="card">

            <h2>

                Eligible Reviewers for {{ employee.name }}

            </h2>

            <p class="muted">

                Existing assigned managers and team leads
                are included automatically.

            </p>

            <form method="post">

                {{ token() }}

                <input

                    type="hidden"

                    name="uid"

                    value="{{ uid }}"

                >

                <input

                    type="hidden"

                    name="module"

                    value="{{ module }}"

                >

                <div class="table-wrap">

                    <table>

                        <thead>

                            <tr>

                                <th>Employee</th>

                                <th>Designation</th>

                                <th>Department</th>

                                <th>Allow Approval</th>

                            </tr>

                        </thead>

                        <tbody>

                            {% for person in candidates %}

                                <tr>

                                    <td>

                                        {{ person.name }}

                                    </td>

                                    <td>

                                        {{ person.designation }}

                                    </td>

                                    <td>

                                        {{ person.department }}

                                    </td>

                                    <td>

                                        {% if person.id in defaults %}

                                            <input

                                                type="checkbox"

                                                checked

                                                disabled

                                            >

                                            <small>

                                                Assigned in Workflow
                                                Configuration

                                            </small>

                                        {% else %}

                                            <input

                                                type="checkbox"

                                                name="reviewers"

                                                value="{{ person.id }}"

                                                {{ 'checked'
                                                   if person.id in selected
                                                   else '' }}

                                            >

                                        {% endif %}

                                    </td>

                                </tr>

                            {% else %}

                                <tr>

                                    <td colspan="4">

                                        No available reviewers.

                                    </td>

                                </tr>

                            {% endfor %}

                        </tbody>

                    </table>

                </div>

                <br>

                <button class="btn btn-purple">

                    Save Eligible Approvers

                </button>

            </form>

        </div>

        {% endif %}

        """,

        uid=uid,

        employee=employee,

        employees=employees,

        module=module,

        modules=[

            (key, MODULES[key]["title"])

            for key in SL_APPROVAL_MODULES

        ],

        candidates=candidates,

        selected=selected,

        defaults=defaults

    )


# =========================================================
# ADD NEW CONFIGURATION TO HR SIDEBAR
# =========================================================

_sl_previous_menu = visible_menu


def visible_menu():

    menu = _sl_previous_menu()

    if not g.user or not admin():

        return menu

    if g.user["must_change_password"]:

        return menu

    if profile_required(g.user):

        return menu

    menu.append({

        "name": "Approver Management",

        "items": [

            {

                "label": "Manage Eligible Approvers",

                "link": url_for(
                    "sl_manage_approvers"
                ),

                "icon": "users",

                "color": "purple"

            }

        ]

    })

    return menu


# =========================================================
# JINJA TEMPLATE HELPERS
# =========================================================

app.jinja_env.globals.update(

    sl_approval_modules=SL_APPROVAL_MODULES,

    sl_form_approvers=sl_form_approvers,

    sl_decision_url=sl_decision_url,

    approval_stage=approval_stage

)


# =========================================================
# PROFESSIONAL LIGHT AND DARK MODE STYLING
# =========================================================

SL_APPROVER_STYLE = """

<style>

.sl-approver-section {

    background: #f8f7fb;
    border: 1px solid #dedce6;
    padding: 22px;
    border-radius: 10px;
    margin: 22px 0;

}

.sl-approver-row {

    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
    margin-bottom: 15px;

}

.sl-approver-row label {

    min-width: 100px;
    font-weight: 650;
    margin: 0;

}

.sl-approver-row select {

    flex: 1;
    min-width: 0;
    min-height: 45px;

}

body.dark .sl-approver-section {

    background: #272433;
    border-color: #494358;
    color: #f1eef7;

}

@media (max-width: 650px) {

    .sl-approver-row {

        flex-direction: column;
        align-items: stretch;

    }

    .sl-approver-section {

        padding: 16px;

    }

}

</style>

"""

LAYOUT = LAYOUT.replace(

    "</head>",

    SL_APPROVER_STYLE + "</head>",

    1

)





# STAR LINK NATIONALITY APPROVAL V1

# =========================================================
# COUNTRY LIST
# =========================================================

SL_COUNTRIES = ['Afghanistan', 'Albania', 'Algeria', 'American Samoa', 'Andorra', 'Angola', 'Anguilla', 'Antarctica', 'Antigua and Barbuda', 'Argentina', 'Armenia', 'Aruba', 'Australia', 'Austria', 'Azerbaijan', 'Bahamas', 'Bahrain', 'Bangladesh', 'Barbados', 'Belarus', 'Belgium', 'Belize', 'Benin', 'Bermuda', 'Bhutan', 'Bolivia', 'Bonaire, Sint Eustatius and Saba', 'Bosnia and Herzegovina', 'Botswana', 'Bouvet Island', 'Brazil', 'British Indian Ocean Territory', 'British Virgin Islands', 'Brunei', 'Bulgaria', 'Burkina Faso', 'Burundi', 'Cabo Verde', 'Cambodia', 'Cameroon', 'Canada', 'Cayman Islands', 'Central African Republic', 'Chad', 'Chile', 'China', 'Christmas Island', 'Cocos (Keeling) Islands', 'Colombia', 'Comoros', 'Congo', 'Cook Islands', 'Costa Rica', 'Croatia', 'Cuba', 'Curaçao', 'Cyprus', 'Czechia', "Côte d'Ivoire", 'Democratic Republic of the Congo', 'Denmark', 'Djibouti', 'Dominica', 'Dominican Republic', 'Ecuador', 'Egypt', 'El Salvador', 'Equatorial Guinea', 'Eritrea', 'Estonia', 'Eswatini', 'Ethiopia', 'Falkland Islands', 'Faroe Islands', 'Fiji', 'Finland', 'France', 'French Guiana', 'French Polynesia', 'French Southern Territories', 'Gabon', 'Gambia', 'Georgia', 'Germany', 'Ghana', 'Gibraltar', 'Greece', 'Greenland', 'Grenada', 'Guadeloupe', 'Guam', 'Guatemala', 'Guernsey', 'Guinea', 'Guinea-Bissau', 'Guyana', 'Haiti', 'Heard Island and McDonald Islands', 'Honduras', 'Hong Kong', 'Hungary', 'Iceland', 'India', 'Indonesia', 'Iran', 'Iraq', 'Ireland', 'Isle of Man', 'Israel', 'Italy', 'Jamaica', 'Japan', 'Jersey', 'Jordan', 'Kazakhstan', 'Kenya', 'Kiribati', 'Kuwait', 'Kyrgyzstan', 'Laos', 'Latvia', 'Lebanon', 'Lesotho', 'Liberia', 'Libya', 'Liechtenstein', 'Lithuania', 'Luxembourg', 'Macao', 'Madagascar', 'Malawi', 'Malaysia', 'Maldives', 'Mali', 'Malta', 'Marshall Islands', 'Martinique', 'Mauritania', 'Mauritius', 'Mayotte', 'Mexico', 'Micronesia', 'Moldova', 'Monaco', 'Mongolia', 'Montenegro', 'Montserrat', 'Morocco', 'Mozambique', 'Myanmar', 'Namibia', 'Nauru', 'Nepal', 'Netherlands', 'New Caledonia', 'New Zealand', 'Nicaragua', 'Niger', 'Nigeria', 'Niue', 'Norfolk Island', 'North Korea', 'North Macedonia', 'Northern Mariana Islands', 'Norway', 'Oman', 'Pakistan', 'Palau', 'Palestine', 'Panama', 'Papua New Guinea', 'Paraguay', 'Peru', 'Philippines', 'Pitcairn', 'Poland', 'Portugal', 'Puerto Rico', 'Qatar', 'Romania', 'Russia', 'Rwanda', 'Réunion', 'Saint Barthélemy', 'Saint Helena, Ascension and Tristan da Cunha', 'Saint Kitts and Nevis', 'Saint Lucia', 'Saint Martin', 'Saint Pierre and Miquelon', 'Saint Vincent and the Grenadines', 'Samoa', 'San Marino', 'Sao Tome and Principe', 'Saudi Arabia', 'Senegal', 'Serbia', 'Seychelles', 'Sierra Leone', 'Singapore', 'Sint Maarten', 'Slovakia', 'Slovenia', 'Solomon Islands', 'Somalia', 'South Africa', 'South Georgia and the South Sandwich Islands', 'South Korea', 'South Sudan', 'Spain', 'Sri Lanka', 'Sudan', 'Suriname', 'Svalbard and Jan Mayen', 'Sweden', 'Switzerland', 'Syria', 'Taiwan', 'Tajikistan', 'Tanzania', 'Thailand', 'Timor-Leste', 'Togo', 'Tokelau', 'Tonga', 'Trinidad and Tobago', 'Tunisia', 'Turkmenistan', 'Turks and Caicos Islands', 'Tuvalu', 'Türkiye', 'U.S. Virgin Islands', 'Uganda', 'Ukraine', 'United Arab Emirates', 'United Kingdom', 'United States', 'United States Minor Outlying Islands', 'Uruguay', 'Uzbekistan', 'Vanuatu', 'Vatican City', 'Venezuela', 'Vietnam', 'Wallis and Futuna', 'Western Sahara', 'Yemen', 'Zambia', 'Zimbabwe', 'Åland Islands']


# =========================================================
# SUPPORT PREVIOUS NATIONALITY VALUES
# =========================================================

def sl_normalize_country(value):

    value = str(value or "").strip()

    aliases = {

        "indian": "India",

        "pakistani": "Pakistan",

        "bangladeshi": "Bangladesh",

        "filipino": "Philippines",

        "filipina": "Philippines",

        "emirati": "United Arab Emirates",

        "uae": "United Arab Emirates",

        "british": "United Kingdom",

        "uk": "United Kingdom",

        "american": "United States",

        "usa": "United States",

        "egyptian": "Egypt",

        "nepali": "Nepal",

        "nepalese": "Nepal",

        "sri lankan": "Sri Lanka",

        "turkey": "Türkiye",

        "russian federation": "Russia",

        "viet nam": "Vietnam",

        "syrian arab republic": "Syria"

    }

    value = aliases.get(
        value.casefold(),
        value
    )

    for country in SL_COUNTRIES:

        if country.casefold() == value.casefold():

            return country

    # Unknown legacy entries must be corrected
    # using the nationality dropdown.

    return ""


# =========================================================
# DATABASE MIGRATION
# =========================================================

_sl_nationality_previous_init = init_db


def init_db():

    _sl_nationality_previous_init()

    connection = db()

    connection.execute("BEGIN IMMEDIATE")

    try:

        connection.execute("""

            CREATE TABLE IF NOT EXISTS sl_approval_policy (

                user_id INTEGER PRIMARY KEY,

                allow_optional INTEGER NOT NULL DEFAULT 0,

                FOREIGN KEY(user_id)

                REFERENCES users(id)

                ON DELETE CASCADE

            )

        """)

        connection.commit()

    except Exception:

        connection.rollback()

        raise


# =========================================================
# CHECK HR-AUTHORIZED EMPLOYEE EXCEPTION
# =========================================================

def sl_optional_stages(uid):

    if not uid:

        return False

    policy = row("""

        SELECT allow_optional

        FROM sl_approval_policy

        WHERE user_id=?

    """, (uid,))

    return bool(

        policy and policy["allow_optional"]

    )


# =========================================================
# GET MANDATORY TEAM LEAD AND REPORTING MANAGER
# =========================================================

def sl_required_reviewers(uid, module):

    if module not in SL_APPROVAL_MODULES:

        return []

    employee = row("""

        SELECT id,role,status

        FROM users

        WHERE id=?

    """, (uid,))

    if not employee:

        abort(404)

    # The existing workflow configuration manages
    # employee accounts, not the main administrator.

    if employee["role"] == "Admin":

        return []

    # Only HR can authorize optional approval stages.

    if sl_optional_stages(uid):

        return []

    route = row("""

        SELECT lead_id,manager_id

        FROM approval_routes

        WHERE user_id=?

        AND module=?

    """, (uid, module))

    if not route:

        abort(

            400,

            description=(
                "HR must configure the Team Lead "
                "and Reporting Manager before "
                "this request can be submitted."
            )

        )

    lead_id = route["lead_id"]

    manager_id = route["manager_id"]

    if (

        not lead_id

        or not manager_id

        or lead_id == manager_id

        or uid in (lead_id, manager_id)

    ):

        abort(

            400,

            description=(
                "A valid Team Lead and Reporting "
                "Manager must be assigned."
            )

        )

    active_reviewers = {

        person["id"]

        for person in sl_eligible_people(
            uid,
            module
        )

    }

    if (

        lead_id not in active_reviewers

        or manager_id not in active_reviewers

    ):

        abort(

            400,

            description=(
                "An assigned reviewer is inactive. "
                "Contact HR to update the approval route."
            )

        )

    return [lead_id, manager_id]


# =========================================================
# DISPLAY REQUIRED APPROVERS IN REQUEST FORMS
# =========================================================

def sl_mandatory_approver_names(module):

    if not g.user:

        return []

    if admin():

        return []

    if sl_optional_stages(g.user["id"]):

        return []

    route = row("""

        SELECT lead_id,manager_id

        FROM approval_routes

        WHERE user_id=?

        AND module=?

    """, (g.user["id"], module))

    if not route:

        return []

    names = []

    for reviewer_id in (

        route["lead_id"],

        route["manager_id"]

    ):

        if reviewer_id:

            person = row("""

                SELECT name

                FROM users

                WHERE id=?

            """, (reviewer_id,))

            if person:

                names.append(person["name"])

    return names


# =========================================================
# REGISTER TEMPLATE HELPERS
# =========================================================

app.jinja_env.globals.update(

    sl_countries=SL_COUNTRIES,

    sl_optional_stages=sl_optional_stages,

    sl_mandatory_approver_names=sl_mandatory_approver_names

)


# =========================================================
# PROFESSIONAL DESIGN
# =========================================================

SL_NATIONALITY_STYLE = """

<style>

.sl-country-select {

    width: 100%;

    min-height: 48px;

    padding: 12px 14px;

    border: 1px solid #d8d8e2;

    border-radius: 7px;

    background: #ffffff;

    color: #17191d;

    font-size: 14px;

    cursor: pointer;

}

.sl-country-select:focus {

    outline: none;

    border-color: #7754ac;

}

.sl-approval-policy {

    background: #f8f7fb;

    border: 1px solid #dedce6;

    border-radius: 10px;

    padding: 20px;

    margin-bottom: 25px;

}

.sl-policy-checkbox {

    display: flex;

    align-items: center;

    gap: 12px;

    font-weight: 650;

    margin: 16px 0;

}

.sl-policy-checkbox input {

    width: 19px;

    height: 19px;

    flex-shrink: 0;

    accent-color: #347c49;

}

.sl-required-reviewers {

    padding: 15px;

    border: 1px solid #ded6ed;

    border-radius: 8px;

    background: #f3f0f9;

    color: #30254f;

    margin: 15px 0;

}

body.dark .sl-country-select {

    background: #191823 !important;

    color: #f1eef7 !important;

    border-color: #494358 !important;

}

body.dark .sl-approval-policy,

body.dark .sl-required-reviewers {

    background: #272433;

    color: #f1eef7;

    border-color: #494358;

}

@media (max-width: 650px) {

    .sl-approval-policy {

        padding: 16px;

    }

}

</style>

"""


# =========================================================
# DYNAMIC MANDATORY APPROVAL DROPDOWNS
# =========================================================

SL_POLICY_SCRIPT = r"""

<script>

document.addEventListener("DOMContentLoaded", () => {

    const optional = document.getElementById(
        "sl-allow-optional"
    );

    if (!optional) return;

    const selectors = document.querySelectorAll(
        "[data-sl-approval-stage]"
    );

    function updateApprovalPolicy() {

        selectors.forEach(select => {

            const allowSkip = optional.checked;

            const isLead = select.name.endsWith("_lead");

            select.required = !allowSkip;

            const placeholder = Array.from(
                select.options
            ).find(option => option.value === "");

            if (placeholder) {

                placeholder.disabled = !allowSkip;

                placeholder.textContent = allowSkip

                    ? "Skip This Approval Stage"

                    : (
                        isLead
                        ? "Select Team Lead"
                        : "Select Reporting Manager"
                    );

            }

        });

    }

    optional.addEventListener(
        "change",
        updateApprovalPolicy
    );

    updateApprovalPolicy();

});

</script>

"""


# =========================================================
# APPLY TO SHARED WEBSITE LAYOUT
# =========================================================

LAYOUT = LAYOUT.replace(

    "</head>",

    SL_NATIONALITY_STYLE + "</head>",

    1

)

LAYOUT = LAYOUT.replace(

    "</body>",

    SL_POLICY_SCRIPT + "</body>",

    1

)





# STAR LINK FINAL PORTAL FIX V1

# =========================================================
# NOTIFICATION SETTINGS
# =========================================================

SL_FIX_PENDING = (
    "Pending",
    "Open",
    "In Progress"
)

SL_FIX_ROUTED = tuple(
    globals().get(
        "SL_APPROVAL_MODULES",
        globals().get(
            "WORKFLOW_MODULES",
            globals().get("ROUTED", ())
        )
    )
)

SL_FIX_SHAREABLE = (
    "tickets",
    "support",
    "leaves",
    "timesheets",
    "extra-work",
    "tasks",
    "onboarding",
    "assets",
    "trainings",
    "skills",
    "transportation",
    "team-calendar"
)


# =========================================================
# DATABASE HELPERS
# =========================================================

def sl_fix_table_exists(connection, table):

    return bool(connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type='table'
        AND name=?
        """,
        (table,)
    ).fetchone())


def sl_fix_columns(connection, table):

    return {
        item[1]
        for item in connection.execute(
            f"PRAGMA table_info({table})"
        )
    }


# =========================================================
# INITIALIZE NOTIFICATION DATABASE
# =========================================================

_sl_fix_previous_init = init_db


def init_db():

    _sl_fix_previous_init()

    connection = db()

    try:

        connection.execute("BEGIN IMMEDIATE")

        connection.execute("""
            CREATE TABLE IF NOT EXISTS sl_notifications (

                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                module TEXT NOT NULL,
                record_id INTEGER,
                kind TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                read_at TEXT,

                FOREIGN KEY(user_id)
                REFERENCES users(id)
                ON DELETE CASCADE

            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS
            sl_notifications_user_index

            ON sl_notifications(user_id, read_at, id)
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS
            sl_approval_notification_reads (

                user_id INTEGER NOT NULL,
                notification_key TEXT NOT NULL,
                read_at TEXT NOT NULL,

                PRIMARY KEY(
                    user_id,
                    notification_key
                )

            )
        """)

        has_recipients = sl_fix_table_exists(
            connection,
            "sl_request_recipients"
        )


        # =================================================
        # NOTIFY ALL SELECTED RECIPIENTS
        # =================================================

        if has_recipients:

            connection.execute("""
                CREATE TRIGGER IF NOT EXISTS
                sl_notification_recipient_added

                AFTER INSERT ON sl_request_recipients

                BEGIN

                    INSERT INTO sl_notifications (

                        user_id,
                        module,
                        record_id,
                        kind,
                        message,
                        created_at

                    )

                    SELECT

                        NEW.recipient_id,
                        NEW.module,
                        NEW.record_id,
                        'addressed',
                        'A new request has been addressed to you.',
                        datetime('now')

                    FROM users

                    WHERE id=NEW.recipient_id
                    AND status='Approved';

                END
            """)


        # =================================================
        # NOTIFICATIONS ACROSS ALL RECORD MODULES
        # =================================================

        for module, config in MODULES.items():

            # Confidential HR notes remain private.

            if module == "hr-notes":
                continue

            table = config.get("table")

            if not table:
                continue

            if not sl_fix_table_exists(connection, table):
                continue

            columns = sl_fix_columns(
                connection,
                table
            )

            if not {"id", "user_id"}.issubset(columns):
                continue

            trigger_name = module.replace("-", "_")

            condition = (

                f"NEW.module='{module}'"

                if table == "employee_records"

                else "1=1"

            )

            old_condition = (

                f"OLD.module='{module}'"

                if table == "employee_records"

                else "1=1"

            )


            # =============================================
            # NEW RECORD OR TASK
            # =============================================

            connection.execute(f"""
                CREATE TRIGGER IF NOT EXISTS
                sl_notification_create_{trigger_name}

                AFTER INSERT ON {table}

                WHEN {condition}

                BEGIN

                    INSERT INTO sl_notifications (

                        user_id,
                        module,
                        record_id,
                        kind,
                        message,
                        created_at

                    )

                    SELECT

                        NEW.user_id,
                        '{module}',
                        NEW.id,
                        'created',
                        'A new record was created for your account.',
                        datetime('now')

                    FROM users

                    WHERE id=NEW.user_id
                    AND status='Approved';

                END
            """)


            # =============================================
            # INCLUDE SELECTED RECIPIENTS
            # =============================================

            extra_recipients = ""

            if (

                has_recipients
                and module in SL_FIX_SHAREABLE

            ):

                extra_recipients = f"""

                    UNION

                    SELECT recipient_id
                    FROM sl_request_recipients

                    WHERE module='{module}'
                    AND record_id=NEW.id

                    UNION

                    SELECT sender_id
                    FROM sl_request_recipients

                    WHERE module='{module}'
                    AND record_id=NEW.id

                """


            # =============================================
            # STATUS CHANGE NOTIFICATIONS
            # =============================================

            if "status" in columns:

                message = """

                    CASE

                        WHEN OLD.status IS NOT NEW.status

                        THEN 'Status changed to ' ||
                            COALESCE(NEW.status, 'Updated')

                        ELSE 'A record concerning you was updated.'

                    END

                """

            elif (

                module == "attendance"
                and "check_out" in columns

            ):

                message = """

                    CASE

                        WHEN OLD.check_out IS NOT NEW.check_out
                        AND NEW.check_out IS NOT NULL

                        THEN 'Your check-out has been recorded.'

                        ELSE 'Your attendance record was updated.'

                    END

                """

            else:

                message = (
                    "'A record concerning you was updated.'"
                )


            connection.execute(f"""
                CREATE TRIGGER IF NOT EXISTS
                sl_notification_update_{trigger_name}

                AFTER UPDATE ON {table}

                WHEN {condition}

                BEGIN

                    INSERT INTO sl_notifications (

                        user_id,
                        module,
                        record_id,
                        kind,
                        message,
                        created_at

                    )

                    SELECT

                        u.id,
                        '{module}',
                        NEW.id,
                        'updated',
                        {message},
                        datetime('now')

                    FROM users u

                    WHERE u.status='Approved'

                    AND u.id IN (

                        SELECT NEW.user_id

                        {extra_recipients}

                    );

                END
            """)


            # =============================================
            # RECORD DELETION NOTIFICATIONS
            # =============================================

            delete_recipients = ""

            if (
                has_recipients
                and module in SL_FIX_SHAREABLE
            ):

                delete_recipients = f"""

                    UNION

                    SELECT recipient_id
                    FROM sl_request_recipients

                    WHERE module='{module}'
                    AND record_id=OLD.id

                    UNION

                    SELECT sender_id
                    FROM sl_request_recipients

                    WHERE module='{module}'
                    AND record_id=OLD.id

                """

            connection.execute(f"""
                CREATE TRIGGER IF NOT EXISTS
                sl_notification_delete_{trigger_name}

                BEFORE DELETE ON {table}

                WHEN {old_condition}

                BEGIN

                    INSERT INTO sl_notifications (

                        user_id,
                        module,
                        record_id,
                        kind,
                        message,
                        created_at

                    )

                    SELECT

                        u.id,
                        '{module}',
                        OLD.id,
                        'deleted',
                        'A record concerning you was removed.',
                        datetime('now')

                    FROM users u

                    WHERE u.status='Approved'

                    AND u.id IN (

                        SELECT OLD.user_id

                        {delete_recipients}

                    );

                END
            """)


        # =================================================
        # INTERMEDIATE APPROVAL NOTIFICATIONS
        # =================================================

        if sl_fix_table_exists(
            connection,
            "approval_history"
        ):

            connection.execute("""
                CREATE TRIGGER IF NOT EXISTS
                sl_notification_delegated_approval

                AFTER INSERT ON approval_history

                WHEN NEW.decision='Delegated Approved'
                AND NEW.module!='hr-notes'

                BEGIN

                    INSERT INTO sl_notifications (

                        user_id,
                        module,
                        record_id,
                        kind,
                        message,
                        created_at

                    )

                    SELECT

                        NEW.employee_id,
                        NEW.module,
                        NEW.record_id,
                        'approval',
                        'Your request has passed an approval stage.',
                        datetime('now')

                    FROM users

                    WHERE id=NEW.employee_id
                    AND status='Approved';

                END
            """)


        # =================================================
        # COMPANY ANNOUNCEMENTS
        # =================================================

        if sl_fix_table_exists(
            connection,
            "announcements"
        ):

            connection.execute("""
                CREATE TRIGGER IF NOT EXISTS
                sl_notification_announcement

                AFTER INSERT ON announcements

                BEGIN

                    INSERT INTO sl_notifications (

                        user_id,
                        module,
                        record_id,
                        kind,
                        message,
                        created_at

                    )

                    SELECT

                        id,
                        'announcements',
                        NEW.id,
                        'announcement',
                        'A new company announcement is available.',
                        datetime('now')

                    FROM users

                    WHERE status='Approved';

                END
            """)


        # =================================================
        # NEW EMPLOYEE REGISTRATION ALERTS
        # =================================================

        connection.execute("""
            CREATE TRIGGER IF NOT EXISTS
            sl_notification_registration

            AFTER INSERT ON users

            WHEN NEW.status='Pending'

            BEGIN

                INSERT INTO sl_notifications (

                    user_id,
                    module,
                    record_id,
                    kind,
                    message,
                    created_at

                )

                SELECT

                    id,
                    'registration',
                    NEW.id,
                    'registration',
                    'A new employee account requires HR approval.',
                    datetime('now')

                FROM users

                WHERE role='Admin'
                AND status='Approved';

            END
        """)

        connection.commit()

    except Exception:

        connection.rollback()

        raise


# =========================================================
# FIND REQUESTS AWAITING THE CURRENT USER'S APPROVAL
# =========================================================

def sl_fix_pending_approvals():

    if not g.user:
        return []

    uid = g.user["id"]

    connection = db()

    results = []

    for module, config in MODULES.items():

        if module in ("attendance", "hr-notes"):
            continue

        routed = module in SL_FIX_ROUTED

        if not admin() and not routed:
            continue

        table = config.get("table")

        if not table:
            continue

        if not sl_fix_table_exists(connection, table):
            continue

        columns = sl_fix_columns(
            connection,
            table
        )

        if not {
            "id", "user_id", "status"
        }.issubset(columns):

            continue

        sql = f"""

            SELECT

                r.id,
                r.user_id,
                r.status,
                u.name AS employee_name,

                COALESCE((

                    SELECT MAX(h.id)
                    FROM approval_history h

                    WHERE h.module=?
                    AND h.record_id=r.id

                ),0) AS history_version,

                COALESCE((

                    SELECT MAX(n.id)
                    FROM sl_notifications n

                    WHERE n.module=?
                    AND n.record_id=r.id

                ),0) AS record_version

            FROM {table} r

            JOIN users u ON u.id=r.user_id

            WHERE r.status IN (
                'Pending',
                'Open',
                'In Progress'
            )

        """

        args = [module, module]

        if table == "employee_records":

            sql += " AND r.module=?"

            args.append(module)

        for item in rows(sql, args):

            reviewer = None

            if routed:

                helper = globals().get(
                    "next_reviewer"
                )

                if helper:

                    reviewer = helper(

                        item["user_id"],
                        module,
                        item["id"]

                    )

            if admin():

                # HR receives requests at the HR stage.

                if reviewer is not None:
                    continue

            else:

                if reviewer != uid:
                    continue

                if item["user_id"] == uid:
                    continue

            key = (

                f"{module}:{item['id']}:"

                f"{item['history_version']}:"

                f"{item['record_version']}:"

                f"{item['status']}"

            )

            if admin() and "review_record" in app.view_functions:

                destination = url_for(

                    "review_record",

                    module=module,

                    record_id=item["id"]

                )

            elif "team_approvals" in app.view_functions:

                destination = url_for("team_approvals")

            else:

                destination = url_for("dashboard")

            results.append({

                "key": key,

                "employee": item["employee_name"],

                "title": config.get("title", module),

                "record_id": item["id"],

                "url": destination

            })

    seen = {

        item["notification_key"]

        for item in rows("""

            SELECT notification_key

            FROM sl_approval_notification_reads

            WHERE user_id=?

        """, (uid,))

    }

    for item in results:

        item["read"] = item["key"] in seen

    return results


# =========================================================
# UNREAD NOTIFICATION COUNTER
# =========================================================

@app.get("/portal-notifications/count")
@login_required
def sl_fix_notification_count():

    item = row("""

        SELECT COUNT(*) AS total

        FROM sl_notifications

        WHERE user_id=?

        AND read_at IS NULL

    """, (g.user["id"],))

    unread = item["total"]

    unread += sum(

        not item["read"]

        for item in sl_fix_pending_approvals()

    )

    return {

        "unread": unread

    }, 200, {

        "Cache-Control": "private, no-store"

    }


# =========================================================
# NOTIFICATION DESTINATION
# =========================================================

def sl_fix_destination(item):

    module = item["module"]

    if item["kind"] == "addressed":

        if "sl_my_inbox" in app.view_functions:

            return url_for("sl_my_inbox")

    if module == "registration" and admin():

        return url_for("workforce")

    if module == "announcements":

        if (
            "communication" in app.view_functions
            and allowed("announcements")
        ):

            return url_for("communication")

    if module in MODULES and allowed(module):

        return url_for(
            "module_page",
            module=module
        )

    if (
        module in SL_FIX_SHAREABLE
        and "sl_my_inbox" in app.view_functions
    ):

        return url_for("sl_my_inbox")

    return url_for("dashboard")


# =========================================================
# PERSONAL NOTIFICATION CENTRE
# =========================================================

@app.get("/portal-notifications")
@login_required
def sl_fix_notifications():

    uid = g.user["id"]

    page_number = max(

        1,

        request.args.get(
            "page",
            1,
            type=int
        )

    )

    events = []

    for item in rows("""

        SELECT *

        FROM sl_notifications

        WHERE user_id=?

        ORDER BY id DESC

        LIMIT 50 OFFSET ?

    """, (

        uid,

        (page_number - 1) * 50

    )):

        event = dict(item)

        event["title"] = MODULES.get(

            item["module"], {}

        ).get(

            "title",

            item["module"].replace("-", " ").title()

        )

        event["url"] = sl_fix_destination(item)

        events.append(event)

    approvals = sl_fix_pending_approvals()

    return page(

        "Notification Centre",

        """

        <div class="sl-fix-notification-heading">

            <div>

                <h1>Notifications</h1>

                <p class="muted">

                    Your requests, approvals and workplace updates.

                </p>

            </div>

            <form method="post"
                  action="{{ url_for('sl_fix_mark_read') }}">

                <input type="hidden"
                       name="csrf"
                       value="{{ csrf() }}">

                <input type="hidden"
                       name="mode"
                       value="all">

                <button class="btn btn-purple">

                    Mark All as Read

                </button>

            </form>

        </div>


        <div class="card">

            <h2>Approvals Requiring Your Attention</h2>

            <p class="muted">

                {{ approvals|length }} requests awaiting your decision

            </p>

            <div class="table-wrap">

                <table class="sl-fix-notification-table">

                    <thead>

                        <tr>

                            <th>Employee</th>

                            <th>Request</th>

                            <th>Notification</th>

                            <th>Actions</th>

                        </tr>

                    </thead>

                    <tbody>

                        {% for item in approvals %}

                        <tr class="{{
                            '' if item.read else 'sl-fix-unread'
                        }}">

                            <td>

                                {{ item.employee }}

                            </td>

                            <td>

                                <strong>{{ item.title }}</strong>

                                <small>#{{ item.record_id }}</small>

                            </td>

                            <td>

                                Your approval is required.

                                {% if not item.read %}

                                    <span class="sl-fix-new">

                                        New

                                    </span>

                                {% endif %}

                            </td>

                            <td>

                                <div class="inline">

                                    <a class="btn btn-small btn-purple"
                                       href="{{ item.url }}">

                                        Review Request

                                    </a>

                                    {% if not item.read %}

                                    <form method="post"
                                          action="{{ url_for(
                                              'sl_fix_mark_read'
                                          ) }}">

                                        <input type="hidden"
                                               name="csrf"
                                               value="{{ csrf() }}">

                                        <input type="hidden"
                                               name="mode"
                                               value="approval">

                                        <input type="hidden"
                                               name="key"
                                               value="{{ item.key }}">

                                        <button class="btn btn-light btn-small">

                                            Mark Read

                                        </button>

                                    </form>

                                    {% endif %}

                                </div>

                            </td>

                        </tr>

                        {% else %}

                        <tr>

                            <td colspan="4">

                                No pending approvals.

                            </td>

                        </tr>

                        {% endfor %}

                    </tbody>

                </table>

            </div>

        </div>


        <div class="card">

            <h2>Recent Notifications</h2>

            <div class="table-wrap">

                <table class="sl-fix-notification-table">

                    <thead>

                        <tr>

                            <th>Module</th>

                            <th>Notification</th>

                            <th>Date & Time (UTC)</th>

                            <th>Status</th>

                            <th>Actions</th>

                        </tr>

                    </thead>

                    <tbody>

                        {% for item in events %}

                        <tr class="{{
                            '' if item.read_at else 'sl-fix-unread'
                        }}">

                            <td>

                                <strong>{{ item.title }}</strong>

                                {% if item.record_id %}

                                    <small>#{{ item.record_id }}</small>

                                {% endif %}

                            </td>

                            <td>

                                {{ item.message }}

                            </td>

                            <td>

                                {{ item.created_at }}

                            </td>

                            <td>

                                {% if item.read_at %}

                                    Read

                                {% else %}

                                    <span class="sl-fix-new">

                                        New

                                    </span>

                                {% endif %}

                            </td>

                            <td>

                                <div class="inline">

                                    <a class="btn btn-small btn-purple"
                                       href="{{ item.url }}">

                                        Open

                                    </a>

                                    {% if not item.read_at %}

                                    <form method="post"
                                          action="{{ url_for(
                                              'sl_fix_mark_read'
                                          ) }}">

                                        <input type="hidden"
                                               name="csrf"
                                               value="{{ csrf() }}">

                                        <input type="hidden"
                                               name="mode"
                                               value="single">

                                        <input type="hidden"
                                               name="id"
                                               value="{{ item.id }}">

                                        <button class="btn btn-small btn-light">

                                            Mark Read

                                        </button>

                                    </form>

                                    {% endif %}

                                </div>

                            </td>

                        </tr>

                        {% else %}

                        <tr>

                            <td colspan="5">

                                No notifications yet.

                            </td>

                        </tr>

                        {% endfor %}

                    </tbody>

                </table>

            </div>

            <div class="sl-fix-pagination">

                {% if page_number > 1 %}

                    <a class="btn btn-light"
                       href="{{ url_for(
                           'sl_fix_notifications',
                           page=page_number-1
                       ) }}">

                        Previous

                    </a>

                {% endif %}

                <span>Page {{ page_number }}</span>

                {% if events|length == 50 %}

                    <a class="btn btn-light"
                       href="{{ url_for(
                           'sl_fix_notifications',
                           page=page_number+1
                       ) }}">

                        Next

                    </a>

                {% endif %}

            </div>

        </div>

        """,

        events=events,

        approvals=approvals,

        page_number=page_number

    )


# =========================================================
# MARK NOTIFICATIONS AS READ
# =========================================================

@app.post("/portal-notifications/read")
@login_required
def sl_fix_mark_read():

    import secrets

    submitted = request.form.get("csrf", "")

    expected = session.get("csrf", "")

    if not expected or not secrets.compare_digest(
        submitted,
        expected
    ):

        abort(403)

    uid = g.user["id"]

    mode = request.form.get("mode", "")

    if mode not in (
        "all", "single", "approval"
    ):

        abort(400)

    connection = db()

    try:

        connection.execute("BEGIN IMMEDIATE")

        if mode == "all":

            connection.execute("""

                UPDATE sl_notifications

                SET read_at=?

                WHERE user_id=?
                AND read_at IS NULL

            """, (

                stamp(),

                uid

            ))

        elif mode == "single":

            notification_id = request.form.get(
                "id",
                type=int
            )

            connection.execute("""

                UPDATE sl_notifications

                SET read_at=?

                WHERE id=?
                AND user_id=?

            """, (

                stamp(),

                notification_id,

                uid

            ))

        if mode in ("all", "approval"):

            key = request.form.get("key", "")

            for item in sl_fix_pending_approvals():

                if mode == "all" or item["key"] == key:

                    connection.execute("""

                        INSERT OR IGNORE INTO
                        sl_approval_notification_reads

                        (
                            user_id,
                            notification_key,
                            read_at
                        )

                        VALUES (?,?,?)

                    """, (

                        uid,

                        item["key"],

                        stamp()

                    ))

        connection.commit()

    except Exception:

        connection.rollback()

        raise

    return redirect(
        url_for("sl_fix_notifications")
    )


# =========================================================
# PERMANENT NOTIFICATION BELL
# =========================================================

SL_FIX_BELL = r"""

{% if g.user %}

<a

    href="{{ url_for('sl_fix_notifications') }}"

    id="sl-fix-bell"

    class="sl-fix-bell"

    title="Notifications"

    aria-label="Notifications"

    data-url="{{ url_for('sl_fix_notification_count') }}"

>

    <svg

        width="23"

        height="23"

        viewBox="0 0 24 24"

        fill="none"

        stroke="currentColor"

        stroke-width="1.8"

        stroke-linecap="round"

        stroke-linejoin="round"

        aria-hidden="true"

    >

        <path d="
            M18 8a6 6 0 0 0-12 0
            c0 7-3 7-3 9h18
            c0-2-3-2-3-9
        "/>

        <path d="M10 21h4"/>

    </svg>

    <span

        id="sl-fix-counter"

        class="sl-fix-counter"

        hidden

    >

        0

    </span>

</a>

{% endif %}

"""


# =========================================================
# PROFESSIONAL LIGHT AND DARK MODE DESIGN
# =========================================================

SL_FIX_STYLE = """

<style>

.sl-fix-bell {

    position: relative;

    display: inline-flex !important;

    align-items: center;

    justify-content: center;

    width: 44px;

    height: 44px;

    min-width: 44px;

    border: 1px solid #ffffff35;

    border-radius: 50%;

    background: #ffffff12;

    color: #ffffff !important;

    text-decoration: none;

    flex-shrink: 0;

}

.sl-fix-bell:hover {

    background: #ffffff30;

}

.sl-fix-counter {

    position: absolute;

    top: -5px;

    right: -5px;

    min-width: 21px;

    height: 21px;

    padding: 0 5px;

    border: 2px solid #30254f;

    border-radius: 15px;

    background: #dc3545;

    color: #ffffff;

    font-size: 11px;

    font-weight: 700;

    display: flex;

    align-items: center;

    justify-content: center;

}

.sl-fix-counter[hidden] {

    display: none !important;

}

/* Prevent duplicate older notification bells. */

#sl-notification-bell {

    display: none !important;

}

.sl-fix-notification-heading {

    display: flex;

    justify-content: space-between;

    align-items: center;

    flex-wrap: wrap;

    gap: 15px;

    margin-bottom: 24px;

}

.sl-fix-notification-heading h1 {

    margin-bottom: 8px;

}

.sl-fix-notification-table {

    width: 100%;

    min-width: 680px;

    border-collapse: collapse;

}

html body .sl-fix-notification-table th {

    padding: 14px !important;

    text-align: left;

    background: #f0edf5;

    color: #30254f;

}

html body .sl-fix-notification-table td {

    padding: 15px !important;

    vertical-align: middle !important;

    white-space: normal !important;

    line-height: 1.5 !important;

    border-bottom: 1px solid #e4e1ea;

}

.sl-fix-notification-table td small {

    display: block;

    margin-top: 5px;

}

.sl-fix-notification-table td .inline {

    display: flex;

    flex-wrap: wrap;

    align-items: center;

    gap: 7px;

}

html body .sl-fix-notification-table tr.sl-fix-unread {

    background: #f1edf8 !important;

}

.sl-fix-new {

    display: inline-block;

    padding: 5px 10px;

    margin: 4px;

    border-radius: 20px;

    color: #624b8b;

    background: #e6dcf3;

    font-size: 11px;

    font-weight: 700;

}

.sl-fix-pagination {

    display: flex;

    justify-content: flex-end;

    align-items: center;

    gap: 12px;

    margin-top: 20px;

}

html body.dark .sl-fix-notification-table th {

    background: #302b40 !important;

    color: #f1eef7 !important;

}

html body.dark .sl-fix-notification-table td {

    border-color: #494358;

    color: #f1eef7;

}

html body.dark .sl-fix-notification-table tr.sl-fix-unread {

    background: #30283f !important;

}

body.dark .sl-fix-new {

    background: #493669;

    color: #e8ddff;

}


/* GLOBAL TABLE ALIGNMENT */

html body .table-wrap {

    overflow-x: auto;

    max-width: 100%;

}

html body table th,

html body table td {

    vertical-align: middle !important;

    white-space: normal !important;

    line-height: 1.5 !important;

    height: auto !important;

}

html body table td {

    padding: 14px 16px !important;

}

html body table td .prewrap {

    white-space: normal !important;

}

html body table tbody tr {

    height: auto !important;

    min-height: 0 !important;

}

@media(max-width:650px) {

    .sl-fix-bell {

        width: 34px;

        height: 34px;

        min-width: 34px;

    }

    .sl-fix-bell svg {

        width: 19px;

        height: 19px;

    }

    .sl-fix-notification-heading {

        align-items: flex-start;

    }

}

</style>

"""


# =========================================================
# AUTOMATIC NOTIFICATION REFRESH
# AND DOCUMENT TABLE CORRECTION
# =========================================================

SL_FIX_SCRIPT = r"""

<script>

(() => {

    // =================================================
    // REMOVE DOCUMENT COPY COLUMN FROM UPLOAD TABLES
    // =================================================

    document.querySelectorAll(

        ".sl-document-table, " +

        ".identity-upload-section table, " +

        ".sl-document-section table"

    ).forEach(table => {

        const headings = Array.from(

            table.querySelectorAll("thead th")

        );

        const index = headings.findIndex(

            heading =>

                heading.textContent.trim().toLowerCase()

                === "document copy"

        );

        if (index === -1) return;

        Array.from(table.rows).forEach(row => {

            if (row.cells.length > index) {

                row.deleteCell(index);

            }

        });

    });


    // =================================================
    // CORRECT RECIPIENT BUTTON TEXT
    // =================================================

    const walker = document.createTreeWalker(

        document.body,

        NodeFilter.SHOW_TEXT

    );

    let node;

    while ((node = walker.nextNode())) {

        const value = node.textContent.trim();

        if (

            value.toLowerCase() === "select more people"

            ||

            value.toLowerCase() === "select employees"

        ) {

            node.textContent = node.textContent.replace(

                /select more people|select employees/ig,

                "Select People"

            );

        }

    }


    // =================================================
    // NOTIFICATION BELL
    // =================================================

    const bell = document.getElementById(
        "sl-fix-bell"
    );

    const counter = document.getElementById(
        "sl-fix-counter"
    );

    if (!bell || !counter) return;

    let loading = false;


    async function refreshNotifications() {

        if (loading || document.hidden) return;

        loading = true;

        try {

            const response = await fetch(

                bell.dataset.url,

                {

                    credentials: "same-origin",

                    cache: "no-store"

                }

            );

            if (!response.ok || response.redirected) {

                return;

            }

            const result = await response.json();

            const unread = Number(result.unread);

            if (!Number.isFinite(unread)) return;

            counter.textContent = (

                unread > 99

                ? "99+"

                : String(unread)

            );

            counter.hidden = unread === 0;

            bell.title = unread + " unread notifications";

            bell.setAttribute(

                "aria-label",

                unread + " unread notifications"

            );

        } catch (error) {

            console.warn(
                "Notification refresh failed.",
                error
            );

        } finally {

            loading = false;

        }

    }

    refreshNotifications();

    setInterval(

        refreshNotifications,

        30000

    );

    document.addEventListener(

        "visibilitychange",

        refreshNotifications

    );

})();

</script>

"""


# =========================================================
# REMOVE OLD BELL AND INSTALL THE NEW HEADER
# =========================================================

old_bell = globals().get("SL_NOTIFICATION_BELL")

if isinstance(old_bell, str):

    LAYOUT = LAYOUT.replace(old_bell, "")

old_script = globals().get(
    "SL_NOTIFICATION_SCRIPT"
)

if isinstance(old_script, str):

    LAYOUT = LAYOUT.replace(old_script, "")

header_anchor = '<div class="header-right">'

if header_anchor not in LAYOUT:

    raise RuntimeError(
        "STAR LINK header could not be located."
    )

LAYOUT = LAYOUT.replace(

    header_anchor,

    header_anchor + SL_FIX_BELL,

    1

)

LAYOUT = LAYOUT.replace(

    "</head>",

    SL_FIX_STYLE + "</head>",

    1

)

LAYOUT = LAYOUT.replace(

    "</body>",

    SL_FIX_SCRIPT + "</body>",

    1

)





# STAR LINK APPROVAL ERROR FIX V2

from werkzeug.exceptions import BadRequest


# =========================================================
# TEMPLATE HELPERS
# =========================================================

def sl_repair_endpoints():

    return app.view_functions.keys()


app.jinja_env.globals.update(

    sl_repair_endpoints=sl_repair_endpoints

)


# =========================================================
# VALIDATE APPROVERS BEFORE SAVING A REQUEST
# =========================================================

_sl_repair_previous_insert = sl_insert_request


def sl_insert_request(module, table, data):

    routed_modules = globals().get(
        "SL_APPROVAL_MODULES",
        globals().get("ROUTED", ())
    )

    if module not in routed_modules:

        return _sl_repair_previous_insert(
            module,
            table,
            data
        )

    uid = data["user_id"]

    employee = row("""

        SELECT id,name,role,status

        FROM users

        WHERE id=?

    """, (uid,))

    if not employee:

        abort(
            400,
            description="Please select a valid employee."
        )

    if employee["status"] != "Approved":

        abort(
            400,
            description="The employee account is not active."
        )

    if not admin() and uid != g.user["id"]:

        abort(403)


    # =====================================================
    # READ SELECTED APPROVERS
    # =====================================================

    submitted = [

        value

        for value in request.form.getlist("sl_reviewer")

        if value.strip()

    ]

    try:

        selected = [
            int(value)
            for value in submitted
        ]

    except (ValueError, TypeError):

        abort(
            400,
            description="Please select valid approvers."
        )

    if len(selected) > 10:

        abort(
            400,
            description="Select a maximum of 10 approvers."
        )

    if len(selected) != len(set(selected)):

        abort(
            400,
            description="The same approver cannot be selected twice."
        )

    if uid in selected:

        abort(
            400,
            description="Employees cannot approve their own requests."
        )


    # =====================================================
    # VALIDATE AUTHORIZED REVIEWERS
    # =====================================================

    if admin():

        candidates = sl_form_approvers(module)

    else:

        candidates = sl_eligible_people(
            uid,
            module
        )

    allowed_ids = {

        person["id"]

        for person in candidates

        if person["id"] != uid

    }

    if any(
        reviewer not in allowed_ids
        for reviewer in selected
    ):

        abort(
            403,
            description="An unauthorized approver was selected."
        )


    # =====================================================
    # CHECK MANDATORY APPROVAL STAGES
    # =====================================================

    mandatory = []

    required_helper = globals().get(
        "sl_required_reviewers"
    )

    if required_helper:

        mandatory = required_helper(
            uid,
            module
        )

    default_helper = globals().get(
        "sl_default_chain"
    )

    defaults = (

        default_helper(uid, module)

        if default_helper

        else []

    )

    final_reviewers = list(dict.fromkeys(

        mandatory + (selected or defaults)

    ))

    if len(final_reviewers) > 10:

        abort(

            400,

            description=(
                "The request exceeds the maximum "
                "of 10 approval stages."
            )

        )

    if uid in final_reviewers:

        abort(

            400,

            description=(
                "Your approval configuration contains "
                "a self-approval assignment. Contact HR."
            )

        )

    if any(

        reviewer not in allowed_ids

        for reviewer in final_reviewers

    ):

        abort(

            400,

            description=(
                "A configured reviewer is inactive or "
                "unavailable. HR must update the approval route."
            )

        )


    # =====================================================
    # PRESERVE ORIGINAL APPROVAL AND NOTIFICATION SYSTEM
    # =====================================================

    return _sl_repair_previous_insert(

        module,

        table,

        data

    )


# =========================================================
# REPLACE GENERIC ERROR 400 ON REQUEST SUBMISSION
# =========================================================

_sl_repair_previous_module_page = app.view_functions[
    "module_page"
]


@login_required
def sl_repaired_module_page(module):

    try:

        return _sl_repair_previous_module_page(
            module
        )

    except BadRequest as error:

        if request.method != "POST":

            raise

        db().rollback()

        message = str(error.description or "")

        if message == BadRequest().description:

            message = (

                "Unable to submit this request. "
                "Please complete all required fields "
                "and confirm that the employee and "
                "approval configuration are valid."

            )

        flash(message, "error")

        return redirect(

            url_for(

                "module_page",

                module=module,

                new=1

            )

        )


app.view_functions[
    "module_page"
] = sl_repaired_module_page


# =========================================================
# PROFESSIONAL APPROVAL FORM STYLING
# =========================================================

SL_APPROVAL_REPAIR_STYLE = """

<style>

.sl-approver-section {

    background: #f8f7fb;

    border: 1px solid #dedce6;

    border-radius: 10px;

    padding: 20px;

    margin: 20px 0;

}

.sl-approver-section h2 {

    font-size: 18px;

    margin: 0 0 12px;

}

.sl-approver-section p {

    font-size: 13px;

    line-height: 1.6;

    margin: 8px 0 14px;

}

.sl-approver-row {

    display: flex;

    align-items: center;

    flex-wrap: wrap;

    gap: 12px;

    margin-bottom: 12px;

}

.sl-approver-row select {

    flex: 1;

    width: 100%;

    min-width: 0;

    min-height: 44px;

    padding: 10px;

    border-radius: 7px;

}

body.dark .sl-approver-section {

    background: #272433;

    color: #f1eef7;

    border-color: #494358;

}

body.dark .sl-approver-section select {

    background: #191823 !important;

    color: #f1eef7 !important;

    border-color: #494358 !important;

}

@media(max-width:650px) {

    .sl-approver-row {

        flex-direction: column;

        align-items: stretch;

    }

    .sl-approver-section {

        padding: 15px;

    }

}

</style>

"""

LAYOUT = LAYOUT.replace(

    "</head>",

    SL_APPROVAL_REPAIR_STYLE + "</head>",

    1

)



with app.app_context():
    init_db()


# =========================================================
# START STAR LINK WEBSITE
# =========================================================

if __name__ == "__main__":

    if "--reset-admin-password" in sys.argv:

        with app.app_context():
            reset_admin_password()

        sys.exit(0)

    print()
    print("=" * 65)
    print("       STAR LINK ENTERPRISE EMPLOYEE PORTAL")
    print("=" * 65)
    print("Website: http://127.0.0.1:5000")
    print("HR Email:", ADMIN_EMAIL)
    print("Database:", DATABASE)
    print("HR Add Employee: Enabled")
    print("Employee Access Control: Enabled")
    print("Employee Password Reset: Enabled")
    print("Individual Permissions: Enabled")
    print("Document Management: Enabled")
    print("Attendance Management: Enabled")
    print("Ticketing System: Enabled")
    print("Workforce Management: Enabled")
    print("HR Analytics: Enabled")
    print("Company Reports: Enabled")

    if LOGO_FILE.exists():
        print("STAR LINK Logo: Loaded Successfully")
    else:
        print("Logo missing: star_link_logo.png")

    print("=" * 65)
    print()

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False
    )
