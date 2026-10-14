# WISE HOUSE INTERNATIONAL SCHOOL Portal

A Flask and SQLite school management portal with role-scoped workflows, student and parent dashboards, a customizable interface, and a separately indexable public school page.

## Local setup

Requirements: Python 3.9+, pip.

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
export SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
python3 -m flask --app school_system:create_app create-admin
python3 -m flask --app school_system:create_app run
```

Create a unique administrator username and a password with at least 12 characters at the prompts. Passwords are entered without echo. Visit <http://127.0.0.1:5000>. The sign-in form accepts administrator usernames and registered student email addresses. For forgotten administrator passwords, run `python3 -m flask --app school_system:create_app reset-admin`.

## Roles and access

- **Super Admin**: complete application, branding, user accounts, role assignment and data-management privileges.
- **Admin**: manage student and teacher profiles, summer-school records, academic data, attendance, grades and staff updates. Cannot change system settings, manage user accounts or delete critical student/profile records.
- **Manager**: manage school records and operations, post newsletters and updates, and delete managed records. Cannot access root settings or user administration.
- **Teacher**: access only their assigned classes and enrolled students; create/edit class exam results, attendance and homework, publish staff updates, and submit tutoring requests. The teacher profile is linked to their portal account by the Super Admin.
- **Health Officer**: a restricted account with dashboard and profile access only. Private health and incident reports remain limited to Super Admins, Managers and Admins.
- **Boarding Master**: manage boarding/day assignments only, with dashboard and profile access; cannot view private health reports or general student administration.
- **Student / Parent**: sign in with the linked student’s registered email or the account username and view only the linked student’s profile, grades, attendance, homework and timetable.

Create teacher/student records before their user accounts, assign each account to the appropriate profile, and assign teachers to classes. Teachers see only assigned students/classes. Never reuse a student’s login credentials for another person. Grade summaries currently treat scores of 50 or higher as passing.

Super Admins can create classes and review their enrolled student lists in **Class Management**. Classes are stored in the SQLite courses table; rosters are derived from actual student enrollments rather than a hard-coded class list. Deletion is blocked while enrollments, homework or timetable entries still reference a class.

**Student Registration & Attendance** gives Super Admins, Managers and Admins a combined workflow to create a student with a required login email, assign the student to an existing class for the active academic year, and save parent phone and occupation details. Its attendance tab opens the existing date- and class-scoped roster; teachers continue to use the attendance module for their assigned classes.

Super Admins, Managers and Admins can record private parent-meeting notes, health/incident reports, and year-specific boarding/day-student assignments. Teachers, Health Officers, Boarding Masters, and student/parent accounts cannot access private health reports or parent-meeting notes. Boarding Masters can manage boarding assignments but cannot access general student records. Managers and Super Admins can delete parent-meeting or health records; Admins can create and edit them. Student/parent dashboards display their own current boarding status. Authorized school administrators can export a per-student academic summary and the active-year student roster; health details are never included in those exports.

Summer School is available only to Super Admins and Admins. Add either internal students or external applicants; external registrations retain guardian contacts, address, gender and School of Origin. Archive cohorts by academic year instead of deleting their records. Search and restore archived cohorts from the archive page.

## Branding and portal

Student records support national ID, guardian details, occupations, address, gender, dormitory information and a profile picture. Teacher records support phone, address, teaching subject, profile picture and assigned classes. Profile images are private and served only to users authorized to view their associated profiles.

Sign in as Super Admin and open **Settings** to change the school name and logo, theme colors, default portal language, and public landing-page text and image. The language selector offers English, Turkish, French, Arabic, Hausa and Twi (Akan). The current translation catalog covers English and Turkish; choosing another language saves that preference but does not provide a full translation yet. Authenticated users can also choose their own interface language. **User accounts** lets the Super Admin create role-specific logins and activate/deactivate accounts. Super Admins can manage the class timetable; teachers can view their assigned class schedule.

AI landing-page copy suggestions are optional and disabled by default. To enable them, configure `AI_API_KEY`, and optionally `AI_API_URL` and `AI_MODEL`, in the server environment, then enable AI assistance in Settings. Only the current public landing-page title and text are sent; student or other private school records are never sent. No AI suggestion is published until a Super Admin saves the Settings form.

The Super Admin can set the active academic year under **Academic year settings**. Student CSV exports include students enrolled in that academic year and their class and boarding/day status; they are limited to Super Admins, Managers and Admins. The **Performance Forecast** page is staff-only and displays an indicative estimate derived from class grade averages and attendance; it is not a machine-learning prediction or a guarantee of future results. Teachers see only their assigned classes on that page.

Orders, bulk SMS drafts, tutoring and parent meetings use internal request records and do not connect to external services; in particular, no SMS messages are sent. Teachers can submit tutoring requests. Printer Requests, Design Requests and TV Content are not part of the portal navigation or available modules. Interface translations currently cover English and Turkish.

## Production deployment on Render

1. Push this project to a private GitHub repository. Create a new **Web Service** on Render and connect the repository. `.python-version` selects Python 3.11.
2. Use **Python** as the runtime, `pip install -r requirements.txt` as the build command, and `gunicorn --bind 0.0.0.0:$PORT --workers 2 'school_system:create_app()'` as the start command. `render.yaml` contains the corresponding blueprint configuration.
3. Attach a persistent disk at `/var/data` (at least 1 GB). SQLite and uploaded school logos must live on persistent storage; the blueprint sets `DATABASE=/var/data/school.sqlite3` and `UPLOAD_FOLDER=/var/data/uploads`.
4. Configure `FLASK_ENV=production`, a stable randomly generated `SECRET_KEY`, and `SITE_URL=https://your-real-domain.example` in the service environment. `SECRET_KEY` must remain private and must not change on routine deploys. Set `SITE_URL` to the final HTTPS custom domain before asking search engines to index the public page.
5. In Render, open the service Shell and create the first Super Admin with `python3 -m flask --app school_system:create_app create-admin`. Do not put a password into deployment configuration or source control.
6. Add your custom domain in Render’s service settings, create the DNS records Render specifies at your registrar, wait for DNS/HTTPS verification, and then update `SITE_URL` to `https://your-domain`.
7. Verify `/healthz`, sign in, create initial school accounts, and confirm the disk is mounted. SQLite-backed deployments need a persistent disk and a single service instance; use PostgreSQL before scaling to multiple instances.
8. Submit `https://your-domain/sitemap.xml` in Google Search Console and request indexing for `https://your-domain/school`. The school portal and sign-in pages intentionally send `noindex` and are excluded from the sitemap to protect student records. Only the public school overview is made indexable. For meaningful search visibility, replace its introductory copy with approved public facts (location, programs and contact details); private student/parent pages must never be made public for SEO.

Render’s persistent disks are generally a paid feature. Railway and other hosts can be used if they provide equivalent durable storage for both the SQLite database and logo uploads. Do not deploy this SQLite configuration on ephemeral serverless storage such as a standard Vercel function filesystem.

## SEO routes and privacy

- `/school`: public, indexable school overview with dynamic title, canonical URL and OpenGraph metadata.
- `/robots.txt` and `/sitemap.xml`: public crawler instructions and a sitemap containing only the public school overview.
- Authenticated dashboards and `/login`: `noindex, nofollow`.
- `/healthz`: minimal readiness check; it reveals no student information.

## Tests

```bash
python3 -m unittest discover -v
```
