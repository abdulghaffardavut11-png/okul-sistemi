import sqlite3
import re

from flask import current_app, g


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS navigation_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    icon TEXT NOT NULL DEFAULT 'circle',
    url_endpoint TEXT NOT NULL,
    route_arg TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    display_order INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_navigation_items_order
    ON navigation_items (category, display_order, id);
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    grade_level TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS teachers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    subject TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS translations (
    language TEXT NOT NULL,
    source TEXT NOT NULL,
    text TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (language, source)
);
CREATE TABLE IF NOT EXISTS dashboard_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL DEFAULT 'block' CHECK (kind IN ('announcement', 'block')),
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    link_label TEXT NOT NULL DEFAULT '',
    link_url TEXT NOT NULL DEFAULT '',
    tone TEXT NOT NULL DEFAULT 'info' CHECK (tone IN ('info', 'success', 'warning', 'danger')),
    audience TEXT NOT NULL DEFAULT 'all',
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    display_order INTEGER NOT NULL DEFAULT 0,
    starts_on TEXT NOT NULL DEFAULT '',
    ends_on TEXT NOT NULL DEFAULT '',
    created_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    code TEXT NOT NULL UNIQUE,
    teacher_id INTEGER NOT NULL REFERENCES teachers(id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS enrollments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    academic_year INTEGER NOT NULL CHECK (academic_year >= 2000),
    UNIQUE (student_id, course_id, academic_year)
);
CREATE TABLE IF NOT EXISTS grades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    enrollment_id INTEGER NOT NULL REFERENCES enrollments(id) ON DELETE CASCADE,
    score REAL NOT NULL CHECK (score >= 0 AND score <= 100),
    graded_on TEXT NOT NULL,
    exam_name TEXT NOT NULL DEFAULT 'Exam'
);
CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    enrollment_id INTEGER NOT NULL REFERENCES enrollments(id) ON DELETE CASCADE,
    attendance_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('Present', 'Absent', 'Late', 'Excused')),
    UNIQUE (enrollment_id, attendance_date)
);
CREATE TABLE IF NOT EXISTS homework (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    due_date TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS timetable (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    room TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS announcements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    published_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS service_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module TEXT NOT NULL CHECK (module IN (
        'orders', 'printer-requests', 'design-requests', 'tv-content',
        'bulk-sms', 'tutoring', 'parent-meetings'
    )),
    title TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'Open'
      CHECK (status IN ('Open', 'In progress', 'Complete')),
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS staff_updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL CHECK (category IN ('newsletter', 'student_log', 'meeting', 'update')),
    student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    scheduled_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS parent_meetings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    meeting_date TEXT NOT NULL,
    notes TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS health_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    report_type TEXT NOT NULL CHECK (report_type IN ('health', 'incident', 'emergency')),
    reported_at TEXT NOT NULL,
    details TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS boarding_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    academic_year INTEGER NOT NULL CHECK (academic_year >= 2000),
    residency TEXT NOT NULL CHECK (residency IN ('Boarding', 'Day')),
    room TEXT NOT NULL DEFAULT '',
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (student_id, academic_year)
);
"""

USER_SCHEMA = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'manager'
      CHECK (role IN (
        'super_admin', 'admin', 'manager', 'teacher', 'student', 'parent',
        'health_officer', 'boarding_master'
      )),
    email TEXT UNIQUE,
    teacher_id INTEGER REFERENCES teachers(id) ON DELETE CASCADE,
    student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
)
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def _migrate_users(db):
    columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
    if not columns:
        db.executescript(USER_SCHEMA)
        return
    table_sql = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()["sql"]
    if (
        "email" in columns
        and "teacher_id" in columns
        and "'admin'" in table_sql
        and "'health_officer'" in table_sql
        and "'boarding_master'" in table_sql
    ):
        return

    db.commit()
    db.execute("PRAGMA foreign_keys = OFF")
    db.execute("PRAGMA legacy_alter_table = ON")
    db.execute("ALTER TABLE users RENAME TO users_legacy")
    db.executescript(USER_SCHEMA)
    old_columns = {row["name"] for row in db.execute("PRAGMA table_info(users_legacy)")}
    legacy_roles = re.sub(r"\s+", "", table_sql.lower())
    legacy_admin_is_root = "check(rolein('admin'))" in legacy_roles
    role_expr = (
        (
            "CASE WHEN role='admin' THEN 'super_admin' "
            "WHEN role IN ('super_admin','manager','teacher','student','parent',"
            "'health_officer','boarding_master') "
            "THEN role ELSE 'manager' END"
            if legacy_admin_is_root
            else "CASE WHEN role IN "
            "('super_admin','admin','manager','teacher','student','parent',"
            "'health_officer','boarding_master') "
            "THEN role ELSE 'manager' END"
        )
        if "role" in old_columns
        else "'super_admin'"
    )
    extra_columns = []
    for column in ("email", "teacher_id", "student_id", "is_active"):
        if column in old_columns:
            extra_columns.append(column)
    insert_columns = ["id", "username", "password_hash", "role"] + extra_columns
    select_columns = ["id", "username", "password_hash", role_expr] + extra_columns
    db.execute(
        f"INSERT INTO users ({', '.join(insert_columns)}) "
        f"SELECT {', '.join(select_columns)} FROM users_legacy"
    )
    db.execute("DROP TABLE users_legacy")
    db.commit()
    db.execute("PRAGMA legacy_alter_table = OFF")
    db.execute("PRAGMA foreign_keys = ON")


def _migrate_attendance_status(db):
    table = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='attendance'"
    ).fetchone()
    if table is None or "'Excused'" in table["sql"]:
        return

    db.commit()
    db.execute("PRAGMA foreign_keys = OFF")
    db.execute(
        "CREATE TABLE attendance_new ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "enrollment_id INTEGER NOT NULL REFERENCES enrollments(id) ON DELETE CASCADE,"
        "attendance_date TEXT NOT NULL,"
        "status TEXT NOT NULL CHECK (status IN ('Present','Absent','Late','Excused')),"
        "UNIQUE (enrollment_id,attendance_date))"
    )
    db.execute(
        "INSERT INTO attendance_new (id,enrollment_id,attendance_date,status) "
        "SELECT id,enrollment_id,attendance_date,status FROM attendance"
    )
    db.execute("DROP TABLE attendance")
    db.execute("ALTER TABLE attendance_new RENAME TO attendance")
    db.commit()
    db.execute("PRAGMA foreign_keys = ON")


def init_db():
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            grade_level TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS teachers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            subject TEXT NOT NULL
        );
        """
    )
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'manager' CHECK (role IN ('super_admin', 'admin', 'manager', 'teacher', 'student', 'parent', 'health_officer', 'boarding_master')),
            email TEXT UNIQUE,
            teacher_id INTEGER REFERENCES teachers(id) ON DELETE CASCADE,
            student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
        );
        """
    )
    _migrate_users(db)
    db.executescript(SCHEMA)
    _migrate_attendance_status(db)
    _add_columns(
        db,
        "students",
        {
            "national_id": "TEXT NOT NULL DEFAULT ''",
            "gender": "TEXT NOT NULL DEFAULT ''",
            "father_name": "TEXT NOT NULL DEFAULT ''",
            "father_phone": "TEXT NOT NULL DEFAULT ''",
            "father_occupation": "TEXT NOT NULL DEFAULT ''",
            "mother_name": "TEXT NOT NULL DEFAULT ''",
            "mother_phone": "TEXT NOT NULL DEFAULT ''",
            "mother_occupation": "TEXT NOT NULL DEFAULT ''",
            "home_address": "TEXT NOT NULL DEFAULT ''",
            "dormitory_info": "TEXT NOT NULL DEFAULT ''",
            "profile_picture": "TEXT NOT NULL DEFAULT ''",
        },
    )
    _add_columns(
        db,
        "teachers",
        {
            "phone": "TEXT NOT NULL DEFAULT ''",
            "home_address": "TEXT NOT NULL DEFAULT ''",
            "profile_picture": "TEXT NOT NULL DEFAULT ''",
        },
    )
    _add_columns(
        db,
        "grades",
        {"exam_name": "TEXT NOT NULL DEFAULT 'Exam'"},
    )
    _add_columns(
        db,
        "staff_updates",
        {"scheduled_at": "TEXT"},
    )
    _add_columns(
        db,
        "announcements",
        {
            "period": "TEXT NOT NULL DEFAULT 'weekly'",
            "expires_at": "TEXT NOT NULL DEFAULT ''",
            "expiry_custom": "INTEGER NOT NULL DEFAULT 0",
            "archived_at": "TEXT NOT NULL DEFAULT ''",
            "archive_reason": "TEXT NOT NULL DEFAULT ''",
        },
    )
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS summer_school_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            academic_year TEXT NOT NULL,
            student_type TEXT NOT NULL CHECK (student_type IN ('internal', 'external')),
            student_id INTEGER REFERENCES students(id) ON DELETE SET NULL,
            full_name TEXT NOT NULL,
            gender TEXT NOT NULL DEFAULT '',
            father_name TEXT NOT NULL DEFAULT '',
            father_phone TEXT NOT NULL DEFAULT '',
            mother_name TEXT NOT NULL DEFAULT '',
            mother_phone TEXT NOT NULL DEFAULT '',
            home_address TEXT NOT NULL DEFAULT '',
            school_of_origin TEXT NOT NULL DEFAULT '',
            archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_summer_school_year_archive
            ON summer_school_records (academic_year, archived);
        CREATE TABLE IF NOT EXISTS staff_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL CHECK (category IN ('newsletter', 'student_log', 'meeting', 'update')),
            student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            scheduled_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_staff_updates_category_created
            ON staff_updates (category, created_at);
        """
    )
    db.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
        ("school_name", "WISE HOUSE INTERNATIONAL SCHOOL"),
    )
    for key, value in (
        ("academic_year", "2026-2027"),
        ("primary_color", "#1758a6"),
        ("login_background", "#f1f5f9"),
        ("language", "en"),
        ("landing_title", "A connected community. A brighter future."),
        ("landing_text", "Welcome to our school community."),
        ("landing_image_filename", ""),
        ("ai_enabled", "0"),
    ):
        db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
    _repair_class_names(db)
    db.commit()
    current_app.teardown_appcontext(close_db)


def _repair_class_names(db):
    """One-time: a class saved as name "grade" + code "4" becomes "Grade 4" (names were all identical)."""
    if db.execute("SELECT 1 FROM settings WHERE key='migration_class_names_v1'").fetchone():
        return
    generic = {"grade", "class", "sınıf", "sinif", "level"}
    for row in db.execute("SELECT id,name,code FROM courses").fetchall():
        name = (row["name"] or "").strip()
        code = (row["code"] or "").strip()
        if name.lower() in generic and code and len(code) <= 3 and code.isalnum():
            db.execute(
                "UPDATE courses SET name=? WHERE id=?",
                (f"{name.capitalize()} {code.upper()}", row["id"]),
            )
    db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('migration_class_names_v1', 'done')")


def _add_columns(db, table, columns):
    existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
    for name, declaration in columns.items():
        if name not in existing:
            try:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
            except sqlite3.OperationalError as error:
                # Two gunicorn workers can start together; the loser sees the column already added.
                if "duplicate column" not in str(error).lower():
                    raise
