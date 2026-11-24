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

    if module in MODULES:

        if MODULES[module]["hr_only"]:
            return ()

        return MODULES[module]["actions"]

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
{% if f.kind=='number' %}min="0" step="0.01"{% endif %}>

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

<div class="user-details">
<strong>{{ g.user.name }}</strong>
<small>
{{ 'HR Administrator' if admin() else 'Employee' }}
</small>
</div>

<details class="account-menu">

<summary>
<div class="avatar">{{ g.user.name[0]|upper }}</div>
</summary>

<div class="account-dropdown">

{% if allowed('profile') and not g.user.must_change_password %}
<a href="{{ url_for('profile') }}">My Profile</a>
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

    current = {}

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
    All permissions are disabled by default.
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

    if module == "attendance" and action == "edit":
        return False
    if not allowed(module, action):
        return False

    if admin():
        return True

    if item["user_id"] != g.user["id"]:
        return False

    if module == "attendance":
        return False

    if item["status"] not in {"Pending", "Open"}:
        return False

    if module == "tasks":

        if item["created_by"] != g.user["id"]:
            return False

    return True


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

            if admin():
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

            record_id = insert(table, data)

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

        if table == "employee_records" and admin():

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
    <td>{{ badge(item.status) }}</td>
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

    item = owned_record(module, record_id, "edit")

    if request.method == "POST":

        data = validate_fields(config["fields"])

        validate_record(module, data)

        if module == "attendance":
            data["check_out"] = data.get("check_out") or None

        if module == "documents":
            data["status"] = "Pending"

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

            sql += " AND user_id=? AND status IN ('Pending','Open')"
            args.append(g.user["id"])

            if module == "tasks":

                sql += " AND created_by=?"
                args.append(g.user["id"])

        try:

            cursor = db().execute(sql, args)

            if not cursor.rowcount:

                db().rollback()
                abort(409)

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
    {{ input_field(f,item[f.name]) }}
    {% endfor %}

    </div>

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

    item = owned_record(module, record_id, "delete")

    table = config["table"]

    sql = f"DELETE FROM {table} WHERE id=?"

    args = [record_id]

    if table == "employee_records":

        sql += " AND module=?"
        args.append(module)

    if not admin():

        sql += " AND user_id=? AND status IN ('Pending','Open')"
        args.append(g.user["id"])

        if module == "tasks":

            sql += " AND created_by=?"
            args.append(g.user["id"])

    cursor = db().execute(sql, args)

    if not cursor.rowcount:

        db().rollback()
        abort(409)

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
            "Light", "Soft", "Contrast"
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
