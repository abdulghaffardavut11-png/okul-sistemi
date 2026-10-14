import io
import os
import sqlite3
import tempfile
import unittest

from werkzeug.security import generate_password_hash

from school_system import create_app
from school_system.db import get_db


class SchoolSystemTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = os.path.join(self.temp_dir.name, "test.sqlite")
        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE": self.database,
                "UPLOAD_FOLDER": os.path.join(self.temp_dir.name, "uploads"),
                "SECRET_KEY": "test-key",
            }
        )
        self.client = self.app.test_client()
        with self.app.app_context():
            get_db().execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'super_admin')",
                (
                    "admin",
                    generate_password_hash(
                        "correct horse battery staple", method="pbkdf2:sha256"
                    ),
                ),
            )
            get_db().commit()

    def tearDown(self):
        self.temp_dir.cleanup()

    def sign_in(self):
        return self.client.post(
            "/login",
            data={"username": "admin", "password": "correct horse battery staple"},
            follow_redirects=True,
        )

    def sign_in_as(self, username, password):
        return self.client.post(
            "/login",
            data={"username": username, "password": password},
            follow_redirects=True,
        )

    def add_user(self, username, role, teacher_id=None, student_id=None, email=None):
        with self.app.app_context():
            get_db().execute(
                "INSERT INTO users (username,email,password_hash,role,teacher_id,student_id) "
                "VALUES (?,?,?,?,?,?)",
                (
                    username,
                    email,
                    generate_password_hash("a-secure-test-password", method="pbkdf2:sha256"),
                    role,
                    teacher_id,
                    student_id,
                ),
            )
            get_db().commit()

    def add_student(self, name="Avery Student", email="avery@example.test"):
        return self.client.post(
            "/manage/students",
            data={"name": name, "email": email, "grade_level": "10"},
            follow_redirects=True,
        )

    def test_dashboard_requires_login_and_login_works(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.location)
        response = self.sign_in()
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Dashboard", response.data)

    def test_invalid_login_is_rejected(self):
        response = self.client.post(
            "/login", data={"username": "admin", "password": "wrong"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Invalid sign-in details", response.data)

    def test_reset_admin_cli_changes_password_without_displaying_it(self):
        result = self.app.test_cli_runner().invoke(
            args=["reset-admin"],
            input="admin\nnew-secret-pass\nnew-secret-pass\n",
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Administrator password updated.", result.output)
        self.assertNotIn("new-secret-pass", result.output)

        response = self.client.post(
            "/login",
            data={"username": "admin", "password": "new-secret-pass"},
            follow_redirects=True,
        )
        self.assertIn(b"Dashboard", response.data)

    def test_reset_admin_cli_rejects_unknown_user(self):
        result = self.app.test_cli_runner().invoke(
            args=["reset-admin"],
            input="unknown\n",
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("No super administrator exists", result.output)

    def test_school_name_is_customizable_from_admin_settings(self):
        response = self.client.get("/settings")
        self.assertEqual(response.status_code, 302)

        response = self.sign_in()
        self.assertIn(b"WISE HOUSE INTERNATIONAL SCHOOL", response.data)

        response = self.client.get("/settings")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"WISE HOUSE INTERNATIONAL SCHOOL", response.data)

        response = self.client.post(
            "/settings",
            data={
                "school_name": "WISE HOUSE INTERNATIONAL SCHOOL",
                "primary_color": "#1758a6",
                "login_background": "#f1f5f9",
            },
            follow_redirects=True,
        )
        self.assertIn(b"School branding saved.", response.data)
        self.assertIn(b"WISE HOUSE INTERNATIONAL SCHOOL", response.data)

        response = self.client.post(
            "/settings", data={"school_name": "   "}, follow_redirects=True
        )
        self.assertIn(b"Enter a school name", response.data)

        response = self.client.post(
            "/settings", data={"school_name": "A" * 121}, follow_redirects=True
        )
        self.assertIn(b"Enter a school name", response.data)

    def test_settings_control_modules_and_dashboard_layout(self):
        self.sign_in()
        settings_page = self.client.get("/settings")
        self.assertEqual(settings_page.status_code, 200)
        self.assertIn(b"Optional modules", settings_page.data)
        self.assertIn(b'name="module_name" value="attendance"', settings_page.data)
        self.assertIn(b'name="action" value="toggle_module"', settings_page.data)
        self.assertIn(b'name="module_name" value="boarding"', settings_page.data)
        self.assertIn(b'class="module-status enabled"', settings_page.data)
        self.assertIn(b'name="index_layout"', settings_page.data)

        for module_name in ("attendance", "teachers", "boarding"):
            saved_modules = self.client.post(
                "/settings",
                data={"action": "toggle_module", "module_name": module_name},
                follow_redirects=True,
            )
            self.assertEqual(saved_modules.status_code, 200)
            self.assertIn(b"Module visibility updated.", saved_modules.data)
        sidebar = self.client.get("/")
        self.assertIn(b'href="/admin/classes"', sidebar.data)
        self.assertIn(b'href="/summer-school"', sidebar.data)
        self.assertIn(b'href="/settings"', sidebar.data)
        self.assertNotIn(b'href="/attendance"', sidebar.data)
        self.assertNotIn(b'href="/manage/teachers"', sidebar.data)
        self.assertNotIn(b'href="/admin/boarding"', sidebar.data)
        self.assertEqual(self.client.get("/attendance").status_code, 404)
        self.assertEqual(self.client.get("/manage/attendance").status_code, 404)
        self.assertEqual(self.client.get("/manage/teachers").status_code, 404)
        self.assertEqual(self.client.get("/admin/boarding").status_code, 404)
        self.assertEqual(self.client.get("/summer-school").status_code, 200)
        registration_page = self.client.get("/student-registration")
        self.assertEqual(registration_page.status_code, 200)
        self.assertNotIn(b"Load attendance roster", registration_page.data)
        self.assertEqual(
            self.client.get("/student-registration?tab=attendance").status_code,
            404,
        )
        self.client.post(
            "/settings",
            data={"action": "toggle_module", "module_name": "attendance"},
            follow_redirects=True,
        )
        attendance_sidebar = self.client.get("/")
        self.assertIn(b'href="/attendance"', attendance_sidebar.data)

        saved_layout = self.client.post(
            "/settings",
            data={"action": "update_layout", "index_layout": "classic_list"},
            follow_redirects=True,
        )
        self.assertEqual(saved_layout.status_code, 200)
        self.assertIn(b"Dashboard layout saved.", saved_layout.data)
        dashboard = self.client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b'class="dashboard-grid dashboard-classic"', dashboard.data)
        minimal_layout = self.client.post(
            "/settings",
            data={"action": "update_layout", "index_layout": "minimal_dashboard"},
            follow_redirects=True,
        )
        self.assertEqual(minimal_layout.status_code, 200)
        self.assertIn(
            b'class="dashboard-grid dashboard-minimal"',
            self.client.get("/").data,
        )
        with self.app.app_context():
            settings = {
                row["key"]: row["value"]
                for row in get_db().execute(
                    "SELECT key,value FROM settings WHERE key IN "
                    "('module_attendance','module_summer_school','module_teachers',"
                    "'module_boarding','index_layout')"
                ).fetchall()
            }
            self.assertEqual(settings["module_attendance"], "1")
            self.assertNotIn("module_summer_school", settings)
            self.assertEqual(settings["module_teachers"], "0")
            self.assertEqual(settings["module_boarding"], "0")
            self.assertEqual(settings["index_layout"], "minimal_dashboard")

    def test_navigation_items_are_managed_and_rendered_from_database(self):
        self.sign_in()
        added = self.client.post(
            "/settings",
            data={
                "action": "add_navigation_item",
                "title": "Custom student link",
                "category": "Custom",
                "icon": "S",
                "target": "views.manage:students",
            },
            follow_redirects=True,
        )
        self.assertEqual(added.status_code, 200)
        self.assertIn(b"Navigation item added.", added.data)
        self.assertIn(b"Custom student link", self.client.get("/").data)

        with self.app.app_context():
            item = get_db().execute(
                "SELECT id FROM navigation_items WHERE title='Custom student link'"
            ).fetchone()
            item_id = item["id"]

        reordered = self.client.post(
            "/settings",
            data={
                "action": "update_navigation_order",
                "item_id": str(item_id),
                "display_order": "0",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Navigation order updated.", reordered.data)
        with self.app.app_context():
            self.assertEqual(
                get_db().execute(
                    "SELECT display_order FROM navigation_items WHERE id=?",
                    (item_id,),
                ).fetchone()["display_order"],
                0,
            )

        hidden = self.client.post(
            "/settings",
            data={"action": "toggle_navigation_item", "item_id": str(item_id)},
            follow_redirects=True,
        )
        self.assertIn(b"Navigation item visibility updated.", hidden.data)
        self.assertNotIn(b"Custom student link", self.client.get("/").data)

        invalid_target = self.client.post(
            "/settings",
            data={
                "action": "add_navigation_item",
                "title": "Invalid",
                "category": "Custom",
                "icon": "X",
                "target": "views.users:admin",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Enter a valid navigation title", invalid_target.data)

        deleted = self.client.post(
            "/settings",
            data={"action": "delete_navigation_item", "item_id": str(item_id)},
            follow_redirects=True,
        )
        self.assertIn(b"Navigation item deleted.", deleted.data)
        with self.app.app_context():
            self.assertIsNone(
                get_db().execute(
                    "SELECT id FROM navigation_items WHERE id=?", (item_id,)
                ).fetchone()
            )

    def test_theme_and_dashboard_welcome_settings_are_persisted(self):
        self.sign_in()
        saved = self.client.post(
            "/settings",
            data={
                "school_name": "WISE HOUSE INTERNATIONAL SCHOOL",
                "primary_color": "#1758a6",
                "login_background": "#f1f5f9",
                "sidebar_color": "#336699",
                "index_welcome_text": "Welcome to our school dashboard.",
                "hero_image": (io.BytesIO(b"\x89PNG\r\n\x1a\nsample"), "hero.png"),
            },
            follow_redirects=True,
        )
        self.assertIn(b"School branding saved.", saved.data)
        dashboard = self.client.get("/")
        self.assertIn(b'style="background-color: #336699;"', dashboard.data)
        self.assertIn(b"Welcome to our school dashboard.", dashboard.data)
        with self.app.app_context():
            settings = {
                row["key"]: row["value"]
                for row in get_db().execute(
                    "SELECT key,value FROM settings WHERE key IN "
                    "('sidebar_color','index_welcome_text','hero_image_filename')"
                ).fetchall()
            }
        self.assertEqual(settings["sidebar_color"], "#336699")
        self.assertEqual(
            settings["index_welcome_text"], "Welcome to our school dashboard."
        )
        self.assertTrue(settings["hero_image_filename"].endswith(".png"))
        public_page = self.client.get("/school")
        self.assertIn(settings["hero_image_filename"].encode(), public_page.data)

    def test_settings_class_controls_reuse_validated_class_management(self):
        self.sign_in()
        with self.app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO teachers (name,email,subject) VALUES (?,?,?)",
                ("Settings Class Teacher", "settings-teacher@example.test", "Science"),
            )
            teacher_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.commit()

        settings_page = self.client.get("/settings")
        self.assertIn(b"Class management", settings_page.data)
        self.assertIn(b"Settings Class Teacher", settings_page.data)
        added = self.client.post(
            "/admin/classes",
            data={
                "action": "add",
                "name": "Settings Managed Class",
                "code": "SMC-1",
                "teacher_id": str(teacher_id),
            },
            follow_redirects=True,
        )
        self.assertEqual(added.status_code, 200)
        self.assertIn(b"Settings Managed Class", self.client.get("/settings").data)
        with self.app.app_context():
            course_id = get_db().execute(
                "SELECT id FROM courses WHERE code='SMC-1'"
            ).fetchone()["id"]
        deleted = self.client.post(
            "/admin/classes",
            data={"action": "delete", "course_id": str(course_id)},
            follow_redirects=True,
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertNotIn(b"Settings Managed Class", self.client.get("/settings").data)

    def test_student_can_be_created_edited_and_deleted(self):
        self.sign_in()
        response = self.add_student()
        self.assertIn(b"Avery Student", response.data)
        with self.app.app_context():
            student_id = get_db().execute(
                "SELECT id FROM students WHERE email = ?", ("avery@example.test",)
            ).fetchone()["id"]

        response = self.client.post(
            f"/manage/students?edit={student_id}",
            data={"name": "Avery Updated", "email": "avery@example.test", "grade_level": "11"},
            follow_redirects=True,
        )
        self.assertIn(b"Avery Updated", response.data)

        response = self.client.post(
            f"/manage/students/{student_id}/delete", follow_redirects=True
        )
        self.assertIn(b"No students yet", response.data)

    def test_required_fields_and_duplicate_emails_are_reported(self):
        self.sign_in()
        response = self.client.post("/manage/students", data={"name": "Missing fields"})
        self.assertIn(b"Email is required", response.data)
        self.add_student()
        response = self.add_student(name="Another Student")
        self.assertIn(b"conflicts with an existing unique value", response.data)

    def test_course_enrollment_grade_attendance_work_end_to_end(self):
        self.sign_in()
        self.add_student()
        self.client.post(
            "/manage/teachers",
            data={"name": "Taylor Teacher", "email": "taylor@example.test", "subject": "Science"},
        )
        with self.app.app_context():
            teacher_id = get_db().execute(
                "SELECT id FROM teachers WHERE email = ?", ("taylor@example.test",)
            ).fetchone()["id"]
        self.client.post(
            "/manage/courses",
            data={"name": "Biology", "code": "BIO-1", "teacher_id": str(teacher_id)},
        )
        with self.app.app_context():
            student_id = get_db().execute("SELECT id FROM students").fetchone()["id"]
            course_id = get_db().execute("SELECT id FROM courses").fetchone()["id"]
        response = self.client.post(
            "/manage/enrollments",
            data={"student_id": str(student_id), "course_id": str(course_id), "academic_year": "2026"},
            follow_redirects=True,
        )
        self.assertIn(b"Avery Student", response.data)
        with self.app.app_context():
            enrollment_id = get_db().execute("SELECT id FROM enrollments").fetchone()["id"]
        response = self.client.post(
            "/manage/grades",
            data={"enrollment_id": str(enrollment_id), "score": "92.5", "graded_on": "2026-10-01"},
            follow_redirects=True,
        )
        self.assertIn(b"92.5", response.data)
        response = self.client.post(
            "/manage/attendance",
            data={
                "enrollment_id": str(enrollment_id),
                "attendance_date": "2026-10-02",
                "status": "Present",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Present", response.data)

    def test_invalid_grade_and_invalid_related_record_are_rejected(self):
        self.sign_in()
        with self.app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO students (name, email, grade_level) VALUES (?, ?, ?)",
                ("Jordan Student", "jordan@example.test", "9"),
            )
            db.execute(
                "INSERT INTO teachers (name, email, subject) VALUES (?, ?, ?)",
                ("Morgan Teacher", "morgan@example.test", "Math"),
            )
            db.execute(
                "INSERT INTO courses (name, code, teacher_id) VALUES (?, ?, ?)",
                ("Algebra", "ALG-1", 1),
            )
            db.execute(
                "INSERT INTO enrollments (student_id, course_id, academic_year) VALUES (?, ?, ?)",
                (1, 1, 2026),
            )
            db.commit()
            enrollment_id = db.execute("SELECT id FROM enrollments").fetchone()["id"]
        response = self.client.post(
            "/manage/grades",
            data={
                "enrollment_id": str(enrollment_id),
                "score": "120",
                "graded_on": "2026-10-01",
            },
        )
        self.assertIn(b"Score must be between 0 and 100", response.data)
        response = self.client.post(
            "/manage/attendance",
            data={
                "enrollment_id": str(enrollment_id),
                "attendance_date": "not-a-date",
                "status": "Present",
            },
        )
        self.assertIn(b"Date must be a valid date", response.data)

    def test_unknown_resource_is_not_found_and_logout_clears_session(self):
        self.sign_in()
        self.assertEqual(self.client.get("/manage/unknown").status_code, 404)
        self.client.post("/logout")
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_manager_can_manage_records_but_not_root_settings_or_accounts(self):
        with self.app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO students (name,email,grade_level) VALUES (?,?,?)",
                ("Manager Student", "manager-student@example.test", "8"),
            )
            student_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute(
                "INSERT INTO teachers (name,email,subject) VALUES (?,?,?)",
                ("Manager Teacher", "manager-teacher@example.test", "English"),
            )
            teacher_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.commit()
        self.add_user("manager", "manager")
        self.sign_in_as("manager", "a-secure-test-password")
        self.assertEqual(self.client.get("/settings").status_code, 403)
        self.assertEqual(self.client.get("/users").status_code, 403)
        self.assertEqual(self.client.get("/manage/courses").status_code, 200)
        self.assertEqual(self.client.get("/manage/students").status_code, 200)
        self.assertEqual(self.client.get("/manage/teachers").status_code, 200)
        response = self.client.post(
            "/portal/bulletin",
            data={"title": "Manager newsletter", "description": "Staff update"},
            follow_redirects=True,
        )
        self.assertIn(b"Manager newsletter", response.data)
        self.assertIn(
            b"Added Student",
            self.client.post(
                "/manage/students",
                data={"name": "Added Student", "email": "added@example.test", "grade_level": "7"},
                follow_redirects=True,
            ).data,
        )
        self.assertEqual(
            self.client.post(f"/manage/students/{student_id}/delete").status_code, 302
        )
        with self.app.app_context():
            self.assertIsNotNone(
                get_db().execute("SELECT id FROM teachers WHERE id=?", (teacher_id,)).fetchone()
            )

    def test_summer_school_records_are_admin_only_searchable_and_archivable(self):
        self.sign_in()
        response = self.client.post(
            "/summer-school",
            data={
                "academic_year": "2026-2027",
                "student_type": "external",
                "full_name": "Jordan Applicant",
                "gender": "Female",
                "father_name": "Alex Applicant",
                "father_phone": "555-0101",
                "mother_name": "Sam Applicant",
                "mother_phone": "555-0102",
                "home_address": "12 School Street",
                "school_of_origin": "Northside School",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Northside School", response.data)
        with self.app.app_context():
            record = get_db().execute(
                "SELECT * FROM summer_school_records WHERE full_name='Jordan Applicant'"
            ).fetchone()
            self.assertEqual(record["student_type"], "external")
            record_id = record["id"]
        response = self.client.post(
            f"/summer-school/{record_id}/archive",
            data={"archived": "1"},
            follow_redirects=True,
        )
        self.assertIn(b"Jordan Applicant", response.data)
        self.assertNotIn(b"Register a summer school applicant", response.data)
        self.assertIn(
            b"Jordan Applicant",
            self.client.get("/summer-school/archive?q=Northside&year=2026-2027").data,
        )
        self.client.post(
            f"/summer-school/{record_id}/archive",
            data={"archived": "0"},
        )
        self.assertIn(b"Jordan Applicant", self.client.get("/summer-school").data)

    def test_summer_school_is_forbidden_to_teachers_and_managers(self):
        with self.app.app_context():
            get_db().execute(
                "INSERT INTO teachers (name,email,subject) VALUES (?,?,?)",
                ("Restricted Teacher", "restricted@example.test", "Math"),
            )
            get_db().commit()
        self.add_user("teacher-no-summer", "teacher", teacher_id=1)
        self.sign_in_as("teacher-no-summer", "a-secure-test-password")
        self.assertEqual(self.client.get("/summer-school").status_code, 403)
        self.assertEqual(self.client.get("/summer-school/archive").status_code, 403)

        self.client.post("/logout")
        self.add_user("manager-no-summer", "manager")
        self.sign_in_as("manager-no-summer", "a-secure-test-password")
        self.assertEqual(self.client.get("/summer-school").status_code, 403)

    def test_admin_can_manage_profiles_but_cannot_delete_or_change_settings(self):
        self.add_user("school-admin", "admin")
        self.sign_in_as("school-admin", "a-secure-test-password")
        response = self.client.post(
            "/manage/students",
            data={
                "name": "Detailed Student",
                "email": "detailed@example.test",
                "grade_level": "8",
                "national_id": "12345678901",
                "gender": "Other",
                "father_name": "Father Name",
                "father_phone": "555-1000",
                "father_occupation": "Engineer",
                "mother_name": "Mother Name",
                "mother_phone": "555-1001",
                "mother_occupation": "Doctor",
                "home_address": "1 Main Street",
                "dormitory_info": "Dorm 2",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Detailed Student", response.data)
        self.assertEqual(self.client.get("/summer-school").status_code, 200)
        with self.app.app_context():
            student_id = get_db().execute(
                "SELECT id FROM students WHERE email='detailed@example.test'"
            ).fetchone()["id"]
        self.assertEqual(self.client.get("/settings").status_code, 403)
        self.assertEqual(
            self.client.post(f"/manage/students/{student_id}/delete").status_code,
            403,
        )

    def _create_academic_test_records(self):
        with self.app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO teachers (name,email,subject) VALUES (?,?,?)",
                ("Course Teacher", "course-teacher@example.test", "Mathematics"),
            )
            teacher_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute(
                "INSERT INTO teachers (name,email,subject) VALUES (?,?,?)",
                ("Other Course Teacher", "other-course-teacher@example.test", "English"),
            )
            other_teacher_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute(
                "INSERT INTO courses (name,code,teacher_id) VALUES (?,?,?)",
                ("Mathematics", "MATH-1", teacher_id),
            )
            course_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute(
                "INSERT INTO courses (name,code,teacher_id) VALUES (?,?,?)",
                ("English", "ENG-1", other_teacher_id),
            )
            other_course_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            enrollment_ids = []
            for index, course in enumerate((course_id, course_id, other_course_id), 1):
                db.execute(
                    "INSERT INTO students (name,email,grade_level,gender) VALUES (?,?,?,?)",
                    (f"Roster Student {index}", f"roster{index}@example.test", "8", "Male"),
                )
                student_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
                db.execute(
                    "INSERT INTO enrollments (student_id,course_id,academic_year) VALUES (?,?,2026)",
                    (student_id, course),
                )
                enrollment_ids.append(
                    db.execute("SELECT last_insert_rowid()").fetchone()[0]
                )
            db.commit()
        return teacher_id, course_id, other_course_id, enrollment_ids

    def test_attendance_roster_and_gradebook_are_writable_for_staff_roles(self):
        teacher_id, course_id, other_course_id, enrollment_ids = (
            self._create_academic_test_records()
        )
        roles = (
            ("manager", "attendance-manager"),
            ("admin", "attendance-admin"),
            ("teacher", "attendance-teacher"),
        )
        for role, username in roles:
            linked_teacher = teacher_id if role == "teacher" else None
            self.add_user(username, role, teacher_id=linked_teacher)
            client = self.app.test_client()
            client.post(
                "/login",
                data={"username": username, "password": "a-secure-test-password"},
            )
            roster = client.get(
                f"/attendance?course_id={course_id}&attendance_date=2026-10-12"
            )
            self.assertEqual(roster.status_code, 200)
            self.assertIn(b"Roster Student 1", roster.data)
            self.assertIn(b"Roster Student 2", roster.data)
            self.assertIn(b'value="Excused"', roster.data)
            self.assertIn(b"status-excused", roster.data)
            lowercase_class_roster = client.get(
                "/attendance?class_name=mathematics&attendance_date=2026-10-12"
            )
            self.assertEqual(lowercase_class_roster.status_code, 200)
            self.assertIn(b"Roster Student 1", lowercase_class_roster.data)
            if role == "teacher":
                self.assertNotIn(b"Roster Student 3", roster.data)
                self.assertEqual(
                    client.get(f"/attendance?course_id={other_course_id}").status_code,
                    403,
                )
            attendance_post = client.post(
                "/attendance",
                data={
                    "course_id": str(course_id),
                    "attendance_date": "2026-10-12",
                    f"status_{enrollment_ids[0]}": "Excused",
                    f"status_{enrollment_ids[1]}": "Absent",
                    f"status_{enrollment_ids[2]}": "Late",
                },
                follow_redirects=True,
            )
            self.assertEqual(attendance_post.status_code, 200)
            self.assertIn(b"Attendance saved", attendance_post.data)
            gradebook = client.get(
                f"/gradebook?course_id={course_id}&exam_name=Term%20Exam&graded_on=2026-10-12"
            )
            self.assertEqual(gradebook.status_code, 200)
            self.assertIn(b"Mathematics", gradebook.data)
            self.assertIn(b"Roster Student 1", gradebook.data)
            if role == "teacher":
                self.assertNotIn(b"Roster Student 3", gradebook.data)
                self.assertEqual(
                    client.get(f"/gradebook?course_id={other_course_id}").status_code,
                    403,
                )
            score_response = client.post(
                "/gradebook",
                data={
                    "course_id": str(course_id),
                    "exam_name": "Term Exam",
                    "graded_on": "2026-10-12",
                    f"score_{enrollment_ids[0]}": "90",
                    f"score_{enrollment_ids[1]}": "80",
                },
                follow_redirects=True,
            )
            self.assertEqual(score_response.status_code, 200)
            self.assertIn(b"90.0", score_response.data)
            self.assertIn(b"80.0", score_response.data)
        with self.app.app_context():
            attendance = get_db().execute(
                "SELECT status FROM attendance WHERE enrollment_id=? AND attendance_date=?",
                (enrollment_ids[0], "2026-10-12"),
            ).fetchone()
            grade = get_db().execute(
                "SELECT score FROM grades WHERE enrollment_id=? AND exam_name=?",
                (enrollment_ids[0], "Term Exam"),
            ).fetchone()
            self.assertEqual(attendance["status"], "Excused")
            self.assertEqual(grade["score"], 90)

    def test_student_registration_assigns_class_and_opens_live_attendance(self):
        teacher_id, course_id, _, _ = self._create_academic_test_records()
        self.sign_in()
        page = self.client.get("/student-registration")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Student Registration &amp; Attendance", page.data)
        self.assertIn(b"Add a new student", page.data)
        self.assertIn(b"Daily class attendance", page.data)
        response = self.client.post(
            "/student-registration",
            data={
                "name": "Newly Registered Student",
                "email": "new-student@example.test",
                "course_id": str(course_id),
                "father_phone": "555-0301",
                "mother_phone": "555-0302",
                "father_occupation": "Engineer",
                "mother_occupation": "Teacher",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Student registered and enrolled", response.data)
        self.assertIn(b"Current student list", response.data)
        self.assertIn(b"new-student@example.test", response.data)
        self.assertIn(b"Mathematics", response.data)
        filtered_roster = self.client.get(
            f"/student-registration?tab=student&registered_course_id={course_id}"
        )
        self.assertEqual(filtered_roster.status_code, 200)
        self.assertIn(b"data-father-phone=\"555-0301\"", filtered_roster.data)
        self.assertIn(b"data-mother-occupation=\"Teacher\"", filtered_roster.data)
        self.assertIn(b'id="profile-father-phone"', filtered_roster.data)
        self.assertIn(b'id="attendance-title"', filtered_roster.data)
        with self.app.app_context():
            student = get_db().execute(
                "SELECT id,grade_level,father_phone,mother_phone,"
                "father_occupation,mother_occupation FROM students WHERE email=?",
                ("new-student@example.test",),
            ).fetchone()
            self.assertEqual(student["grade_level"], "Mathematics")
            self.assertEqual(student["father_phone"], "555-0301")
            self.assertEqual(student["mother_phone"], "555-0302")
            self.assertEqual(student["father_occupation"], "Engineer")
            self.assertEqual(student["mother_occupation"], "Teacher")
            enrollment = get_db().execute(
                "SELECT id,academic_year FROM enrollments "
                "WHERE student_id=? AND course_id=?",
                (student["id"], course_id),
            ).fetchone()
            self.assertEqual(enrollment["academic_year"], 2026)
        roster = self.client.get(
            f"/student-registration?tab=attendance&course_id={course_id}"
            "&attendance_date=2026-10-12"
        )
        self.assertEqual(roster.status_code, 200)
        self.assertIn(b"Newly Registered Student", roster.data)
        attendance_save = self.client.post(
            "/student-registration",
            data={
                "action": "attendance",
                "active_tab": "attendance",
                "course_id": str(course_id),
                "attendance_date": "2026-10-12",
                f"status_{enrollment['id']}": "Present",
            },
            follow_redirects=True,
        )
        self.assertEqual(attendance_save.status_code, 200)
        self.assertIn(b"Attendance saved", attendance_save.data)
        self.assertIn(b"Newly Registered Student", attendance_save.data)
        with self.app.app_context():
            saved_status = get_db().execute(
                "SELECT status FROM attendance WHERE enrollment_id=? AND attendance_date=?",
                (enrollment["id"], "2026-10-12"),
            ).fetchone()["status"]
            self.assertEqual(saved_status, "Present")
        self.client.post("/logout")
        self.add_user("registration-teacher", "teacher", teacher_id=teacher_id)
        self.sign_in_as("registration-teacher", "a-secure-test-password")
        self.assertEqual(self.client.get("/student-registration").status_code, 403)
        self.assertNotIn(b'href="/student-registration"', self.client.get("/").data)

    def test_class_name_attendance_route_uses_assigned_roster_and_saves_bulk(self):
        teacher_id, _, _, enrollment_ids = (
            self._create_academic_test_records()
        )
        self.add_user("class-name-teacher", "teacher", teacher_id=teacher_id)
        self.sign_in_as("class-name-teacher", "a-secure-test-password")
        response = self.client.get("/manage/attendance?class_name=Mathematics")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<select name="class_name" class="form-control"', response.data)
        self.assertIn(b'<option value="Mathematics" selected>Mathematics</option>', response.data)
        self.assertIn(b"Roster Student 1", response.data)
        self.assertIn(b"Roster Student 2", response.data)
        self.assertNotIn(b"Roster Student 3", response.data)
        response = self.client.post(
            "/manage/attendance",
            data={
                "class_name": "Mathematics",
                "attendance_date": "2026-10-12",
                f"status_{enrollment_ids[0]}": "Present",
                f"status_{enrollment_ids[1]}": "Late",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Attendance saved", response.data)
        with self.app.app_context():
            status = get_db().execute(
                "SELECT status FROM attendance WHERE enrollment_id=? AND attendance_date=?",
                (enrollment_ids[0], "2026-10-12"),
            ).fetchone()["status"]
            self.assertEqual(status, "Present")
        self.assertEqual(
            self.client.get(f"/manage/attendance?class_name=English").status_code,
            403,
        )

    def test_gradebook_scores_can_be_updated_and_deleted(self):
        teacher_id, course_id, _, enrollment_ids = (
            self._create_academic_test_records()
        )
        self.add_user("gradebook-teacher", "teacher", teacher_id=teacher_id)
        self.sign_in_as("gradebook-teacher", "a-secure-test-password")
        url = "/gradebook"
        payload = {
            "course_id": str(course_id),
            "exam_name": "Quiz 1",
            "graded_on": "2026-10-12",
            f"score_{enrollment_ids[0]}": "64",
        }
        self.client.post(url, data=payload)
        payload[f"score_{enrollment_ids[0]}"] = "91"
        updated = self.client.post(url, data=payload, follow_redirects=True)
        self.assertIn(b"91.0", updated.data)
        with self.app.app_context():
            grade = get_db().execute(
                "SELECT id FROM grades WHERE enrollment_id=? AND exam_name='Quiz 1'",
                (enrollment_ids[0],),
            ).fetchone()
            grade_id = grade["id"]
        deleted = self.client.post(
            f"/gradebook/{grade_id}/delete",
            data={
                "course_id": str(course_id),
                "exam_name": "Quiz 1",
                "graded_on": "2026-10-12",
            },
            follow_redirects=True,
        )
        self.assertNotIn(b"91.0", deleted.data)
        self.assertEqual(
            self.client.post(f"/gradebook/{grade_id}/delete").status_code,
            404,
        )

    def test_staff_updates_are_writable_staff_only_and_student_scoped(self):
        teacher_id, course_id, _, _ = (
            self._create_academic_test_records()
        )
        self.add_user("staff-teacher", "teacher", teacher_id=teacher_id)
        self.sign_in_as("staff-teacher", "a-secure-test-password")
        with self.app.app_context():
            student_id = get_db().execute(
                "SELECT student_id FROM enrollments WHERE course_id=? ORDER BY id LIMIT 1",
                (course_id,),
            ).fetchone()["student_id"]
        response = self.client.post(
            "/staff-updates",
            data={
                "category": "student_log",
                "student_id": str(student_id),
                "title": "Student sent home early",
                "body": "Internal staff-only log detail.",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Internal staff-only log detail", response.data)
        response = self.client.post(
            "/staff-updates",
            data={
                "category": "meeting",
                "title": "Staff meeting",
                "scheduled_at": "2026-10-15T15:30",
                "body": "Meeting room 4",
            },
            follow_redirects=True,
        )
        self.assertIn(b"2026-10-15 15:30", response.data)
        with self.app.app_context():
            update_id = get_db().execute(
                "SELECT id FROM staff_updates WHERE category='student_log'"
            ).fetchone()["id"]
        self.client.post("/logout")
        self.add_user("other-teacher", "teacher", teacher_id=teacher_id)
        self.sign_in_as("other-teacher", "a-secure-test-password")
        self.assertEqual(
            self.client.get(f"/staff-updates?edit={update_id}").status_code,
            403,
        )
        self.client.post("/logout")
        with self.app.app_context():
            get_db().execute(
                "INSERT INTO users (username,password_hash,role,student_id) VALUES (?,?,?,?)",
                (
                    "log-parent",
                    generate_password_hash("secure-parent-password", method="pbkdf2:sha256"),
                    "parent",
                    student_id,
                ),
            )
            get_db().commit()
        student_client = self.app.test_client()
        student_client.post(
            "/login",
            data={"username": "log-parent", "password": "secure-parent-password"},
            follow_redirects=True,
        )
        self.assertEqual(student_client.get("/staff-updates").status_code, 403)
        self.assertEqual(student_client.get("/portal/bulletin").status_code, 403)
        self.assertNotIn(b"Student sent home early", student_client.get("/").data)
        self.assertNotIn(b"Staff meeting", student_client.get("/").data)

    def test_student_csv_export_uses_academic_year_and_enforces_privacy_roles(self):
        teacher_id, _, _, enrollment_ids = self._create_academic_test_records()
        with self.app.app_context():
            db = get_db()
            db.execute(
                "UPDATE enrollments SET academic_year=2026 WHERE id IN (?,?)",
                (enrollment_ids[0], enrollment_ids[1]),
            )
            db.execute(
                "UPDATE students SET name='=Formula Student' WHERE id=("
                "SELECT student_id FROM enrollments WHERE id=?)",
                (enrollment_ids[0],),
            )
            db.commit()
        self.sign_in()
        response = self.client.get("/export/students?academic_year=2026-2027")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.headers["Content-Type"])
        self.assertIn(b"\xef\xbb\xbf", response.data[:3])
        self.assertIn(b"'=Formula Student", response.data)
        self.assertIn(b"2026-2027", response.data)
        self.assertEqual(self.client.get("/export/students?academic_year=invalid").status_code, 400)
        self.client.post("/logout")
        self.add_user("export-teacher", "teacher", teacher_id=teacher_id)
        self.sign_in_as("export-teacher", "a-secure-test-password")
        self.assertEqual(self.client.get("/export/students").status_code, 403)
        self.assertEqual(self.client.get("/academic-year-settings").status_code, 404)

    def test_predica_is_based_on_real_assigned_class_results(self):
        teacher_id, _, other_course_id, enrollment_ids = (
            self._create_academic_test_records()
        )
        with self.app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO grades (enrollment_id,score,graded_on,exam_name) VALUES (?,?,?,?)",
                (enrollment_ids[0], 80, "2026-10-01", "Term exam"),
            )
            for status in ("Present", "Present", "Present", "Present", "Present",
                           "Present", "Present", "Absent", "Absent", "Absent"):
                db.execute(
                    "INSERT INTO attendance (enrollment_id,attendance_date,status) VALUES (?,?,?)",
                    (enrollment_ids[0], f"2026-10-{10 + db.execute('SELECT COUNT(*) FROM attendance').fetchone()[0]:02d}", status),
                )
            db.commit()
        self.add_user("predica-teacher", "teacher", teacher_id=teacher_id)
        self.sign_in_as("predica-teacher", "a-secure-test-password")
        response = self.client.get("/predica")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Roster Student 1", response.data)
        self.assertIn(b"74.0%", response.data)
        self.assertNotIn(b"Roster Student 3", response.data)
        self.assertNotIn(b"Other Course Teacher", response.data)
        self.assertEqual(
            self.client.get(f"/gradebook?course_id={other_course_id}").status_code,
            403,
        )

    def test_academic_year_settings_validate_and_save_and_language_alias_works(self):
        self.add_user("year-manager", "manager")
        self.sign_in_as("year-manager", "a-secure-test-password")
        self.assertEqual(self.client.get("/settings/academic-year").status_code, 403)
        self.client.post("/logout")
        self.sign_in()
        invalid = self.client.post(
            "/settings/academic-year",
            data={"academic_year": "2026-2028"},
            follow_redirects=True,
        )
        self.assertIn(b"consecutive years", invalid.data)
        saved = self.client.post(
            "/settings/academic-year",
            data={"academic_year": "2027-2028"},
            follow_redirects=True,
        )
        self.assertIn(b"Academic year settings saved.", saved.data)
        self.assertIn(b"2027-2028", self.client.get("/").data)
        self.assertEqual(self.client.get("/set_language/fr").status_code, 302)
        self.assertEqual(self.client.get("/set_language/ar").status_code, 302)
        self.assertEqual(self.client.get("/set_language/ha").status_code, 302)
        self.assertEqual(self.client.get("/set_language/ak").status_code, 302)
        self.assertEqual(self.client.get("/set_language/xx").status_code, 404)
        self.assertEqual(self.client.get("/set_language/tr").status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 200)
        external_redirect = self.client.get(
            "/set_language/en",
            headers={"Referer": "https://example.org/steal-session"},
        )
        self.assertTrue(external_redirect.location.endswith("/"))

    def test_teacher_can_submit_tutoring_request(self):
        teacher_id, _, _, _ = self._create_academic_test_records()
        self.add_user("tutoring-teacher", "teacher", teacher_id=teacher_id)
        self.sign_in_as("tutoring-teacher", "a-secure-test-password")
        response = self.client.post(
            "/portal/tutoring",
            data={"title": "Math help", "description": "Request extra practice."},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Math help", response.data)
        with self.app.app_context():
            request_row = get_db().execute(
                "SELECT created_by FROM service_requests WHERE module='tutoring'"
            ).fetchone()
            self.assertIsNotNone(request_row)

    def test_super_admin_class_management_uses_persistent_courses_and_enrollments(self):
        with self.app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO teachers(name,email,subject) VALUES(?,?,?)",
                ("Class Manager Teacher", "class-manager@example.test", "Science"),
            )
            teacher_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute(
                "INSERT INTO students(name,email,grade_level) VALUES(?,?,?)",
                ("Database Roster Student", "database-roster@example.test", "Grade 1"),
            )
            student_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.commit()
        self.sign_in()
        denied = self.client.get("/admin/classes")
        self.assertEqual(denied.status_code, 200)
        self.assertIn(b"Class Management", denied.data)
        self.assertIn(b"classes", denied.data)
        added = self.client.post(
            "/admin/classes",
            data={
                "action": "add",
                "name": "Grade 1",
                "code": "GRADE-1-A",
                "teacher_id": str(teacher_id),
            },
            follow_redirects=True,
        )
        self.assertIn(b"Class added successfully.", added.data)
        with self.app.app_context():
            db = get_db()
            course_id = db.execute(
                "SELECT id FROM courses WHERE code='GRADE-1-A'"
            ).fetchone()["id"]
            db.execute(
                "INSERT INTO enrollments(student_id,course_id,academic_year) VALUES(?,?,2026)",
                (student_id, course_id),
            )
            db.commit()
        roster = self.client.get("/admin/classes")
        self.assertIn(b"Database Roster Student", roster.data)
        self.assertIn(b"2026-2027", roster.data)
        blocked_delete = self.client.post(
            "/admin/classes",
            data={"action": "delete", "course_id": str(course_id)},
            follow_redirects=True,
        )
        self.assertIn(b"cannot be deleted", blocked_delete.data)
        with self.app.app_context():
            db = get_db()
            self.assertIsNotNone(
                db.execute("SELECT id FROM courses WHERE id=?", (course_id,)).fetchone()
            )
            db.execute("DELETE FROM enrollments WHERE course_id=?", (course_id,))
            db.commit()
        deleted = self.client.post(
            "/admin/classes",
            data={"action": "delete", "course_id": str(course_id)},
            follow_redirects=True,
        )
        self.assertIn(b"Class deleted successfully.", deleted.data)
        with self.app.app_context():
            self.assertIsNone(
                get_db().execute("SELECT id FROM courses WHERE id=?", (course_id,)).fetchone()
            )

    def test_class_management_is_super_admin_only_and_generic_course_mutations_are_blocked(self):
        self.add_user("classes-manager", "manager")
        self.sign_in_as("classes-manager", "a-secure-test-password")
        self.assertEqual(self.client.get("/admin/classes").status_code, 403)
        self.assertEqual(
            self.client.post(
                "/admin/classes",
                data={"action": "add", "name": "Grade 2", "code": "G2", "teacher_id": "1"},
            ).status_code,
            403,
        )
        self.assertEqual(self.client.get("/manage/courses").status_code, 200)
        self.assertEqual(
            self.client.post(
                "/manage/courses",
                data={"name": "Grade 2", "code": "G2", "teacher_id": "1"},
            ).status_code,
            403,
        )
        self.client.post("/logout")
        self.add_user("classes-admin", "admin")
        self.sign_in_as("classes-admin", "a-secure-test-password")
        self.assertEqual(self.client.get("/admin/classes").status_code, 403)
        self.assertEqual(
            self.client.get("/").data.count(b'href="/admin/classes"'),
            0,
        )

    def test_sensitive_admin_records_boarding_and_report_exports(self):
        teacher_id, _, _, enrollment_ids = self._create_academic_test_records()
        with self.app.app_context():
            student_id = get_db().execute(
                "SELECT student_id FROM enrollments WHERE id=?",
                (enrollment_ids[0],),
            ).fetchone()["student_id"]
        self.add_user("records-admin", "admin")
        self.sign_in_as("records-admin", "a-secure-test-password")
        meeting = self.client.post(
            "/admin/parent-meetings",
            data={
                "student_id": str(student_id),
                "meeting_date": "2026-10-12",
                "notes": "Parent requested reading support.",
            },
            follow_redirects=True,
        )
        self.assertEqual(meeting.status_code, 200)
        self.assertIn(b"Parent requested reading support.", meeting.data)
        health = self.client.post(
            "/admin/health-reports",
            data={
                "student_id": str(student_id),
                "report_type": "incident",
                "reported_at": "2026-10-12T10:30",
                "details": "Private health information.",
            },
            follow_redirects=True,
        )
        self.assertEqual(health.status_code, 200)
        self.assertIn(b"Private health information.", health.data)
        with self.app.app_context():
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM health_reports").fetchone()[0], 1)
        boarding = self.client.post(
            "/admin/boarding",
            data={
                "student_id": str(student_id),
                "residency": "Boarding",
                "room": "Hostel A / Room 2",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Hostel A / Room 2", boarding.data)
        with self.app.app_context():
            assignment = get_db().execute(
                "SELECT residency,room FROM boarding_assignments WHERE student_id=?",
                (student_id,),
            ).fetchone()
            self.assertEqual(assignment["residency"], "Boarding")
        report = self.client.get(f"/export/report/{student_id}")
        self.assertEqual(report.status_code, 200)
        self.assertIn(b"Parent Meeting Records,1", report.data)
        self.assertIn(b"Health & Incident Alerts,1", report.data)
        self.assertIn(b"Boarding Status,Boarding", report.data)
        export = self.client.get("/export/students?academic_year=2026-2027")
        self.assertIn(b"Mathematics", export.data)
        self.assertIn(b"Boarding", export.data)
        with self.app.app_context():
            db = get_db()
            meeting_id = db.execute("SELECT id FROM parent_meetings").fetchone()["id"]
            health_id = db.execute("SELECT id FROM health_reports").fetchone()["id"]
        self.assertEqual(
            self.client.post(f"/admin/parent-meetings/{meeting_id}/delete").status_code,
            403,
        )
        self.assertEqual(
            self.client.post(f"/admin/health-reports/{health_id}/delete").status_code,
            403,
        )
        self.client.post("/logout")
        self.add_user("records-teacher", "teacher", teacher_id=teacher_id)
        self.sign_in_as("records-teacher", "a-secure-test-password")
        for path in (
            "/admin/parent-meetings",
            "/admin/health-reports",
            "/admin/boarding",
            "/export/report/{}".format(student_id),
        ):
            self.assertEqual(self.client.get(path).status_code, 403, path)
        self.assertNotIn(b"Private health information.", self.client.get("/").data)

    def test_health_officer_and_boarding_master_roles_are_limited(self):
        _, _, _, enrollment_ids = self._create_academic_test_records()
        with self.app.app_context():
            student_id = get_db().execute(
                "SELECT student_id FROM enrollments WHERE id=?",
                (enrollment_ids[0],),
            ).fetchone()["student_id"]
        self.client.post("/language", data={"language": "fr"})
        self.sign_in()
        self.assertIn(b'<option value="fr" selected', self.client.get("/").data)
        for username, role in (
            ("health-officer", "health_officer"),
            ("boarding-master", "boarding_master"),
        ):
            response = self.client.post(
                "/users",
                data={
                    "username": username,
                    "role": role,
                    "password": "a-secure-test-password",
                },
                follow_redirects=True,
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn(username.encode(), response.data)

        self.client.post("/logout")
        self.sign_in_as("health-officer", "a-secure-test-password")
        dashboard = self.client.get("/")
        self.assertIn(b"Health Officer", dashboard.data)
        self.assertNotIn(b"School overview", dashboard.data)
        self.assertNotIn(b"Health &amp; Incident Reports", dashboard.data)
        self.assertNotIn(b'href="/manage/students"', dashboard.data)
        self.assertNotIn(b'href="/portal/bulletin"', dashboard.data)
        self.assertEqual(self.client.get("/admin/health-reports").status_code, 403)
        self.assertEqual(self.client.get("/admin/boarding").status_code, 403)
        self.assertEqual(self.client.get("/manage/students").status_code, 403)

        self.client.post("/logout")
        self.sign_in_as("boarding-master", "a-secure-test-password")
        dashboard = self.client.get("/")
        self.assertIn(b"Boarding Master", dashboard.data)
        self.assertIn(b'href="/admin/boarding"', dashboard.data)
        self.assertNotIn(b"School overview", dashboard.data)
        self.assertNotIn(b'href="/manage/students"', dashboard.data)
        self.assertNotIn(b'href="/portal/appointments"', dashboard.data)
        boarding = self.client.post(
            "/admin/boarding",
            data={
                "student_id": str(student_id),
                "residency": "Boarding",
                "room": "Hostel B / Room 1",
            },
            follow_redirects=True,
        )
        self.assertEqual(boarding.status_code, 200)
        self.assertIn(b"Hostel B / Room 1", boarding.data)
        for path in (
            "/admin/health-reports",
            "/admin/parent-meetings",
            "/manage/students",
            "/export/students",
        ):
            self.assertEqual(self.client.get(path).status_code, 403, path)

    def test_manager_can_delete_sensitive_records_and_bulk_sms_is_only_a_draft(self):
        _, _, _, enrollment_ids = self._create_academic_test_records()
        with self.app.app_context():
            student_id = get_db().execute(
                "SELECT student_id FROM enrollments WHERE id=?",
                (enrollment_ids[0],),
            ).fetchone()["student_id"]
        self.add_user("records-manager", "manager")
        self.sign_in_as("records-manager", "a-secure-test-password")
        self.client.post(
            "/admin/parent-meetings",
            data={
                "student_id": str(student_id),
                "meeting_date": "2026-10-12",
                "notes": "Manager meeting note.",
            },
        )
        self.client.post(
            "/admin/health-reports",
            data={
                "student_id": str(student_id),
                "report_type": "health",
                "reported_at": "2026-10-12T11:15",
                "details": "Manager health note.",
            },
        )
        with self.app.app_context():
            meeting_id = get_db().execute("SELECT id FROM parent_meetings").fetchone()["id"]
            health_id = get_db().execute("SELECT id FROM health_reports").fetchone()["id"]
        self.assertEqual(
            self.client.post(f"/admin/parent-meetings/{meeting_id}/delete").status_code,
            302,
        )
        self.assertEqual(
            self.client.post(f"/admin/health-reports/{health_id}/delete").status_code,
            302,
        )
        sms = self.client.post(
            "/admin/bulksms",
            data={"title": "Grade 1 parents", "description": "School closes at 2 PM."},
            follow_redirects=True,
        )
        self.assertIn(b"No SMS was sent", sms.data)
        self.assertIn(b"School closes at 2 PM.", sms.data)
        with self.app.app_context():
            request_row = get_db().execute(
                "SELECT status,details FROM service_requests WHERE module='bulk-sms'"
            ).fetchone()
            self.assertEqual(request_row["status"], "Open")
            self.assertEqual(request_row["details"], "School closes at 2 PM.")
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM health_reports").fetchone()[0], 0)

    def test_profile_photos_are_private_and_role_scoped(self):
        self.sign_in()
        self.client.post(
            "/manage/students",
            data={
                "name": "Photo Student",
                "email": "photo@example.test",
                "grade_level": "7",
                "profile_picture": (io.BytesIO(b"\x89PNG\r\n\x1a\nimage"), "student.png"),
            },
            content_type="multipart/form-data",
        )
        with self.app.app_context():
            student_id = get_db().execute(
                "SELECT id,profile_picture FROM students WHERE email='photo@example.test'"
            ).fetchone()
            filename = student_id["profile_picture"]
            self.assertTrue(filename.startswith("profile-"))
            get_db().execute(
                "INSERT INTO students (name,email,grade_level) VALUES (?,?,?)",
                ("Other Photo Student", "other-photo@example.test", "8"),
            )
            other_id = get_db().execute("SELECT last_insert_rowid()").fetchone()[0]
            get_db().execute(
                "INSERT INTO users (username,password_hash,role,student_id) VALUES (?,?,?,?)",
                (
                    "photo-student",
                    generate_password_hash("a-secure-test-password", method="pbkdf2:sha256"),
                    "student",
                    student_id["id"],
                ),
            )
            get_db().execute(
                "INSERT INTO users (username,password_hash,role,student_id) VALUES (?,?,?,?)",
                (
                    "other-photo-student",
                    generate_password_hash("another-secure-test-password", method="pbkdf2:sha256"),
                    "student",
                    other_id,
                ),
            )
            get_db().commit()
        self.assertEqual(self.client.get("/uploads/" + filename).status_code, 404)
        self.client.post("/logout")
        self.assertEqual(
            self.client.get("/profile-images/" + filename).status_code,
            302,
        )
        self.sign_in_as("photo-student", "a-secure-test-password")
        photo = self.client.get("/profile-images/" + filename)
        self.assertEqual(photo.status_code, 200)
        photo.close()
        self.assertEqual(
            self.client.get(f"/profile-images/profile-{'0' * 32}.png").status_code,
            404,
        )
        self.client.post("/logout")
        self.sign_in_as("other-photo-student", "another-secure-test-password")
        self.assertEqual(
            self.client.get("/profile-images/" + filename).status_code,
            403,
        )

    def test_portal_language_toggle_and_landing_page_content_save(self):
        self.sign_in()
        settings_page = self.client.get("/settings")
        for language_label in ("Français", "العربية (RTL)", "Hausa", "Twi (Akan)"):
            self.assertIn(language_label.encode(), settings_page.data)
        self.assertIn(b"fr", self.client.get("/login").data)
        language_response = self.client.post(
            "/language", data={"language": "fr"}, follow_redirects=True
        )
        self.assertEqual(language_response.status_code, 200)
        self.assertIn(b"fr", self.client.get("/").data)
        self.client.post("/language", data={"language": "tr"})
        response = self.client.post(
            "/settings",
            data={
                "school_name": "WISE HOUSE INTERNATIONAL SCHOOL",
                "primary_color": "#1758a6",
                "login_background": "#f1f5f9",
                "language": "tr",
                "landing_title": "Welcome to Wise House",
                "landing_text": "A welcoming school community.",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Wise House", self.client.get("/school").data)
        self.assertIn(b"Ana Sayfa", self.client.get("/").data)

    def test_teacher_data_is_limited_to_assigned_classes_and_students(self):
        with self.app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO teachers (name,email,subject) VALUES (?,?,?)",
                ("Assigned Teacher", "assigned@example.test", "Science"),
            )
            teacher_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute(
                "INSERT INTO teachers (name,email,subject) VALUES (?,?,?)",
                ("Other Teacher", "other@example.test", "Math"),
            )
            other_teacher = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute(
                "INSERT INTO courses (name,code,teacher_id) VALUES (?,?,?)",
                ("Assigned Class", "AS-1", teacher_id),
            )
            own_course = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute(
                "INSERT INTO courses (name,code,teacher_id) VALUES (?,?,?)",
                ("Private Class", "PR-1", other_teacher),
            )
            private_course = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute(
                "INSERT INTO students (name,email,grade_level) VALUES (?,?,?)",
                ("Assigned Student", "assigned-student@example.test", "8"),
            )
            own_student = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute(
                "UPDATE students SET gender='Female',father_name=?,father_phone=?,"
                "father_occupation=?,mother_name=?,mother_phone=?,mother_occupation=?,home_address=? "
                "WHERE id=?",
                (
                    "PRIVATE_GUARDIAN_NAME", "PRIVATE_PARENT_PHONE",
                    "PRIVATE_PARENT_OCCUPATION", "PRIVATE_MOTHER_NAME",
                    "PRIVATE_MOTHER_PHONE", "PRIVATE_MOTHER_OCCUPATION",
                    "PRIVATE_HOME_ADDRESS", own_student,
                ),
            )
            db.execute(
                "INSERT INTO students (name,email,grade_level) VALUES (?,?,?)",
                ("Private Student", "private-student@example.test", "9"),
            )
            private_student = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            for student_id, course_id in (
                (own_student, own_course), (private_student, private_course)
            ):
                db.execute(
                    "INSERT INTO enrollments (student_id,course_id,academic_year) VALUES (?,?,2026)",
                    (student_id, course_id),
                )
            enrollments = db.execute("SELECT id FROM enrollments ORDER BY id").fetchall()
            db.execute(
                "INSERT INTO grades (enrollment_id,score,graded_on) VALUES (?,?,?)",
                (enrollments[0]["id"], 82, "2026-10-01"),
            )
            db.execute(
                "INSERT INTO grades (enrollment_id,score,graded_on) VALUES (?,?,?)",
                (enrollments[1]["id"], 23, "2026-10-01"),
            )
            db.commit()
            foreign_grade_id = db.execute(
                "SELECT id FROM grades WHERE enrollment_id=?", (enrollments[1]["id"],)
            ).fetchone()["id"]
        self.add_user("teacher", "teacher", teacher_id=teacher_id)
        greeting = self.sign_in_as("teacher", "a-secure-test-password")
        self.assertIn(b"Good morning, Assigned Teacher", greeting.data)
        students = self.client.get("/manage/students")
        self.assertIn(b"Assigned Student", students.data)
        self.assertNotIn(b"Private Student", students.data)
        self.assertIn(b"Female", students.data)
        self.assertIn(b"contact Administration", students.data)
        for private_value in (
            b"assigned-student@example.test", b"PRIVATE_GUARDIAN_NAME",
            b"PRIVATE_PARENT_PHONE", b"PRIVATE_PARENT_OCCUPATION",
            b"PRIVATE_MOTHER_NAME", b"PRIVATE_MOTHER_PHONE",
            b"PRIVATE_MOTHER_OCCUPATION", b"PRIVATE_HOME_ADDRESS",
        ):
            self.assertNotIn(private_value, students.data)
        grades = self.client.get("/manage/grades")
        self.assertIn(b"82.0", grades.data)
        self.assertNotIn(b"23.0", grades.data)
        self.assertEqual(self.client.get("/manage/grades?edit=" + str(foreign_grade_id)).status_code, 404)
        self.assertEqual(self.client.get("/settings").status_code, 403)
        self.assertEqual(self.client.get("/manage/teachers").status_code, 403)
        self.assertEqual(
            self.client.post(
                "/manage/grades",
                data={
                    "enrollment_id": str(enrollments[1]["id"]),
                    "score": "90",
                    "graded_on": "2026-10-02",
                },
            ).status_code,
            200,
        )
        self.assertEqual(self.client.get("/homework").status_code, 200)

    def test_student_email_login_shows_only_linked_student_records(self):
        with self.app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO students (name,email,grade_level) VALUES (?,?,?)",
                ("Portal Student", "portal-student@example.test", "10"),
            )
            student_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute(
                "INSERT INTO students (name,email,grade_level) VALUES (?,?,?)",
                ("Private Student Record", "private@example.test", "11"),
            )
            db.execute(
                "INSERT INTO users (username,password_hash,role,student_id) VALUES (?,?,?,?)",
                (
                    "student-account",
                    generate_password_hash("a-secure-test-password", method="pbkdf2:sha256"),
                    "student",
                    student_id,
                ),
            )
            db.execute(
                "INSERT INTO users (username,password_hash,role,student_id) VALUES (?,?,?,?)",
                (
                    "parent-account",
                    generate_password_hash("parent-password-123", method="pbkdf2:sha256"),
                    "parent",
                    student_id,
                ),
            )
            db.commit()
        response = self.sign_in_as("portal-student@example.test", "a-secure-test-password")
        self.assertIn(b"Portal Student", response.data)
        self.assertNotIn(b"Private Student Record", response.data)
        self.assertEqual(self.client.get("/manage/students").status_code, 403)
        self.assertEqual(self.client.get("/users").status_code, 403)
        parent_client = self.app.test_client()
        parent_client.post(
            "/login",
            data={"username": "portal-student@example.test", "password": "parent-password-123"},
            follow_redirects=True,
        )
        self.assertIn(b"Parent", parent_client.get("/profile").data)

    def test_school_logo_upload_requires_valid_image_and_serves_safely(self):
        self.sign_in()
        response = self.client.post(
            "/settings",
            data={
                "school_name": "Updated School",
                "primary_color": "#123456",
                "login_background": "#abcdef",
                "logo": (io.BytesIO(b"not an image"), "logo.png"),
            },
            content_type="multipart/form-data",
        )
        self.assertIn(b"Upload a valid PNG", response.data)
        response = self.client.post(
            "/settings",
            data={
                "school_name": "Updated School",
                "primary_color": "#123456",
                "login_background": "#abcdef",
                "logo": (io.BytesIO(b"\x89PNG\r\n\x1a\nlogo"), "logo.png"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertIn(b"Updated School", response.data)
        with self.app.app_context():
            filename = get_db().execute(
                "SELECT value FROM settings WHERE key='logo_filename'"
            ).fetchone()["value"]
        logo = self.client.get("/uploads/" + filename)
        self.assertEqual(logo.status_code, 200)
        self.assertEqual(logo.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.client.get("/uploads/../../secret.png").status_code, 404)
        logo.close()

    def test_csrf_protects_production_login_posts(self):
        protected_db = os.path.join(self.temp_dir.name, "csrf.sqlite")
        protected_app = create_app(
            {
                "DATABASE": protected_db,
                "UPLOAD_FOLDER": os.path.join(self.temp_dir.name, "csrf-uploads"),
                "SECRET_KEY": "csrf-test",
            }
        )
        response = protected_app.test_client().post(
            "/login", data={"username": "admin", "password": "irrelevant"}
        )
        self.assertEqual(response.status_code, 400)

    def test_super_admin_creates_role_limited_accounts_and_assignments(self):
        self.sign_in()
        with self.app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO teachers (name,email,subject) VALUES (?,?,?)",
                ("Assigned Teacher", "assigned-teacher@example.test", "History"),
            )
            teacher_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute(
                "INSERT INTO students (name,email,grade_level) VALUES (?,?,?)",
                ("Assigned Learner", "learner@example.test", "7"),
            )
            student_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.commit()
        teacher_response = self.client.post(
            "/users",
            data={
                "username": "teacher-login",
                "email": "teacher@example.test",
                "password": "teacher-password-123",
                "role": "teacher",
                "teacher_id": str(teacher_id),
            },
            follow_redirects=True,
        )
        self.assertIn(b"User account created.", teacher_response.data)
        student_response = self.client.post(
            "/users",
            data={
                "username": "student-login",
                "password": "student-password-123",
                "role": "student",
                "student_id": str(student_id),
            },
            follow_redirects=True,
        )
        self.assertIn(b"User account created.", student_response.data)
        with self.app.app_context():
            teacher_account = get_db().execute(
                "SELECT id FROM users WHERE username='teacher-login'"
            ).fetchone()["id"]
            student_account = get_db().execute(
                "SELECT id FROM users WHERE username='student-login'"
            ).fetchone()["id"]
        changed = self.client.post(
            f"/users/{teacher_account}/role",
            data={"role": "manager"},
            follow_redirects=True,
        )
        self.assertIn(b"Account role and linked profile updated.", changed.data)
        with self.app.app_context():
            account = get_db().execute(
                "SELECT role,teacher_id FROM users WHERE id=?", (teacher_account,)
            ).fetchone()
            self.assertEqual(account["role"], "manager")
            self.assertIsNone(account["teacher_id"])
        student_session = self.app.test_client()
        student_session.post(
            "/login",
            data={"username": "learner@example.test", "password": "student-password-123"},
            follow_redirects=True,
        )
        self.assertEqual(student_session.get("/settings").status_code, 403)
        self.assertEqual(self.client.post(f"/users/{student_account}/status").status_code, 302)

    def test_calendar_requests_and_timetable_are_managed(self):
        self.sign_in()
        request_page = self.client.post(
            "/portal/bulk-sms",
            data={"title": "Parent reminder draft", "description": "Term begins Monday."},
            follow_redirects=True,
        )
        self.assertIn(b"Request recorded.", request_page.data)
        self.assertIn(b"Parent reminder draft", request_page.data)
        with self.app.app_context():
            request_id = get_db().execute(
                "SELECT id FROM service_requests WHERE module='bulk-sms'"
            ).fetchone()["id"]
        response = self.client.post(
            f"/requests/{request_id}/status",
            data={"status": "Complete", "module": "bulk-sms"},
            follow_redirects=True,
        )
        self.assertIn(b"Complete", response.data)
        response = self.client.post(
            f"/requests/{request_id}/delete",
            data={"module": "bulk-sms"},
            follow_redirects=True,
        )
        self.assertIn(b"No records have been added", response.data)

        announcement = self.client.post(
            "/portal/bulletin",
            data={"title": "Welcome", "description": "Welcome to the new term."},
            follow_redirects=True,
        )
        self.assertIn(b"Welcome", announcement.data)
        with self.app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO teachers (name,email,subject) VALUES (?,?,?)",
                ("Schedule Teacher", "schedule@example.test", "Science"),
            )
            teacher_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute(
                "INSERT INTO courses (name,code,teacher_id) VALUES (?,?,?)",
                ("Scheduled Class", "SCH-1", teacher_id),
            )
            db.commit()
            course_id = db.execute(
                "SELECT id FROM courses WHERE code='SCH-1'"
            ).fetchone()["id"]
        schedule = self.client.post(
            "/timetable",
            data={
                "course_id": str(course_id),
                "weekday": "0",
                "starts_at": "08:30",
                "ends_at": "09:30",
                "room": "Room 2",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Scheduled Class", schedule.data)

    def test_old_admin_schema_migrates_without_losing_login(self):
        legacy_path = os.path.join(self.temp_dir.name, "legacy.sqlite")
        legacy_db = sqlite3.connect(legacy_path)
        password_hash = generate_password_hash(
            "legacy-admin-password", method="pbkdf2:sha256"
        )
        legacy_db.executescript(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, "
            "password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'admin' "
            "CHECK(role IN ('admin'))); "
            "CREATE TABLE linked (user_id INTEGER REFERENCES users(id));"
        )
        legacy_db.execute(
            "INSERT INTO users (username,password_hash,role) VALUES (?,?,?)",
            ("legacy-admin", password_hash, "admin"),
        )
        legacy_db.execute("INSERT INTO linked (user_id) VALUES (1)")
        legacy_db.commit()
        legacy_db.close()

        migrated_app = create_app(
            {
                "TESTING": True,
                "DATABASE": legacy_path,
                "SECRET_KEY": "migration-test",
            }
        )
        with migrated_app.app_context():
            user = get_db().execute(
                "SELECT username,password_hash,role FROM users"
            ).fetchone()
            self.assertEqual(user["username"], "legacy-admin")
            self.assertEqual(user["password_hash"], password_hash)
            self.assertEqual(user["role"], "super_admin")
            self.assertEqual(get_db().execute("PRAGMA foreign_key_check").fetchall(), [])
            foreign_key = get_db().execute("PRAGMA foreign_key_list(linked)").fetchone()
            self.assertEqual(foreign_key["table"], "users")
        response = migrated_app.test_client().post(
            "/login",
            data={"username": "legacy-admin", "password": "legacy-admin-password"},
            follow_redirects=True,
        )
        self.assertIn(b"Dashboard", response.data)

    def test_role_schema_migration_preserves_existing_non_admin_roles(self):
        legacy_path = os.path.join(self.temp_dir.name, "role-legacy.sqlite")
        legacy_db = sqlite3.connect(legacy_path)
        legacy_db.executescript(
            "CREATE TABLE teachers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
            "email TEXT NOT NULL UNIQUE, subject TEXT NOT NULL);"
            "CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
            "email TEXT NOT NULL UNIQUE, grade_level TEXT NOT NULL);"
            "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, "
            "password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN "
            "('super_admin','manager','teacher','student','parent',"
            "'health_officer','boarding_master')), email TEXT UNIQUE, "
            "teacher_id INTEGER REFERENCES teachers(id), student_id INTEGER REFERENCES students(id), "
            "is_active INTEGER NOT NULL DEFAULT 1);"
        )
        legacy_db.execute(
            "INSERT INTO teachers (id,name,email,subject) VALUES (1,'Legacy Teacher','t@example.test','Math')"
        )
        legacy_db.execute(
            "INSERT INTO students (id,name,email,grade_level) VALUES (1,'Legacy Student','s@example.test','8')"
        )
        for username, role, teacher_id, student_id in (
            ("root-user", "super_admin", None, None),
            ("manager-user", "manager", None, None),
            ("teacher-user", "teacher", 1, None),
            ("student-user", "student", None, 1),
            ("parent-user", "parent", None, 1),
            ("health-user", "health_officer", None, None),
            ("boarding-user", "boarding_master", None, None),
        ):
            legacy_db.execute(
                "INSERT INTO users (username,password_hash,role,teacher_id,student_id) "
                "VALUES (?,?,?,?,?)",
                (username, "hash", role, teacher_id, student_id),
            )
        legacy_db.commit()
        legacy_db.close()

        migrated_app = create_app(
            {
                "TESTING": True,
                "DATABASE": legacy_path,
                "SECRET_KEY": "role-migration-test",
            }
        )
        with migrated_app.app_context():
            roles = {
                row["username"]: row["role"]
                for row in get_db().execute("SELECT username,role FROM users")
            }
            self.assertEqual(
                roles,
                {
                    "root-user": "super_admin",
                    "manager-user": "manager",
                    "teacher-user": "teacher",
                    "student-user": "student",
                    "parent-user": "parent",
                    "health-user": "health_officer",
                    "boarding-user": "boarding_master",
                },
            )
            self.assertEqual(get_db().execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_public_seo_page_excludes_private_portal_from_search(self):
        page = self.client.get("/school")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"index, follow", page.data)
        self.assertIn(b"og:title", page.data)
        self.assertIn(b"noindex, nofollow", self.client.get("/login").data)
        self.assertIn(b"/sitemap.xml", self.client.get("/robots.txt").data)
        self.assertIn(b"<urlset", self.client.get("/sitemap.xml").data)


if __name__ == "__main__":
    unittest.main()
