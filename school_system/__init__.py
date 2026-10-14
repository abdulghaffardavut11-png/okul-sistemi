import os
import secrets

from datetime import date

from flask import Flask, session, url_for

from .db import get_db, init_db


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
        DATABASE=os.path.join(app.instance_path, "school.sqlite3"),
        UPLOAD_FOLDER=os.environ.get("UPLOAD_FOLDER", os.path.join(app.instance_path, "uploads")),
        SITE_URL=os.environ.get("SITE_URL", "http://localhost:5000").rstrip("/"),
        AI_API_URL=os.environ.get("AI_API_URL", "https://api.openai.com/v1/chat/completions"),
        AI_API_KEY=os.environ.get("AI_API_KEY", ""),
        AI_MODEL=os.environ.get("AI_MODEL", "gpt-4o-mini"),
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        WTF_CSRF_ENABLED=True,
    )
    if os.environ.get("FLASK_ENV") == "production":
        if not os.environ.get("SECRET_KEY"):
            raise RuntimeError("SECRET_KEY must be configured in production.")
        app.config["SESSION_COOKIE_SECURE"] = True
        app.config["SESSION_COOKIE_HTTPONLY"] = True
        app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    if test_config:
        app.config.update(test_config)
        if test_config.get("TESTING"):
            app.config["WTF_CSRF_ENABLED"] = False

    os.makedirs(app.instance_path, exist_ok=True)

    with app.app_context():
        init_db()

    from .auth import bp as auth_bp
    from .views import bp as views_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(views_bp)
    from flask_wtf.csrf import CSRFProtect

    CSRFProtect(app)

    @app.get("/healthz")
    def health_check():
        from .db import get_db

        get_db().execute("SELECT 1")
        return {"status": "ok"}, 200

    from .views import (
        INDEX_LAYOUTS,
        LANGUAGES,
        MODULE_DEFAULTS,
        NAVIGATION_TARGETS,
        PORTAL_GROUPS,
        RESOURCES,
        translate,
    )

    with app.app_context():
        db = get_db()
        navigation_seeded = db.execute(
            "SELECT value FROM settings WHERE key='navigation_seeded'"
        ).fetchone()
        if not navigation_seeded:
            order = 0
            for category, items in PORTAL_GROUPS:
                for title, icon, endpoint, *args in items:
                    route_arg = args[0] if args else ""
                    db.execute(
                        "INSERT INTO navigation_items "
                        "(category,title,icon,url_endpoint,route_arg,is_active,display_order) "
                        "VALUES (?,?,?,?,?,1,?)",
                        (category, title, icon, endpoint, route_arg, order),
                    )
                    order += 1
            db.execute(
                "INSERT INTO settings (key,value) VALUES ('navigation_seeded','1')"
            )
            db.commit()

    @app.context_processor
    def inject_resources():
        from .db import get_db

        setting = get_db().execute(
            "SELECT value FROM settings WHERE key = ?", ("school_name",)
        ).fetchone()
        all_settings = {
            row["key"]: row["value"]
            for row in get_db().execute("SELECT key,value FROM settings").fetchall()
        }
        enabled_modules = {
            name: all_settings.get(
                "module_" + name, "1" if enabled else "0"
            ) == "1"
            for name, enabled in MODULE_DEFAULTS.items()
        }
        index_layout = all_settings.get("index_layout", "modern_grid")
        index_layout = {
            "compact_list": "classic_list",
        }.get(index_layout, index_layout)
        if index_layout not in INDEX_LAYOUTS:
            index_layout = "modern_grid"
        language = session.get("language") or all_settings.get("language", "en")
        configured_academic_year = all_settings.get("academic_year", "2026-2027")
        try:
            academic_year_start = int(configured_academic_year[:4])
        except ValueError:
            academic_year_start = date.today().year
        role = session.get("role")
        visible_groups = {}
        visible_nav_items = []
        navigation_items = get_db().execute(
            "SELECT category,title,icon,url_endpoint,route_arg,is_active,display_order "
            "FROM navigation_items WHERE is_active=1 ORDER BY display_order,id"
        ).fetchall()
        for item in navigation_items:
            route_arg = item["route_arg"]
            target_key = (
                f"{item['url_endpoint']}:{route_arg}"
                if route_arg else item["url_endpoint"]
            )
            if target_key not in NAVIGATION_TARGETS:
                continue
            endpoint = item["url_endpoint"]
            args = [route_arg] if route_arg else []
            title = item["title"]
            icon = item["icon"]
            visible = []
            if endpoint == "views.users" and role != "super_admin":
                continue
            if endpoint == "views.manage_classes" and role != "super_admin":
                continue
            if endpoint == "views.student_registration" and role not in {
                "super_admin", "manager", "admin"
            }:
                continue
            if endpoint in {
                "views.parent_meetings",
                "views.health_reports",
            } and role not in {"super_admin", "manager", "admin"}:
                continue
            if endpoint == "views.boarding_management" and role not in {
                "super_admin", "manager", "admin", "boarding_master"
            }:
                continue
            if endpoint == "views.portal" and role == "admin" and args[0] == "bulk-sms":
                visible.append(
                    {"label": title, "icon": icon, "endpoint": endpoint, "module": args[0]}
                )
            elif endpoint == "views.settings" and role != "super_admin":
                continue
            elif endpoint == "views.academic_year_settings" and role != "super_admin":
                continue
            elif endpoint == "views.export_students" and role not in {"super_admin", "manager", "admin"}:
                continue
            elif endpoint == "views.predica" and role not in {"super_admin", "manager", "admin", "teacher"}:
                continue
            elif endpoint in {"views.summer_school", "views.summer_school_archive"} and role not in {"super_admin", "admin"}:
                continue
            elif endpoint == "views.timetable" and role not in {"super_admin", "manager", "admin", "teacher"}:
                continue
            elif endpoint == "views.manage":
                resource = args[0]
                if resource == "attendance" and not enabled_modules["attendance"]:
                    continue
                if resource == "teachers" and not enabled_modules["teachers"]:
                    continue
                if role not in {"super_admin", "manager", "admin", "teacher"}:
                    continue
                if role == "teacher" and resource not in {"students", "courses", "grades", "attendance"}:
                    continue
                if role in {"student", "parent"}:
                    continue
                visible.append(
                    {"label": title, "icon": icon, "endpoint": endpoint, "resource": resource}
                )
            elif endpoint == "views.attendance_roster" and not enabled_modules["attendance"]:
                continue
            elif endpoint in {"views.summer_school", "views.summer_school_archive"} and not enabled_modules["summer_school"]:
                continue
            elif endpoint == "views.boarding_management" and not enabled_modules["boarding"]:
                continue
            elif endpoint == "views.portal":
                module = args[0]
                if role not in {"super_admin", "manager", "admin", "teacher"}:
                    continue
                if role == "teacher" and module not in {"bulletin", "tutoring"}:
                    continue
                if role == "admin" and module not in {"appointments", "bulletin", "tutoring", "parent-meetings", "bulk-sms"}:
                    continue
                visible.append(
                    {"label": title, "icon": icon, "endpoint": endpoint, "module": module}
                )
            elif endpoint == "views.homework" and role not in {"super_admin", "manager", "admin", "teacher"}:
                continue
            elif endpoint in {"views.gradebook", "views.attendance_roster", "views.staff_updates"} and role not in {"super_admin", "manager", "admin", "teacher"}:
                continue
            else:
                visible.append(
                    {"label": title, "icon": icon, "endpoint": endpoint, "module": None}
                )
            if visible:
                category = item["category"]
                for entry in visible:
                    translated_entry = {
                        **entry,
                        "label": translate(entry["label"], language),
                    }
                    visible_groups.setdefault(category, []).append(translated_entry)
                    visible_nav_items.append(
                        {
                            "category": translate(category, language),
                            "title": translated_entry["label"],
                            "icon": entry["icon"],
                            "url_endpoint": entry["endpoint"],
                            "route_arg": entry.get("resource") or entry.get("module", ""),
                            "display_order": item["display_order"],
                            **(
                                {"resource": entry["resource"]}
                                if entry.get("resource")
                                else {}
                            ),
                            **(
                                {"module": entry["module"]}
                                if entry.get("module")
                                else {}
                            ),
                        }
                    )
        visible_groups = [
            {"title": translate(category, language), "items": items}
            for category, items in visible_groups.items()
        ]
        return {
            "resources": RESOURCES,
            "school_name": setting["value"] if setting else "WISE HOUSE INTERNATIONAL SCHOOL",
            "nav_groups": visible_groups,
            "nav_items": visible_nav_items,
            "site_settings": {
                "sidebar_color": all_settings.get("sidebar_color", "#123c73"),
                "hero_image_url": (
                    url_for(
                        "views.uploaded_logo",
                        filename=all_settings.get("hero_image_filename")
                        or all_settings.get("landing_image_filename"),
                    )
                    if all_settings.get("hero_image_filename")
                    or all_settings.get("landing_image_filename")
                    else ""
                ),
                "index_welcome_text": all_settings.get("index_welcome_text", ""),
                "primary_color": all_settings.get("primary_color", "#1758a6"),
                "login_background": all_settings.get("login_background", "#f1f5f9"),
                "school_name": all_settings.get(
                    "school_name", "WISE HOUSE INTERNATIONAL SCHOOL"
                ),
            },
            "role_label": {
                "super_admin": "Super Admin", "admin": "Admin", "manager": "Manager",
                "teacher": "Teacher", "student": "Student", "parent": "Parent",
                "health_officer": "Health Officer",
                "boarding_master": "Boarding Master",
            }.get(role, "Guest"),
            "current_year": date.today().year,
            "academic_year": configured_academic_year,
            "academic_year_options": [
                "{}-{}".format(start, start + 1)
                for start in range(
                    max(2000, academic_year_start - 1),
                    academic_year_start + 2,
                )
            ],
            "current_date": date.today().strftime("%A, %B %d, %Y").replace(" 0", " "),
            "site_url": app.config["SITE_URL"],
            "language": language,
            "enabled_modules": enabled_modules,
            "index_layout": index_layout,
            "languages": LANGUAGES,
            "landing_settings": all_settings,
            "brand_settings": {
                key: all_settings[key]
                for key in (
                    "logo_filename", "landing_image_filename",
                    "hero_image_filename", "primary_color", "login_background",
                    "sidebar_color",
                )
                if key in all_settings
            },
            "index_welcome_text": all_settings.get("index_welcome_text", ""),
        }

    return app
