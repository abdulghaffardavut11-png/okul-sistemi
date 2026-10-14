from datetime import date, datetime
import json
import os
import re
import secrets
import sqlite3
import csv
import io
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    send_from_directory,
    request,
    session,
    make_response,
    url_for,
)
from werkzeug.security import generate_password_hash

from .auth import ROLES, roles_required
from .db import get_db

bp = Blueprint("views", __name__)

LANGUAGES = {
    "en": "English",
    "tr": "Türkçe",
    "fr": "Français",
    "ar": "العربية (RTL)",
    "ha": "Hausa",
    "ak": "Twi (Akan)",
}

MODULE_DEFAULTS = {
    "attendance": True,
    "summer_school": True,
    "teachers": True,
    "boarding": True,
}
INDEX_LAYOUTS = {
    "modern_grid": "Modern Grid (Isabet Style)",
    "classic_list": "Classic List",
    "minimal_dashboard": "Minimal Dashboard",
}
LEGACY_INDEX_LAYOUTS = {"compact_list": "classic_list"}

RESOURCES = {
    "students": {
        "title": "Students",
        "singular": "student",
        "table": "students",
        "fields": [
            ("name", "Full name", "text"),
            ("email", "Email", "email"),
            ("grade_level", "Class / grade", "text"),
            ("national_id", "TC / National ID", "text", False),
            ("gender", "Gender", "select", ["Female", "Male", "Other"], False),
            ("father_name", "Father's name", "text", False),
            ("father_phone", "Father's phone", "tel", False),
            ("father_occupation", "Father's occupation", "text", False),
            ("mother_name", "Mother's name", "text", False),
            ("mother_phone", "Mother's phone", "tel", False),
            ("mother_occupation", "Mother's occupation", "text", False),
            ("home_address", "Home address", "textarea", False),
            ("dormitory_info", "Dormitory information", "text", False),
            ("profile_picture", "Profile picture", "file", False),
        ],
        "columns": [("profile_picture", "Photo"), ("name", "Name"), ("email", "Email"), ("grade_level", "Class / grade"), ("national_id", "TC / National ID"), ("gender", "Gender"), ("father_name", "Father"), ("father_phone", "Father's phone"), ("father_occupation", "Father's occupation"), ("mother_name", "Mother"), ("mother_phone", "Mother's phone"), ("mother_occupation", "Mother's occupation"), ("home_address", "Home address"), ("dormitory_info", "Dormitory")],
        "select": "SELECT id, name, email, grade_level, national_id, gender, father_name, father_phone, father_occupation, mother_name, mother_phone, mother_occupation, home_address, dormitory_info, profile_picture FROM students ORDER BY name",
    },
    "teachers": {
        "title": "Teachers",
        "singular": "teacher",
        "table": "teachers",
        "fields": [
            ("name", "Full name", "text"),
            ("email", "Email", "email"),
            ("subject", "Teaching subject / branch", "text"),
            ("phone", "Phone number", "tel", False),
            ("home_address", "Home address", "textarea", False),
            ("profile_picture", "Profile picture", "file", False),
        ],
        "columns": [("profile_picture", "Photo"), ("name", "Name"), ("email", "Email"), ("phone", "Phone"), ("home_address", "Home address"), ("subject", "Teaching subject"), ("assigned_classes", "Assigned classes")],
        "select": "SELECT t.id, t.name, t.email, t.phone, t.home_address, t.subject, t.profile_picture, (SELECT group_concat(c.name, ', ') FROM courses c WHERE c.teacher_id=t.id) AS assigned_classes FROM teachers t ORDER BY t.name",
    },
    "courses": {
        "title": "Classes",
        "singular": "class",
        "table": "courses",
        "fields": [("name", "Class name", "text"), ("code", "Class code", "text"), ("teacher_id", "Teacher", "select", "SELECT id, name FROM teachers ORDER BY name")],
        "columns": [("name", "Class"), ("code", "Code"), ("teacher_name", "Teacher")],
        "select": "SELECT c.id, c.name, c.code, t.name AS teacher_name FROM courses c JOIN teachers t ON t.id = c.teacher_id ORDER BY c.name",
    },
    "enrollments": {
        "title": "Enrollments",
        "singular": "enrollment",
        "table": "enrollments",
        "fields": [("student_id", "Student", "select", "SELECT id, name FROM students ORDER BY name"), ("course_id", "Class", "select", "SELECT id, name FROM courses ORDER BY name"), ("academic_year", "Academic year (start year)", "number")],
        "columns": [("student_name", "Student"), ("course_name", "Class"), ("academic_year", "Academic year")],
        "select": "SELECT e.id, s.name AS student_name, c.name AS course_name, e.academic_year FROM enrollments e JOIN students s ON s.id = e.student_id JOIN courses c ON c.id = e.course_id ORDER BY e.academic_year DESC, s.name",
    },
    "grades": {
        "title": "Exam results",
        "singular": "exam result",
        "table": "grades",
        "fields": [("enrollment_id", "Student / subject", "select", "SELECT e.id, s.name || ' — ' || c.name FROM enrollments e JOIN students s ON s.id = e.student_id JOIN courses c ON c.id = e.course_id ORDER BY s.name"), ("exam_name", "Exam name", "text", False), ("score", "Score (0–100)", "number"), ("graded_on", "Exam date", "date")],
        "columns": [("student_name", "Student"), ("course_name", "Subject / class"), ("exam_name", "Exam"), ("score", "Score"), ("graded_on", "Date")],
        "select": "SELECT g.id, s.name AS student_name, c.name AS course_name, g.exam_name, g.score, g.graded_on FROM grades g JOIN enrollments e ON e.id = g.enrollment_id JOIN students s ON s.id = e.student_id JOIN courses c ON c.id = e.course_id ORDER BY g.graded_on DESC, s.name",
    },
    "attendance": {
        "title": "Attendance",
        "singular": "attendance record",
        "table": "attendance",
        "fields": [("enrollment_id", "Student / class", "select", "SELECT e.id, s.name || ' — ' || c.name FROM enrollments e JOIN students s ON s.id = e.student_id JOIN courses c ON c.id = e.course_id ORDER BY s.name"), ("attendance_date", "Date", "date"), ("status", "Status", "select", ["Present", "Absent", "Late", "Excused"])],
        "columns": [("student_name", "Student"), ("course_name", "Class"), ("attendance_date", "Date"), ("status", "Status")],
        "select": "SELECT a.id, s.name AS student_name, c.name AS course_name, a.attendance_date, a.status FROM attendance a JOIN enrollments e ON e.id = a.enrollment_id JOIN students s ON s.id = e.student_id JOIN courses c ON c.id = e.course_id ORDER BY a.attendance_date DESC, s.name",
    },
}

PORTAL_GROUPS = [
    ("Home", [("Dashboard", "dashboard", "views.dashboard")]),
    ("Registration & Enrollment", [
        ("Summer School", "summer-school", "views.summer_school"),
        ("Summer School Archive", "archive", "views.summer_school_archive"),
        ("Class Management", "classes", "views.manage_classes"),
        ("Enrollments", "enrollments", "views.manage", "enrollments"),
    ]),
    ("Institutional Operations", [
        ("Teachers", "teachers", "views.manage", "teachers"),
        ("Orders", "orders", "views.portal", "orders"),
        ("Appointments & Calendar", "appointments", "views.portal", "appointments"),
        ("Bulk SMS", "bulk-sms", "views.portal", "bulk-sms"),
        ("Parent Meeting Logs", "parent-meetings", "views.parent_meetings"),
        ("Health & Incident Reports", "health", "views.health_reports"),
        ("Boarding & Hostel", "boarding", "views.boarding_management"),
    ]),
    ("Student Operations", [
        ("Student Registration & Attendance", "student-registration", "views.student_registration"),
        ("Student Information", "students", "views.manage", "students"),
        ("Homework Tracker", "homework", "views.homework"),
        ("Class timetable", "timetable", "views.timetable"),
        ("Tutoring", "tutoring", "views.portal", "tutoring"),
        ("Weekly Bulletin", "bulletin", "views.portal", "bulletin"),
        ("Gradebook", "grades", "views.gradebook"),
        ("Performance Reports", "reports", "views.manage", "grades"),
        ("Performance Forecast", "predica", "views.predica"),
        ("Daily Attendance", "attendance", "views.attendance_roster"),
        ("Export Students (CSV)", "export", "views.export_students"),
        ("Staff Updates & Logs", "updates", "views.staff_updates"),
    ]),
    ("User Account", [
        ("Profile", "profile", "views.profile"),
        ("Users", "users", "views.users"),
        ("Settings", "settings", "views.settings"),
        ("Academic year settings", "academic-year", "views.academic_year_settings"),
    ]),
]
NAVIGATION_TARGETS = {}
for _category, _items in PORTAL_GROUPS:
    for _title, _icon, _endpoint, *_args in _items:
        _route_arg = _args[0] if _args else ""
        _target_key = f"{_endpoint}:{_route_arg}" if _route_arg else _endpoint
        NAVIGATION_TARGETS[_target_key] = {
            "title": _title,
            "category": _category,
            "icon": _icon,
            "endpoint": _endpoint,
            "route_arg": _route_arg,
        }

ROLE_LABELS = {
    "super_admin": "Super Admin",
    "admin": "Admin",
    "manager": "Manager",
    "teacher": "Teacher",
    "student": "Student",
    "parent": "Parent",
    "health_officer": "Health Officer",
    "boarding_master": "Boarding Master",
}

TRANSLATIONS = {
    "tr": {
        "Home": "Ana Sayfa",
        "Institutional Operations": "Kurumsal İşlemler",
        "Appointments & Calendar": "Randevu ve Takvim",
        "Orders": "Siparişler",
        "Bulk SMS": "Toplu SMS",
        "Tutoring": "Etüt",
        "Parent Meetings": "Veli Toplantıları",
        "Summer School": "Yaz Okulu",
        "Summer School Archive": "Yaz Okulu Arşivi",
        "Class Management": "Sınıf Yönetimi",
        "Parent Meeting Logs": "Veli Görüşme Kayıtları",
        "Health & Incident Reports": "Sağlık ve Olay Raporları",
        "Boarding & Hostel": "Yurt ve Pansiyon",
        "Meeting date": "Görüşme tarihi",
        "Meeting notes": "Görüşme notları",
        "Report type": "Rapor türü",
        "Reported date and time": "Rapor tarihi ve saati",
        "Details": "Ayrıntılar",
        "Health": "Sağlık",
        "Incident": "Olay",
        "Emergency": "Acil durum",
        "Boarding status": "Konaklama durumu",
        "Boarding": "Yatılı",
        "Day": "Gündüzlü",
        "Room": "Oda",
        "Save assignment": "Yerleşimi kaydet",
        "No records found.": "Kayıt bulunamadı.",
        "An internal SMS request was recorded. No SMS was sent because no messaging provider is configured.": "Dahili SMS talebi kaydedildi. Mesaj sağlayıcısı yapılandırılmadığı için SMS gönderilmedi.",
        "No records": "Kayıt yok",
        "Confidential health and incident records. Access is limited to Super Admins, Managers and Admins.": "Gizli sağlık ve olay kayıtları. Erişim Süper Yöneticiler, Müdürler ve Yöneticilerle sınırlıdır.",
        "Private parent-meeting notes. Access is limited to Super Admins, Managers and Admins.": "Özel veli görüşme notları. Erişim Süper Yöneticiler, Müdürler ve Yöneticilerle sınırlıdır.",
        "Internal request tracking only. No SMS messages are sent because a messaging provider is not configured.": "Yalnızca dahili talep takibidir. Mesaj sağlayıcısı yapılandırılmadığından SMS gönderilmez.",
        "Recipient group": "Alıcı grubu",
        "SMS message draft": "SMS mesaj taslağı",
        "Assign boarding status": "Yatılılık durumunu ata",
        "Boarding assignments": "Yatılılık yerleşimleri",
        "Create school classes, assign teachers, and review enrolled students.": "Sınıflar oluşturun, öğretmen atayın ve kayıtlı öğrencileri inceleyin.",
        "Choose an existing teacher profile for the new class.": "Yeni sınıf için mevcut bir öğretmen profili seçin.",
        "Private parent-meeting notes. Access is limited to Super Admins, Managers and Admins.": "Özel veli görüşme notları. Erişim Süper Yöneticiler, Müdürler ve Yöneticilerle sınırlıdır.",
        "Confidential health and incident records. Access is limited to Super Admins, Managers and Admins.": "Gizli sağlık ve olay kayıtları. Erişim Süper Yöneticiler, Müdürler ve Yöneticilerle sınırlıdır.",
        "Record parent meeting": "Veli görüşmesini kaydet",
        "Edit meeting record": "Görüşme kaydını düzenle",
        "Record health or incident report": "Sağlık veya olay raporu kaydet",
        "Edit report": "Raporu düzenle",
        "Save record": "Kaydı kaydet",
        "Recorded by": "Kaydeden",
        "Records": "Kayıtlar",
        "No students enrolled in this class yet.": "Bu sınıfa henüz öğrenci kaydı yapılmadı.",
        "Students enrolled": "Kayıtlı öğrenci",
        "Total enrollments": "Toplam kayıt",
        "Classes": "Sınıflar",
        "classes": "sınıf",
        "Delete this class? Classes with linked records cannot be deleted.": "Bu sınıf silinsin mi? Bağlı kayıtları olan sınıflar silinemez.",
        "Dashboard": "Kontrol Paneli",
        "Sign out": "Çıkış Yap",
        "Academic year": "Akademik yıl",
        "Academic year settings": "Akademik yıl ayarları",
        "Current academic year": "Mevcut akademik yıl",
        "Set the academic year used for school reporting and performance forecasts.": "Okul raporları ve başarı tahminleri için kullanılacak akademik yılı belirleyin.",
        "Use consecutive years, for example 2026-2027.": "Ardışık yıllar kullanın; örneğin 2026-2027.",
        "Save academic year": "Akademik yılı kaydet",
        "Performance Forecast": "Başarı Tahmini",
        "Export Students (CSV)": "Öğrencileri Dışa Aktar (CSV)",
        "Export CSV": "CSV Dışa Aktar",
        "Recorded grade average": "Kaydedilen not ortalaması",
        "Attendance rate": "Devam oranı",
        "Estimated score": "Tahmini puan",
        "Risk level": "Risk düzeyi",
        "High": "Yüksek",
        "Medium": "Orta",
        "Low": "Düşük",
        "Insufficient data": "Yetersiz veri",
        "A transparent estimate based on recorded grades and attendance for the current academic year.": "Mevcut akademik yılın not ve devam kayıtlarına dayalı şeffaf bir tahmin.",
        "This is an indicative calculation, not a guarantee of future results.": "Bu hesaplama bilgilendirme amaçlıdır; gelecekteki sonuçları garanti etmez.",
        "No enrolled students or academic records were found for this year.": "Bu yıl için kayıtlı öğrenci veya akademik kayıt bulunamadı.",
        "Registration & Enrollment": "Kayıt ve Kayıt İşlemleri",
        "Student Operations": "Öğrenci İşlemleri",
        "User Account": "Kullanıcı Hesabı",
        "Registration & Enrollment": "Kayıt ve Kayıt İşlemleri",
        "Enrollments": "Kayıtlar",
        "Add class": "Sınıf ekle",
        "Class name": "Sınıf adı",
        "Class code": "Sınıf kodu",
        "Assigned teacher": "Atanmış öğretmen",
        "Choose a teacher": "Öğretmen seçin",
        "Class added successfully.": "Sınıf başarıyla eklendi.",
        "Class deleted successfully.": "Sınıf başarıyla silindi.",
        "This class has dependent enrollments, homework, or timetable entries and cannot be deleted.": "Bu sınıfta bağlı öğrenci kayıtları, ödevler veya ders programı olduğundan silinemez.",
        "Add a teacher profile before creating a class.": "Sınıf oluşturmadan önce öğretmen profili ekleyin.",
        "School settings": "Okul ayarları",
        "Manage portal branding, public landing content, language, and optional AI assistance.": "Portal görünümünü, herkese açık sayfa içeriğini, dili ve isteğe bağlı yapay zekâ yardımını yönetin.",
        "School identity": "Okul kimliği",
        "Set the name and logo shown throughout the portal.": "Portal genelinde gösterilecek okul adını ve logosunu belirleyin.",
        "School logo": "Okul logosu",
        "Portal appearance": "Portal görünümü",
        "Choose primary and login background colors, and the default portal language.": "Ana rengi, giriş arka planını ve varsayılan portal dilini seçin.",
        "Primary theme color": "Ana tema rengi",
        "Login page background": "Giriş sayfası arka planı",
        "Default language": "Varsayılan dil",
        "Public landing page": "Herkese açık tanıtım sayfası",
        "Customize the public school welcome page content and hero image.": "Okul tanıtım sayfası metnini ve görselini özelleştirin.",
        "Landing page title": "Tanıtım sayfası başlığı",
        "Welcome text": "Karşılama metni",
        "Landing page image": "Tanıtım sayfası görseli",
        "AI writing assistance": "Yapay zekâ yazım yardımı",
        "Enable AI landing-page suggestions": "Yapay zekâ önerilerini etkinleştir",
        "Suggest improved landing copy": "Tanıtım metni öner",
        "Save settings": "Ayarları kaydet",
        "Teachers": "Öğretmenler",
        "Student Information": "Öğrenci Bilgileri",
        "Homework Tracker": "Ödev Takibi",
        "Class timetable": "Ders Programı",
        "Weekly Bulletin": "Haftalık Duyuru",
        "Performance Reports": "Başarı Raporları",
        "Attendance": "Devam Durumu",
        "Gradebook": "Not Defteri",
        "Staff Updates & Logs": "Personel Duyuruları ve Notları",
        "Settings": "Ayarlar",
        "Users": "Kullanıcılar",
        "Profile": "Profil",
        "Sign out": "Çıkış Yap",
        "School branding": "Okul Görünümü",
        "School name": "Okul Adı",
        "Save branding": "Görünümü Kaydet",
        "Good morning": "Günaydın",
        "Good day": "İyi günler",
        "Summer School Records": "Yaz Okulu Kayıtları",
        "Archive": "Arşiv",
        "Search records": "Kayıt ara",
        "Search": "Ara",
        "All years": "Tüm yıllar",
        "Sign in to your portal": "Portalınıza giriş yapın",
        "Welcome back": "Tekrar hoş geldiniz",
        "Enter your username or registered student email to continue.": "Devam etmek için kullanıcı adınızı veya kayıtlı öğrenci e-postanızı girin.",
        "Username or student email": "Kullanıcı adı veya öğrenci e-postası",
        "Enter your username or email": "Kullanıcı adınızı veya e-postanızı girin",
        "Password": "Şifre",
        "Enter your password": "Şifrenizi girin",
        "Sign in": "Giriş yap",
        "Need help signing in? Please contact your school administrator.": "Giriş konusunda yardıma mı ihtiyacınız var? Okul yöneticinizle iletişime geçin.",
        "Your account and school data are protected.": "Hesabınız ve okul verileriniz korunmaktadır.",
        "User information": "Kullanıcı bilgileri",
        "Your account overview": "Hesap özetiniz",
        "Access role": "Erişim rolü",
        "Portal version": "Portal sürümü",
        "Weekly announcements": "Haftalık duyurular",
        "News from your school": "Okulunuzdan haberler",
        "Appointments & calendar": "Randevular ve takvim",
        "Upcoming school events": "Yaklaşan okul etkinlikleri",
        "Students": "Öğrenciler",
        "Exam results": "Sınav sonuçları",
        "Attendance records": "Devam kayıtları",
        "Daily Attendance": "Günlük yoklama",
        "My classes": "Sınıflarım",
        "My students": "Öğrencilerim",
        "Student": "Öğrenci",
        "Teacher": "Öğretmen",
        "Summer School Records": "Yaz Okulu Kayıtları",
        "Summer School Archive": "Yaz Okulu Arşivi",
        "Keep internal and external applicant records searchable by cohort year.": "Okul içi ve dışı aday kayıtlarını yıla göre aranabilir biçimde saklayın.",
        "View archive": "Arşivi görüntüle",
        "Active records": "Aktif kayıtlar",
        "Register a summer school applicant": "Yaz okulu adayı kaydet",
        "Academic year": "Akademik yıl",
        "Applicant type": "Aday türü",
        "Internal student": "Okul öğrencisi",
        "External applicant": "Okul dışı aday",
        "School student": "Okul öğrencisi",
        "Choose a student…": "Öğrenci seçin…",
        "Full name": "Ad soyad",
        "Gender": "Cinsiyet",
        "Father's name": "Baba adı",
        "Father's phone": "Baba telefonu",
        "Mother's name": "Anne adı",
        "Mother's phone": "Anne telefonu",
        "School of Origin": "Önceki okul",
        "Home address": "Ev adresi",
        "Register applicant": "Adayı kaydet",
        "Search applicant, school of origin, guardian, or year": "Aday, önceki okul, veli veya yıla göre ara",
        "Search records": "Kayıt ara",
        "Year": "Yıl",
        "Type": "Tür",
        "Father / phone": "Baba / telefon",
        "Mother / phone": "Anne / telefon",
        "Address": "Adres",
        "Actions": "İşlemler",
        "Restore": "Geri yükle",
        "No records found": "Kayıt bulunamadı",
        "Try another year or search term.": "Başka bir yıl veya arama terimi deneyin.",
        "User accounts": "Kullanıcı hesapları",
        "Create portal logins and assign each account to the right school role.": "Portal hesapları oluşturun ve uygun okul rolünü atayın.",
        "Create account": "Hesap oluştur",
        "New accounts require a strong password.": "Yeni hesaplar güçlü bir parola gerektirir.",
        "Username": "Kullanıcı adı",
        "Email (optional)": "E-posta (isteğe bağlı)",
        "Role": "Rol",
        "Choose a role": "Rol seçin",
        "Teacher profile (for teachers)": "Öğretmen profili (öğretmenler için)",
        "Not applicable": "Uygulanamaz",
        "Student profile (for students/parents)": "Öğrenci profili (öğrenci/veli için)",
        "Initial password": "İlk parola",
        "Create user account": "Kullanıcı hesabı oluştur",
        "Portal users": "Portal kullanıcıları",
        "Account": "Hesap",
        "Linked profile": "Bağlı profil",
        "Status": "Durum",
        "Action": "İşlem",
        "Update role": "Rolü güncelle",
        "Active": "Etkin",
        "Inactive": "Devre dışı",
        "Deactivate": "Devre dışı bırak",
        "Activate": "Etkinleştir",
        "Current account": "Geçerli hesap",
        "No accounts have been created.": "Henüz hesap oluşturulmadı.",
        "Homework tracker": "Ödev takibi",
        "Assignments and due dates for your classes.": "Sınıflarınızın ödevleri ve teslim tarihleri.",
        "Edit homework": "Ödevi düzenle",
        "Assign homework": "Ödev ata",
        "Class": "Sınıf",
        "Choose a class": "Sınıf seçin",
        "Assignment title": "Ödev başlığı",
        "Due date": "Teslim tarihi",
        "Description": "Açıklama",
        "Save changes": "Değişiklikleri kaydet",
        "No additional instructions.": "Ek yönerge yok.",
        "No homework assignments found.": "Ödev bulunamadı.",
        "Class timetable": "Ders programı",
        "Weekly teaching schedule and assigned class times.": "Haftalık ders programı ve atanmış sınıf saatleri.",
        "Add timetable entry": "Programa ders ekle",
        "Choose a day": "Gün seçin",
        "Weekday": "Haftanın günü",
        "Start time": "Başlangıç saati",
        "End time": "Bitiş saati",
        "Room": "Derslik",
        "Add to timetable": "Programa ekle",
        "No timetable entries have been added.": "Henüz programa ders eklenmedi.",
        "Interactive attendance": "Etkileşimli yoklama",
        "Select a class and date, mark each student, then save the full register.": "Sınıfı ve tarihi seçin, öğrencileri işaretleyin ve yoklamayı kaydedin.",
        "Attendance date": "Yoklama tarihi",
        "Load students": "Öğrencileri getir",
        "Present (Derste)": "Derste",
        "Absent (Yok)": "Yok",
        "Late (Geç)": "Geç",
        "Save attendance": "Yoklamayı kaydet",
        "No students are enrolled in this class.": "Bu sınıfa kayıtlı öğrenci yok.",
        "Gradebook": "Not defteri",
        "Choose a subject, name the exam, and enter student scores in one register.": "Dersi seçin, sınavı adlandırın ve öğrenci notlarını tek bir listede girin.",
        "Subject / class": "Ders / sınıf",
        "Choose a subject": "Ders seçin",
        "Exam name": "Sınav adı",
        "Load gradebook": "Not defterini yükle",
        "Save exam scores": "Sınav notlarını kaydet",
        "Delete score": "Notu sil",
        "Staff updates & logs": "Personel duyuruları ve notları",
        "Staff updates & logs": "Personel duyuruları ve notları",
        "Internal newsletters, student logs, meeting schedules and staff updates. Visible only to school staff.": "Personel bültenleri, öğrenci notları, toplantı takvimi ve iç duyurular. Yalnızca okul personeli görebilir.",
        "Edit staff update": "Personel duyurusunu düzenle",
        "Post staff update": "Personel duyurusu ekle",
        "Update type": "Duyuru türü",
        "Staff newsletter": "Personel bülteni",
        "Internal student log": "Dahili öğrenci notu",
        "Meeting": "Toplantı",
        "Staff update": "Personel duyurusu",
        "Student": "Öğrenci",
        "Choose a student": "Öğrenci seçin",
        "Meeting date and time": "Toplantı tarihi ve saati",
        "Details": "Ayrıntılar",
        "Publish to staff": "Personelle paylaş",
        "STAFF ONLY": "YALNIZCA PERSONEL",
        "Delete this staff update?": "Bu personel duyurusu silinsin mi?",
        "No staff updates have been posted.": "Henüz personel duyurusu yok.",
        "Select a class and date, mark each student, then save the full register.": "Sınıf ve tarih seçin, öğrencileri işaretleyin ve yoklamayı kaydedin.",
        "For parent or guardian contact requests, please contact Administration.": "Veli iletişim bilgileri için lütfen İdare ile iletişime geçin.",
        "Male": "Erkek",
        "Female": "Kız",
        "Exam": "Sınav",
        "Score": "Not",
        "Time": "Saat",
        "students": "öğrenci",
        "View records": "Kayıtları görüntüle",
        "My profile": "Profilim",
        "Your personal and family details": "Kişisel ve aile bilgileriniz",
        "Your signed-in school portal identity.": "Oturum açtığınız okul portalı hesabınız.",
        "No email address linked": "E-posta adresi bağlı değil",
        "Passed exams": "Geçilen sınavlar",
        "Recorded scores": "Kaydedilen notlar",
        "Score of 50 or above": "50 ve üzeri not",
        "Attendance entries": "Devam kayıtları",
        "Recent records": "Son kayıtlar",
        "Exam history": "Sınav geçmişi",
        "Your grades and results": "Notlarınız ve sonuçlarınız",
        "Result": "Sonuç",
        "Passed": "Geçti",
        "Needs support": "Destek gerekiyor",
        "Your latest attendance records": "Son devam kayıtlarınız",
        "Homework": "Ödev",
        "Upcoming class assignments": "Yaklaşan sınıf ödevleri",
        "Class timetable": "Ders programı",
        "Your weekly schedule": "Haftalık programınız",
        "No exam results are available yet.": "Henüz sınav sonucu yok.",
        "No attendance records are available yet.": "Henüz devam kaydı yok.",
        "No homework assignments found.": "Ödev bulunamadı.",
        "Your timetable has not been published yet.": "Ders programınız henüz yayımlanmadı.",
        "Add appointment": "Randevu ekle",
        "Publish announcement": "Duyuru yayımla",
        "Create request": "Talep oluştur",
        "Title": "Başlık",
        "Date and time": "Tarih ve saat",
        "Notes": "Notlar",
        "Announcement": "Duyuru",
        "Request details": "Talep ayrıntıları",
        "Record request": "Talebi kaydet",
        "Save": "Kaydet",
        "Delete": "Sil",
        "This workspace is ready. No records have been added yet.": "Bu alan hazır. Henüz kayıt eklenmedi.",
        "School information and service workspace.": "Okul bilgileri ve hizmet alanı.",
        "User accounts": "Kullanıcı hesapları",
        "Create account": "Hesap oluştur",
        "Role": "Rol",
        "Account": "Hesap",
        "Linked profile": "Bağlı profil",
        "Action": "İşlem",
        "Update role": "Rolü güncelle",
        "Delete": "Sil",
        "Active": "Etkin",
        "Inactive": "Devre dışı",
        "Current account": "Geçerli hesap",
        "Edit": "Düzenle",
        "Save changes": "Değişiklikleri kaydet",
        "No timetable entries have been added.": "Henüz programa ders eklenmedi.",
        "Request status": "Talep durumu",
        "Open": "Açık",
        "In progress": "Devam ediyor",
        "Complete": "Tamamlandı",
        "Create portal logins and assign each account to the right school role.": "Portal hesapları oluşturun ve uygun okul rolünü atayın.",
        "New accounts require a strong password.": "Yeni hesaplar güçlü bir parola gerektirir.",
        "Create user account": "Kullanıcı hesabı oluştur",
        "Use at least 12 characters. Share credentials through a secure channel.": "En az 12 karakter kullanın. Giriş bilgilerini güvenli bir yolla paylaşın.",
        "accounts": "hesap",
        "No email": "E-posta yok",
        "Assignments and due dates for your classes.": "Sınıflarınızın ödevleri ve teslim tarihleri.",
        "Due": "Teslim",
        "Add timetable entry": "Programa ders ekle",
        "Weekly teaching schedule and assigned class times.": "Haftalık ders programı ve atanmış sınıf saatleri.",
        "Time": "Saat",
        "Monday": "Pazartesi",
        "Tuesday": "Salı",
        "Wednesday": "Çarşamba",
        "Thursday": "Perşembe",
        "Friday": "Cuma",
        "Saturday": "Cumartesi",
        "Sunday": "Pazar",
        "Mon": "Pzt",
        "Tue": "Sal",
        "Wed": "Çar",
        "Thu": "Per",
        "Fri": "Cum",
        "Sat": "Cmt",
        "Sun": "Paz",
        "Add to timetable": "Programa ekle",
        "No timetable entries have been added.": "Henüz programa ders eklenmedi.",
        "My learning": "Öğrenimim",
        "Welcome": "Hoş geldiniz",
        "Your personal learning overview. This information is only visible to you.": "Kişisel öğrenim özetiniz. Bu bilgileri yalnızca siz görebilirsiniz.",
        "Your personal and family details": "Kişisel ve aile bilgileriniz",
        "Dormitory": "Yurt",
        "Your latest attendance records": "Son devam kayıtlarınız",
        "Upcoming class assignments": "Yaklaşan sınıf ödevleri",
        "Your weekly schedule": "Haftalık programınız",
        "Speak with your teacher": "Öğretmeninizle görüşün",
        "Female": "Kadın",
        "Male": "Erkek",
        "Other": "Diğer",
        "Present": "Var",
        "Absent": "Yok",
        "Late": "Geç",
        "Excused": "İzinli",
        "Super Admin": "Süper yönetici",
        "Admin": "Yönetici",
        "Manager": "Müdür",
        "Teacher": "Öğretmen",
        "Student": "Öğrenci",
        "Parent": "Veli",
        "orders": "Siparişler",
        "appointments": "Randevular",
        "bulk-sms": "Toplu SMS",
        "tutoring": "Etüt",
        "bulletin": "Duyurular",
        "parent-meetings": "Veli toplantıları",
        "school information and service workspace.": "Okul bilgileri ve hizmet alanı.",
        "Manage school records and review details.": "Okul kayıtlarını yönetin ve ayrıntıları inceleyin.",
        "Records overview": "Kayıt özeti",
        "Add student": "Öğrenci ekle",
        "Edit student": "Öğrenciyi düzenle",
        "Add teacher": "Öğretmen ekle",
        "Edit teacher": "Öğretmeni düzenle",
        "Add class": "Sınıf ekle",
        "Edit class": "Sınıfı düzenle",
        "Add enrollment": "Kayıt ekle",
        "Edit enrollment": "Kaydı düzenle",
        "Add exam result": "Sınav sonucu ekle",
        "Edit exam result": "Sınav sonucunu düzenle",
        "Add attendance record": "Devam kaydı ekle",
        "Edit attendance record": "Devam kaydını düzenle",
        "Edit": "Düzenle",
        "TC / National ID": "TC / Kimlik numarası",
        "Father's occupation": "Baba mesleği",
        "Mother's occupation": "Anne mesleği",
        "Father": "Baba",
        "Mother": "Anne",
        "Father's phone": "Baba telefonu",
        "Mother's phone": "Anne telefonu",
        "Class / grade": "Sınıf / kademe",
        "Student full name": "Öğrenci ad soyadı",
        "Student profile": "Öğrenci profili",
        "Select a student": "Öğrenci seçin",
        "All classes": "Tüm sınıflar",
        "Filter": "Filtrele",
        "Father information": "Baba bilgileri",
        "Mother information": "Anne bilgileri",
        "Address information": "Adres bilgisi",
        "Phone": "Telefon",
        "Occupation": "Meslek",
        "Teaching subject / branch": "Ders / branş",
        "Teaching subject": "Ders / branş",
        "Phone number": "Telefon numarası",
        "Assigned classes": "Atanmış sınıflar",
        "Class name": "Sınıf adı",
        "Class code": "Sınıf kodu",
        "Optional modules": "İsteğe bağlı modüller",
        "Enable or disable optional school portal modules.": "Okul portalındaki isteğe bağlı modülleri açın veya kapatın.",
        "Save module settings": "Modül ayarlarını kaydet",
        "Dashboard layout": "Kontrol paneli düzeni",
        "Choose how the dashboard is arranged.": "Kontrol panelinin görünümünü seçin.",
        "Home page layout": "Ana sayfa düzeni",
        "Modern Grid (Isabet Style)": "Modern ızgara",
        "Classic List": "Klasik liste",
        "Minimal Dashboard": "Sade kontrol paneli",
        "Toggle status": "Durumu değiştir",
        "Active": "Aktif",
        "Hidden": "Gizli",
        "Save dashboard layout": "Ana sayfa düzenini kaydet",
        "Class management": "Sınıf yönetimi",
        "Create classes, assign teachers, and remove classes without linked records.": "Sınıf oluşturun, öğretmen atayın ve bağlı kaydı olmayan sınıfları silin.",
        "Assigned teacher": "Atanan öğretmen",
        "Choose a teacher": "Öğretmen seçin",
        "Add a teacher profile before creating a class.": "Sınıf oluşturmadan önce bir öğretmen profili ekleyin.",
        "Delete this class? Classes with linked records cannot be deleted.": "Bu sınıf silinsin mi? Bağlı kayıtları olan sınıflar silinemez.",
        "No classes yet": "Henüz sınıf yok",
        "Open full class management": "Sınıf yönetimini aç",
        "Teacher": "Öğretmen",
        "Student": "Öğrenci",
        "Student / class": "Öğrenci / sınıf",
        "Score (0–100)": "Not (0–100)",
        "Exam date": "Sınav tarihi",
        "Date": "Tarih",
        "Status": "Durum",
        "No students yet": "Henüz öğrenci yok",
        "No teachers yet": "Henüz öğretmen yok",
        "No classes yet": "Henüz sınıf yok",
        "No enrollments yet": "Henüz kayıt yok",
        "No exam results yet": "Henüz sınav sonucu yok",
        "No attendance yet": "Henüz devam kaydı yok",
        "Add a record using the form to get started.": "Başlamak için formu kullanarak kayıt ekleyin.",
    }
}


@bp.app_template_filter("translate")
def translate(text, language="en"):
    return TRANSLATIONS.get(language, {}).get(text, text)


def _module_states(db=None):
    db = db or get_db()
    settings = {
        row["key"]: row["value"]
        for row in db.execute(
            "SELECT key,value FROM settings WHERE key LIKE 'module_%'"
        ).fetchall()
    }
    return {
        name: settings.get("module_" + name, "1" if enabled else "0") == "1"
        for name, enabled in MODULE_DEFAULTS.items()
    }


def _index_layout(value):
    value = LEGACY_INDEX_LAYOUTS.get(value, value)
    return value if value in INDEX_LAYOUTS else "modern_grid"


def _require_module(name):
    if not _module_states().get(name, False):
        abort(404)


def _settings_context(db, values):
    index_layout = _index_layout(values.get("index_layout", "modern_grid"))
    return {
        "values": values,
        "modules": _module_states(db),
        "index_layout": index_layout,
        "layouts": INDEX_LAYOUTS,
        "classes": db.execute(
            "SELECT c.id,c.name,c.code,t.name AS teacher_name FROM courses c "
            "JOIN teachers t ON t.id=c.teacher_id ORDER BY c.name"
        ).fetchall(),
        "teachers": db.execute(
            "SELECT id,name,subject FROM teachers ORDER BY name"
        ).fetchall(),
        "navigation_items": db.execute(
            "SELECT id,category,title,icon,url_endpoint,route_arg,is_active,display_order "
            "FROM navigation_items ORDER BY category,display_order,id"
        ).fetchall(),
        "navigation_targets": NAVIGATION_TARGETS,
    }


@bp.get("/school")
def public_school_page():
    return render_template("public_school.html", public_page=True)


@bp.get("/robots.txt")
def robots():
    response = make_response(
        "User-agent: *\nDisallow: /\nAllow: /school$\nAllow: /sitemap.xml\nSitemap: "
        + url_for("views.sitemap", _external=True)
        + "\n",
        200,
    )
    response.mimetype = "text/plain"
    return response


@bp.get("/sitemap.xml")
def sitemap():
    response = make_response(
        render_template(
            "sitemap.xml",
            school_url=current_app.config["SITE_URL"] + url_for("views.public_school_page"),
        ),
        200,
    )
    response.mimetype = "application/xml"
    return response


@bp.get("/uploads/<path:filename>")
def uploaded_logo(filename):
    if not re.fullmatch(r"[a-f0-9]{32}\.(png|jpg|gif|webp)", filename):
        abort(404)
    response = send_from_directory(current_app.config["UPLOAD_FOLDER"], filename)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.get("/profile-images/<path:filename>")
@roles_required(*ROLES)
def profile_image(filename):
    if not re.fullmatch(r"profile-[a-f0-9]{32}\.(png|jpg|gif|webp)", filename):
        abort(404)
    user = _current_user()
    db = get_db()
    owner = db.execute(
        "SELECT id FROM teachers WHERE profile_picture=?", (filename,)
    ).fetchone()
    if owner:
        allowed = user["role"] in {"super_admin", "admin", "manager"} or (
            user["role"] == "teacher" and user["teacher_id"] == owner["id"]
        )
    else:
        owner = db.execute(
            "SELECT id FROM students WHERE profile_picture=?", (filename,)
        ).fetchone()
        allowed = bool(owner) and user["role"] in {"super_admin", "admin", "manager"}
        if owner and user["role"] == "teacher":
            allowed = bool(
                db.execute(
                    "SELECT 1 FROM enrollments e JOIN courses c ON c.id=e.course_id "
                    "WHERE e.student_id=? AND c.teacher_id=? LIMIT 1",
                    (owner["id"], user["teacher_id"]),
                ).fetchone()
            )
        elif owner and user["role"] in {"student", "parent"}:
            allowed = user["student_id"] == owner["id"]
    if not owner:
        abort(404)
    if not allowed:
        abort(403)
    response = send_from_directory(current_app.config["UPLOAD_FOLDER"], filename)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, max-age=300"
    return response


def _current_user():
    return get_db().execute(
        "SELECT u.*, t.name AS teacher_name, t.profile_picture AS teacher_picture, "
        "s.name AS student_name, s.profile_picture AS student_picture "
        "FROM users u LEFT JOIN teachers t ON t.id=u.teacher_id "
        "LEFT JOIN students s ON s.id=u.student_id WHERE u.id = ?",
        (session["user_id"],),
    ).fetchone()


def _teacher_course_filter(user, query):
    if user["role"] == "teacher":
        if "FROM students ORDER BY" in query:
            return query.replace(
                " ORDER BY",
                " WHERE id IN (SELECT e.student_id FROM enrollments e "
                "JOIN courses c ON c.id=e.course_id WHERE c.teacher_id=?) ORDER BY",
            ), (user["teacher_id"],)
        return query.replace(" ORDER BY", " WHERE c.teacher_id = ? ORDER BY"), (user["teacher_id"],)
    return query, ()


STAFF_ROLES = ("super_admin", "manager", "admin", "teacher")
ACADEMIC_WRITE_ROLES = ("super_admin", "manager", "admin", "teacher")


def _course_scope(user):
    if user["role"] == "teacher":
        if not user["teacher_id"]:
            abort(403)
        return " AND c.teacher_id=?", (user["teacher_id"],)
    if user["role"] in {"super_admin", "manager", "admin"}:
        return "", ()
    abort(403)


def _student_scope(user):
    if user["role"] == "teacher":
        if not user["teacher_id"]:
            abort(403)
        return (
            " AND e.student_id IN (SELECT e2.student_id FROM enrollments e2 "
            "JOIN courses c2 ON c2.id=e2.course_id WHERE c2.teacher_id=?)",
            (user["teacher_id"],),
        )
    if user["role"] in {"super_admin", "manager", "admin"}:
        return "", ()
    abort(403)


@bp.get("/")
@roles_required(*ROLES)
def dashboard():
    user = _current_user()
    db = get_db()
    module_states = _module_states(db)
    if user["role"] in ("student", "parent"):
        if user["student_id"] is None:
            abort(403)
        student = db.execute(
            "SELECT * FROM students WHERE id = ?", (user["student_id"],)
        ).fetchone()
        if student is None:
            abort(403)
        grades = db.execute(
            "SELECT c.name AS course_name, g.score, g.graded_on, "
            "CASE WHEN g.score >= 50 THEN 'Passed' ELSE 'Needs support' END AS result "
            "FROM grades g JOIN enrollments e ON e.id = g.enrollment_id "
            "JOIN courses c ON c.id = e.course_id WHERE e.student_id = ? "
            "ORDER BY g.graded_on DESC",
            (student["id"],),
        ).fetchall()
        attendance = db.execute(
            "SELECT c.name AS course_name, a.attendance_date, a.status "
            "FROM attendance a JOIN enrollments e ON e.id = a.enrollment_id "
            "JOIN courses c ON c.id = e.course_id WHERE e.student_id = ? "
            "ORDER BY a.attendance_date DESC LIMIT 10",
            (student["id"],),
        ).fetchall()
        homework = db.execute(
            "SELECT h.title, h.description, h.due_date, c.name AS course_name "
            "FROM homework h JOIN courses c ON c.id = h.course_id "
            "JOIN enrollments e ON e.course_id = c.id WHERE e.student_id = ? "
            "ORDER BY h.due_date",
            (student["id"],),
        ).fetchall()
        timetable = db.execute(
            "SELECT c.name AS course_name, t.weekday, t.starts_at, t.ends_at, t.room "
            "FROM timetable t JOIN courses c ON c.id = t.course_id "
            "JOIN enrollments e ON e.course_id = c.id WHERE e.student_id = ? "
            "ORDER BY t.weekday, t.starts_at",
            (student["id"],),
        ).fetchall()
        academic_year_row = db.execute(
            "SELECT value FROM settings WHERE key='academic_year'"
        ).fetchone()
        academic_year_value = (
            academic_year_row["value"] if academic_year_row else "2026-2027"
        )
        academic_year_match = re.fullmatch(r"(\d{4})-(\d{4})", academic_year_value)
        boarding_assignment = None
        if academic_year_match:
            boarding_assignment = db.execute(
                "SELECT residency,room FROM boarding_assignments "
                "WHERE student_id=? AND academic_year=?",
                (student["id"], int(academic_year_match.group(1))),
            ).fetchone()
        return render_template(
            "student_dashboard.html",
            user=user,
            student=student,
            grades=grades,
            attendance=attendance,
            homework=homework,
            timetable=timetable,
            boarding_assignment=boarding_assignment,
            passed=sum(1 for grade in grades if grade["score"] >= 50),
            failed=sum(1 for grade in grades if grade["score"] < 50),
        )

    teacher_courses = []
    counts = {}
    if user["role"] == "teacher":
        teacher_courses = db.execute(
            "SELECT c.id, c.name, c.code FROM courses c WHERE c.teacher_id = ? ORDER BY c.name",
            (user["teacher_id"],),
        ).fetchall() if user["teacher_id"] else []
        course_ids = [course["id"] for course in teacher_courses]
        if course_ids:
            marks = ",".join("?" for _ in course_ids)
            counts = {
                "My classes": len(course_ids),
                "My students": db.execute(
                    f"SELECT COUNT(DISTINCT student_id) FROM enrollments WHERE course_id IN ({marks})",
                    course_ids,
                ).fetchone()[0],
                "Exam results": db.execute(
                    f"SELECT COUNT(*) FROM grades g JOIN enrollments e ON e.id=g.enrollment_id WHERE e.course_id IN ({marks})",
                    course_ids,
                ).fetchone()[0],
                "Attendance records": db.execute(
                    f"SELECT COUNT(*) FROM attendance a JOIN enrollments e ON e.id=a.enrollment_id WHERE e.course_id IN ({marks})",
                    course_ids,
                ).fetchone()[0],
            }
    elif user["role"] in {"manager", "admin"}:
        counts = {
            "Students": db.execute("SELECT COUNT(*) FROM students").fetchone()[0],
            "Teachers": db.execute("SELECT COUNT(*) FROM teachers").fetchone()[0],
            "Exam results": db.execute("SELECT COUNT(*) FROM grades").fetchone()[0],
            "Attendance records": db.execute("SELECT COUNT(*) FROM attendance").fetchone()[0],
        }
    elif user["role"] in {"super_admin", "manager", "admin"}:
        counts = {
            "Students": db.execute("SELECT COUNT(*) FROM students").fetchone()[0],
            "Teachers": db.execute("SELECT COUNT(*) FROM teachers").fetchone()[0],
            "Classes": db.execute("SELECT COUNT(*) FROM courses").fetchone()[0],
            "Enrollments": db.execute("SELECT COUNT(*) FROM enrollments").fetchone()[0],
        }
    else:
        counts = {}
    if not module_states["attendance"]:
        counts.pop("Attendance records", None)
    if not module_states["teachers"]:
        counts.pop("Teachers", None)
    staff_user = user["role"] in STAFF_ROLES
    announcements = db.execute(
        "SELECT title, body FROM announcements ORDER BY published_at DESC LIMIT 3"
    ).fetchall() if staff_user else []
    appointments = db.execute(
        "SELECT title, starts_at, notes FROM appointments "
        "WHERE date(starts_at) >= date('now') ORDER BY starts_at LIMIT 5"
    ).fetchall() if staff_user else []
    return render_template(
        "dashboard.html",
        user=user,
        role_label=ROLE_LABELS[user["role"]],
        greeting="Good morning" if user["role"] == "teacher" else "Good day",
        greeting_name=user["teacher_name"] if user["role"] == "teacher" else user["username"],
        counts=counts,
        announcements=announcements,
        appointments=appointments,
        teacher_courses=teacher_courses,
        groups=PORTAL_GROUPS,
        index_layout=_index_layout(
            (
                db.execute(
                    "SELECT value FROM settings WHERE key='index_layout'"
                ).fetchone() or {"value": "modern_grid"}
            )["value"]
        ),
    )


@bp.route("/summer-school", methods=("GET", "POST"))
@roles_required("super_admin", "admin")
def summer_school():
    _require_module("summer_school")
    db = get_db()
    if request.method == "POST":
        academic_year = request.form.get("academic_year", "").strip()
        student_type = request.form.get("student_type", "").strip()
        student_id = _optional_int(request.form.get("student_id"))
        full_name = request.form.get("full_name", "").strip()
        gender = request.form.get("gender", "").strip()
        father_name = request.form.get("father_name", "").strip()
        father_phone = request.form.get("father_phone", "").strip()
        mother_name = request.form.get("mother_name", "").strip()
        mother_phone = request.form.get("mother_phone", "").strip()
        home_address = request.form.get("home_address", "").strip()
        school_of_origin = request.form.get("school_of_origin", "").strip()
        if not re.fullmatch(r"\d{4}-\d{4}", academic_year):
            flash("Select a valid academic year.", "error")
        elif student_type not in {"internal", "external"}:
            flash("Choose internal or external student.", "error")
        elif gender not in {"Female", "Male", "Other"}:
            flash("Select a valid gender.", "error")
        elif student_type == "internal" and not student_id:
            flash("Choose an enrolled school student.", "error")
        elif student_type == "external" and not all(
            (
                full_name, father_name, father_phone, mother_name,
                mother_phone, home_address, school_of_origin,
            )
        ):
            flash(
                "Complete all required external applicant and School of Origin details.",
                "error",
            )
        else:
            if student_type == "internal":
                student = db.execute(
                    "SELECT name,gender,father_name,father_phone,mother_name,mother_phone,home_address "
                    "FROM students WHERE id=?",
                    (student_id,),
                ).fetchone()
                if student is None:
                    flash("Choose an existing school student.", "error")
                    return redirect(url_for("views.summer_school"))
                full_name = student["name"]
                gender = student["gender"]
                father_name = student["father_name"]
                father_phone = student["father_phone"]
                mother_name = student["mother_name"]
                mother_phone = student["mother_phone"]
                home_address = student["home_address"]
                school_of_origin = ""
            db.execute(
                "INSERT INTO summer_school_records "
                "(academic_year,student_type,student_id,full_name,gender,father_name,father_phone,"
                "mother_name,mother_phone,home_address,school_of_origin) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    academic_year, student_type,
                    student_id if student_type == "internal" else None,
                    full_name, gender, father_name, father_phone, mother_name,
                    mother_phone, home_address, school_of_origin,
                ),
            )
            db.commit()
            flash("Summer school applicant registered.", "success")
            return redirect(url_for("views.summer_school", year=academic_year))

    selected_year = request.args.get("year", "").strip()
    query_text = request.args.get("q", "").strip()
    where = ["archived=0"]
    params = []
    if selected_year:
        where.append("academic_year=?")
        params.append(selected_year)
    if query_text:
        where.append(
            "(full_name LIKE ? OR school_of_origin LIKE ? OR father_name LIKE ? "
            "OR mother_name LIKE ? OR academic_year LIKE ?)"
        )
        search = f"%{query_text}%"
        params.extend([search] * 5)
    records = db.execute(
        "SELECT * FROM summer_school_records WHERE "
        + " AND ".join(where)
        + " ORDER BY academic_year DESC,full_name",
        params,
    ).fetchall()
    years = db.execute(
        "SELECT DISTINCT academic_year FROM summer_school_records ORDER BY academic_year DESC"
    ).fetchall()
    students = db.execute(
        "SELECT id,name,gender,father_name,father_phone,mother_name,mother_phone,home_address "
        "FROM students ORDER BY name"
    ).fetchall()
    return render_template(
        "summer_school.html", records=records, years=years, students=students,
        selected_year=selected_year, query_text=query_text, archived=False,
    )


@bp.get("/summer-school/archive")
@roles_required("super_admin", "admin")
def summer_school_archive():
    _require_module("summer_school")
    db = get_db()
    selected_year = request.args.get("year", "").strip()
    query_text = request.args.get("q", "").strip()
    where = ["archived=1"]
    params = []
    if selected_year:
        where.append("academic_year=?")
        params.append(selected_year)
    if query_text:
        where.append(
            "(full_name LIKE ? OR school_of_origin LIKE ? OR father_name LIKE ? "
            "OR mother_name LIKE ? OR academic_year LIKE ?)"
        )
        search = f"%{query_text}%"
        params.extend([search] * 5)
    records = db.execute(
        "SELECT * FROM summer_school_records WHERE "
        + " AND ".join(where)
        + " ORDER BY academic_year DESC,full_name",
        params,
    ).fetchall()
    years = db.execute(
        "SELECT DISTINCT academic_year FROM summer_school_records "
        "WHERE archived=1 ORDER BY academic_year DESC"
    ).fetchall()
    return render_template(
        "summer_school.html", records=records, years=years, students=[],
        selected_year=selected_year, query_text=query_text, archived=True,
    )


@bp.post("/summer-school/<int:record_id>/archive")
@roles_required("super_admin", "admin")
def archive_summer_school_record(record_id):
    _require_module("summer_school")
    archived = request.form.get("archived") == "1"
    cursor = get_db().execute(
        "UPDATE summer_school_records SET archived=? WHERE id=?",
        (int(archived), record_id),
    )
    if not cursor.rowcount:
        abort(404)
    get_db().commit()
    flash(
        "Summer school cohort record archived." if archived else "Record restored to active summer school.",
        "success",
    )
    endpoint = "views.summer_school_archive" if archived else "views.summer_school"
    return redirect(url_for(endpoint))


@bp.post("/language")
def change_language():
    language = request.form.get("language", "")
    if language not in LANGUAGES:
        abort(400)
    session["language"] = language
    endpoint = "views.dashboard" if session.get("user_id") else "auth.login"
    return redirect(url_for(endpoint))


@bp.get("/set_language/<lang_code>")
def set_language(lang_code):
    if lang_code not in LANGUAGES:
        abort(404)
    session["language"] = lang_code
    referrer = request.referrer
    if referrer:
        parsed = urlsplit(referrer)
        if parsed.netloc == request.host and parsed.scheme in {"http", "https"}:
            return redirect(referrer)
    endpoint = "views.dashboard" if session.get("user_id") else "auth.login"
    return redirect(url_for(endpoint))


@bp.route("/settings/academic-year", methods=("GET", "POST"))
@roles_required("super_admin")
def academic_year_settings():
    db = get_db()
    current_year_row = db.execute(
        "SELECT value FROM settings WHERE key='academic_year'"
    ).fetchone()
    current_year = current_year_row["value"] if current_year_row else "2026-2027"
    if request.method == "POST":
        academic_year = request.form.get("academic_year", "").strip()
        match = re.fullmatch(r"(\d{4})-(\d{4})", academic_year)
        if not match or int(match.group(2)) != int(match.group(1)) + 1:
            flash("Enter an academic year in YYYY-YYYY format with consecutive years.", "error")
        elif int(match.group(1)) < 2000:
            flash("Academic year must start in 2000 or later.", "error")
        else:
            db.execute(
                "INSERT INTO settings(key,value) VALUES('academic_year',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (academic_year,),
            )
            db.commit()
            flash("Academic year settings saved.", "success")
            return redirect(url_for("views.academic_year_settings"))
    return render_template(
        "academic_settings.html",
        academic_year=request.form.get("academic_year", current_year),
    )


@bp.route("/settings", methods=("GET", "POST"))
@roles_required("super_admin")
def settings():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action", "save_branding")
        if action == "save_modules":
            updates = []
            for name in MODULE_DEFAULTS:
                posted_value = request.form.get("module_" + name)
                if posted_value not in (None, "1"):
                    abort(400)
                updates.append(
                    ("module_" + name, "1" if posted_value == "1" else "0")
                )
            db.executemany(
                "INSERT INTO settings (key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                updates,
            )
            db.commit()
            flash("Module settings saved.", "success")
            return redirect(url_for("views.settings"))
        if action == "toggle_module":
            module_name = request.form.get("module_name", "")
            if module_name not in MODULE_DEFAULTS:
                abort(400)
            enabled = _module_states(db)[module_name]
            db.execute(
                "INSERT INTO settings (key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("module_" + module_name, "0" if enabled else "1"),
            )
            db.commit()
            flash("Module visibility updated.", "success")
            return redirect(url_for("views.settings"))
        if action == "update_layout":
            layout = request.form.get("index_layout", "")
            if layout not in INDEX_LAYOUTS:
                flash("Choose a supported dashboard layout.", "error")
                return redirect(url_for("views.settings"))
            db.execute(
                "INSERT INTO settings (key,value) VALUES ('index_layout',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (layout,),
            )
            db.commit()
            flash("Dashboard layout saved.", "success")
            return redirect(url_for("views.settings"))
        if action == "add_navigation_item":
            target_key = request.form.get("target", "")
            target = NAVIGATION_TARGETS.get(target_key)
            category = request.form.get("category", "").strip()
            title = request.form.get("title", "").strip()
            icon = request.form.get("icon", "").strip()
            if (
                target is None
                or not category
                or len(category) > 100
                or not title
                or len(title) > 100
                or not icon
                or len(icon) > 50
            ):
                flash("Enter a valid navigation title, category, icon, and destination.", "error")
                return redirect(url_for("views.settings"))
            display_order = db.execute(
                "SELECT COALESCE(MAX(display_order),-1)+1 FROM navigation_items"
            ).fetchone()[0]
            db.execute(
                "INSERT INTO navigation_items "
                "(category,title,icon,url_endpoint,route_arg,is_active,display_order) "
                "VALUES (?,?,?,?,?,1,?)",
                (
                    category, title, icon, target["endpoint"],
                    target["route_arg"], display_order,
                ),
            )
            db.commit()
            flash("Navigation item added.", "success")
            return redirect(url_for("views.settings"))
        if action in {"toggle_navigation_item", "delete_navigation_item", "update_navigation_order"}:
            item_id = request.form.get("item_id", "")
            if not item_id.isdigit():
                abort(400)
            item = db.execute(
                "SELECT id,is_active FROM navigation_items WHERE id=?",
                (int(item_id),),
            ).fetchone()
            if item is None:
                abort(404)
            if action == "toggle_navigation_item":
                db.execute(
                    "UPDATE navigation_items SET is_active=? WHERE id=?",
                    (0 if item["is_active"] else 1, item["id"]),
                )
                flash("Navigation item visibility updated.", "success")
            elif action == "delete_navigation_item":
                db.execute("DELETE FROM navigation_items WHERE id=?", (item["id"],))
                flash("Navigation item deleted.", "success")
            else:
                order_value = request.form.get("display_order", "")
                if not order_value.isdigit() or int(order_value) > 9999:
                    flash("Enter an order between 0 and 9999.", "error")
                    return redirect(url_for("views.settings"))
                db.execute(
                    "UPDATE navigation_items SET display_order=? WHERE id=?",
                    (int(order_value), item["id"]),
                )
                flash("Navigation order updated.", "success")
            db.commit()
            return redirect(url_for("views.settings"))
        if action != "save_branding":
            abort(400)

        existing_settings = {
            row["key"]: row["value"]
            for row in db.execute("SELECT key,value FROM settings").fetchall()
        }
        school_name = request.form.get("school_name", "").strip()
        primary_color = request.form.get("primary_color", "")
        login_background = request.form.get("login_background", "")
        sidebar_color = request.form.get(
            "sidebar_color", existing_settings.get("sidebar_color", "#123c73")
        )
        landing_title = request.form.get(
            "landing_title", existing_settings.get("landing_title", "")
        ).strip()
        landing_text = request.form.get(
            "landing_text", existing_settings.get("landing_text", "")
        ).strip()
        index_welcome_text = request.form.get(
            "index_welcome_text", existing_settings.get("index_welcome_text", "")
        ).strip()
        language = request.form.get("language", existing_settings.get("language", "en"))
        if not school_name or len(school_name) > 120:
            flash("Enter a school name of 1–120 characters.", "error")
        elif not landing_title or len(landing_title) > 160 or len(landing_text) > 2000:
            flash("Enter a landing-page title and text within the allowed length.", "error")
        elif language not in LANGUAGES:
            flash("Choose a supported language for the portal.", "error")
        elif not re.fullmatch(r"#[0-9a-fA-F]{6}", primary_color):
            flash("Choose a valid primary color.", "error")
        elif not re.fullmatch(r"#[0-9a-fA-F]{6}", login_background):
            flash("Choose a valid background color.", "error")
        elif not re.fullmatch(r"#[0-9a-fA-F]{6}", sidebar_color):
            flash("Choose a valid sidebar color.", "error")
        elif len(index_welcome_text) > 255:
            flash("Dashboard welcome text must be 255 characters or fewer.", "error")
        else:
            updates = {
                "school_name": school_name,
                "primary_color": primary_color,
                "login_background": login_background,
                "landing_title": landing_title,
                "landing_text": landing_text,
                "index_welcome_text": index_welcome_text,
                "sidebar_color": sidebar_color,
                "language": language,
                "ai_enabled": "1" if request.form.get("ai_enabled") == "1" else "0",
            }
            for upload_key, setting_key in (
                ("logo", "logo_filename"),
                ("landing_image", "landing_image_filename"),
                ("hero_image", "hero_image_filename"),
            ):
                upload = request.files.get(upload_key)
                if upload and upload.filename:
                    try:
                        updates[setting_key] = _store_uploaded_image(upload)
                    except ValueError as error:
                        flash(str(error), "error")
                        values = {
                            row["key"]: row["value"]
                            for row in db.execute("SELECT key,value FROM settings").fetchall()
                        }
                        values.update(updates)
                        return render_template(
                            "settings.html", **_settings_context(db, values)
                        )

            db.executemany(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                updates.items(),
            )
            db.commit()
            session["language"] = language
            flash("School branding saved.", "success")
            return redirect(url_for("views.settings"))
    values = {
        row["key"]: row["value"]
        for row in db.execute("SELECT key, value FROM settings").fetchall()
    }
    return render_template("settings.html", **_settings_context(db, values))


@bp.get("/export/students")
@roles_required("super_admin", "manager", "admin")
def export_students():
    db = get_db()
    current_year_row = db.execute(
        "SELECT value FROM settings WHERE key='academic_year'"
    ).fetchone()
    academic_year = request.args.get(
        "academic_year",
        current_year_row["value"] if current_year_row else "2026-2027",
    ).strip()
    match = re.fullmatch(r"(\d{4})-(\d{4})", academic_year)
    if not match or int(match.group(2)) != int(match.group(1)) + 1:
        abort(400)
    students = db.execute(
        "SELECT s.id,s.name,s.grade_level,c.name AS class_name,s.gender,? AS academic_year,"
        "COALESCE(b.residency,'Day') AS status "
        "FROM enrollments e JOIN students s ON s.id=e.student_id "
        "JOIN courses c ON c.id=e.course_id "
        "LEFT JOIN boarding_assignments b ON b.student_id=s.id AND b.academic_year=e.academic_year "
        "WHERE e.academic_year=? ORDER BY c.name,s.name",
        (academic_year, int(match.group(1))),
    ).fetchall()
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(("ID", "Class", "Student Full Name", "Gender", "Academic Year", "Status"))
    for student in students:
        values = (
            student["id"], student["class_name"], student["name"],
            student["gender"], student["academic_year"], student["status"],
        )
        writer.writerow(
            tuple(
                _safe_csv_value(value)
                for value in values
            )
        )
    response = make_response("\ufeff" + stream.getvalue())
    response.headers["Content-Disposition"] = (
        f'attachment; filename="student_records_{academic_year}.csv"'
    )
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    return response


@bp.get("/export/report/<int:student_id>")
@roles_required("super_admin", "manager", "admin")
def export_student_report(student_id):
    db = get_db()
    student = db.execute(
        "SELECT id,name,grade_level FROM students WHERE id=?", (student_id,)
    ).fetchone()
    if student is None:
        abort(404)
    year_row = db.execute(
        "SELECT value FROM settings WHERE key='academic_year'"
    ).fetchone()
    academic_year = year_row["value"] if year_row else "2026-2027"
    match = re.fullmatch(r"(\d{4})-(\d{4})", academic_year)
    year_start = int(match.group(1)) if match else date.today().year
    enrollment = db.execute(
        "SELECT COUNT(*) AS total FROM enrollments WHERE student_id=? AND academic_year=?",
        (student_id, year_start),
    ).fetchone()["total"]
    grade_stats = db.execute(
        "SELECT COUNT(*) AS total,AVG(g.score) AS average,"
        "SUM(CASE WHEN g.score>=50 THEN 1 ELSE 0 END) AS passed "
        "FROM grades g JOIN enrollments e ON e.id=g.enrollment_id "
        "WHERE e.student_id=? AND e.academic_year=?",
        (student_id, year_start),
    ).fetchone()
    attendance_stats = db.execute(
        "SELECT COUNT(*) AS total,"
        "SUM(CASE WHEN a.status='Absent' THEN 1 ELSE 0 END) AS absent "
        "FROM attendance a JOIN enrollments e ON e.id=a.enrollment_id "
        "WHERE e.student_id=? AND e.academic_year=?",
        (student_id, year_start),
    ).fetchone()
    meeting_count = db.execute(
        "SELECT COUNT(*) FROM parent_meetings WHERE student_id=?", (student_id,)
    ).fetchone()[0]
    health_count = db.execute(
        "SELECT COUNT(*) FROM health_reports WHERE student_id=?", (student_id,)
    ).fetchone()[0]
    boarding = db.execute(
        "SELECT residency,room FROM boarding_assignments "
        "WHERE student_id=? AND academic_year=?",
        (student_id, year_start),
    ).fetchone()
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(("Wise House School - Official Student Summary",))
    writer.writerow(("Student", _safe_csv_value(student["name"])))
    writer.writerow(("Class / Grade", _safe_csv_value(student["grade_level"])))
    writer.writerow(("Academic Year", academic_year))
    writer.writerow(("Enrolled", "Yes" if enrollment else "No"))
    writer.writerow(("Grade Records", grade_stats["total"]))
    writer.writerow(("Average Score", grade_stats["average"] or ""))
    writer.writerow(("Passed Exams", grade_stats["passed"] or 0))
    writer.writerow(("Attendance Records", attendance_stats["total"]))
    writer.writerow(("Absences", attendance_stats["absent"] or 0))
    writer.writerow(("Parent Meeting Records", meeting_count))
    writer.writerow(("Health & Incident Alerts", health_count))
    writer.writerow(("Boarding Status", boarding["residency"] if boarding else "Day"))
    if boarding and boarding["room"]:
        writer.writerow(("Boarding Room", _safe_csv_value(boarding["room"])))
    response = make_response("\ufeff" + stream.getvalue())
    response.headers["Content-Disposition"] = (
        f'attachment; filename="student_{student_id}_report.csv"'
    )
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    return response


def _safe_csv_value(value):
    if isinstance(value, str) and value[:1] in {"=", "+", "-", "@"}:
        return "'" + value
    return value


@bp.get("/predica")
@roles_required(*STAFF_ROLES)
def predica():
    db = get_db()
    user = _current_user()
    course_scope, course_params = _course_scope(user)
    school_year_row = db.execute(
        "SELECT value FROM settings WHERE key='academic_year'"
    ).fetchone()
    school_year = school_year_row["value"] if school_year_row else "2026-2027"
    match = re.fullmatch(r"(\d{4})-(\d{4})", school_year)
    academic_year = int(match.group(1)) if match else date.today().year
    records = db.execute(
        "SELECT s.id AS student_id,s.name,s.grade_level,c.name AS course_name,"
        "grade_summary.average_score,COALESCE(grade_summary.grade_count,0) AS grade_count,"
        "COALESCE(attendance_summary.attendance_count,0) AS attendance_count,"
        "COALESCE(attendance_summary.present_count,0) AS present_count,"
        "COALESCE(attendance_summary.late_count,0) AS late_count "
        "FROM enrollments e JOIN students s ON s.id=e.student_id "
        "JOIN courses c ON c.id=e.course_id "
        "LEFT JOIN (SELECT enrollment_id,AVG(score) AS average_score,COUNT(*) AS grade_count "
        "FROM grades GROUP BY enrollment_id) grade_summary "
        "ON grade_summary.enrollment_id=e.id "
        "LEFT JOIN (SELECT enrollment_id,COUNT(*) AS attendance_count,"
        "SUM(CASE WHEN status='Present' THEN 1 ELSE 0 END) AS present_count,"
        "SUM(CASE WHEN status='Late' THEN 1 ELSE 0 END) AS late_count "
        "FROM attendance GROUP BY enrollment_id) attendance_summary "
        "ON attendance_summary.enrollment_id=e.id "
        "WHERE e.academic_year=?" + course_scope + " ORDER BY c.name,s.name",
        (academic_year,) + course_params,
    ).fetchall()
    predictions = []
    for row in records:
        attendance_count = row["attendance_count"]
        attendance_rate = (
            (row["present_count"] + row["late_count"]) * 100 / attendance_count
            if attendance_count else None
        )
        average_score = row["average_score"]
        if average_score is None:
            predicted_score = None
            risk_level = "Insufficient data"
        else:
            attendance_factor = attendance_rate / 100 if attendance_rate is not None else 1
            predicted_score = round(
                min(100, average_score * (0.75 + 0.25 * attendance_factor)), 1
            )
            if predicted_score < 50 or (
                attendance_rate is not None and attendance_rate < 70
            ):
                risk_level = "High"
            elif predicted_score < 65 or (
                attendance_rate is not None and attendance_rate < 85
            ):
                risk_level = "Medium"
            else:
                risk_level = "Low"
        predictions.append(
            {
                **dict(row),
                "predicted_score": predicted_score,
                "attendance_rate": (
                    round(attendance_rate, 1)
                    if attendance_rate is not None else None
                ),
                "risk_level": risk_level,
            }
        )
    return render_template("predica.html", predictions=predictions)


@bp.post("/settings/ai-suggest")
@roles_required("super_admin")
def ai_suggest_landing_copy():
    db = get_db()
    enabled = db.execute(
        "SELECT value FROM settings WHERE key='ai_enabled'"
    ).fetchone()
    api_key = current_app.config["AI_API_KEY"]
    if not enabled or enabled["value"] != "1":
        return jsonify(error="Enable AI assistance in Settings first."), 403
    if not api_key:
        return jsonify(error="AI assistance is not configured. Set AI_API_KEY on the server."), 503
    current_title = db.execute(
        "SELECT value FROM settings WHERE key='landing_title'"
    ).fetchone()["value"]
    current_text = db.execute(
        "SELECT value FROM settings WHERE key='landing_text'"
    ).fetchone()["value"]
    payload = json.dumps(
        {
            "model": current_app.config["AI_MODEL"],
            "messages": [
                {
                    "role": "system",
                    "content": "Write concise, welcoming public school homepage copy in English. "
                    "Return only JSON with string keys title and text. Never request or include private student data.",
                },
                {
                    "role": "user",
                    "content": f"Improve this public school homepage text. Title: {current_title}. Text: {current_text}",
                },
            ],
            "response_format": {"type": "json_object"},
        }
    ).encode("utf-8")
    try:
        outbound = Request(
            current_app.config["AI_API_URL"],
            data=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(outbound, timeout=15) as response:
            result = json.loads(response.read(128 * 1024).decode("utf-8"))
        suggestion = json.loads(result["choices"][0]["message"]["content"])
        title, text = suggestion.get("title"), suggestion.get("text")
        if not isinstance(title, str) or not title.strip() or not isinstance(text, str) or not text.strip():
            raise ValueError("AI response did not include a title and text.")
    except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as error:
        current_app.logger.exception("AI landing-copy request failed")
        return jsonify(error=f"AI suggestion failed: {error}"), 502
    return jsonify(title=title.strip()[:160], text=text.strip()[:2000])


@bp.route("/users", methods=("GET", "POST"))
@roles_required("super_admin")
def users():
    db = get_db()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower() or None
        password = request.form.get("password", "")
        role = request.form.get("role", "")
        teacher_id = _optional_int(request.form.get("teacher_id"))
        student_id = _optional_int(request.form.get("student_id"))
        if not username or len(username) > 80:
            flash("Username must contain 1–80 characters.", "error")
        elif role not in set(ROLES) - {"super_admin"}:
            flash("Select a valid role.", "error")
        elif len(password) < 12:
            flash("Password must contain at least 12 characters.", "error")
        elif role == "teacher" and not teacher_id:
            flash("Select the teacher profile assigned to this account.", "error")
        elif role in ("student", "parent") and not student_id:
            flash("Select the student profile linked to this account.", "error")
        else:
            try:
                db.execute(
                    "INSERT INTO users (username, email, password_hash, role, teacher_id, student_id) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        username, email,
                        generate_password_hash(password, method="pbkdf2:sha256"),
                        role, teacher_id if role == "teacher" else None,
                        student_id if role in ("student", "parent") else None,
                    ),
                )
                db.commit()
                flash("User account created.", "success")
                return redirect(url_for("views.users"))
            except sqlite3.IntegrityError:
                db.rollback()
                flash("Username or email is already in use, or the linked profile is invalid.", "error")
    accounts = db.execute(
        "SELECT u.id, u.username, u.email, u.role, u.is_active, "
        "t.name AS teacher_name, s.name AS student_name FROM users u "
        "LEFT JOIN teachers t ON t.id=u.teacher_id "
        "LEFT JOIN students s ON s.id=u.student_id ORDER BY u.id"
    ).fetchall()
    teachers = db.execute("SELECT id, name FROM teachers ORDER BY name").fetchall()
    students = db.execute("SELECT id, name FROM students ORDER BY name").fetchall()
    return render_template(
        "users.html", accounts=accounts, teachers=teachers, students=students,
        roles=ROLES, role_labels=ROLE_LABELS,
    )


@bp.post("/users/<int:user_id>/status")
@roles_required("super_admin")
def user_status(user_id):
    if user_id == session["user_id"]:
        flash("You cannot deactivate your own account.", "error")
    else:
        db = get_db()
        current = db.execute(
            "SELECT role, is_active FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if current is None:
            abort(404)
        active_admins = db.execute(
            "SELECT COUNT(*) FROM users WHERE role='super_admin' AND is_active=1"
        ).fetchone()[0]
        if current["role"] == "super_admin" and current["is_active"] and active_admins <= 1:
            flash("The last active super administrator cannot be deactivated.", "error")
            return redirect(url_for("views.users"))
        cursor = db.execute(
            "UPDATE users SET is_active = 1 - is_active WHERE id = ?", (user_id,)
        )
        if not cursor.rowcount:
            abort(404)
        db.commit()
        flash("Account status updated.", "success")
    return redirect(url_for("views.users"))


@bp.post("/users/<int:user_id>/role")
@roles_required("super_admin")
def update_user_role(user_id):
    if user_id == session["user_id"]:
        flash("Your own super administrator role cannot be changed.", "error")
        return redirect(url_for("views.users"))
    role = request.form.get("role", "")
    teacher_id = _optional_int(request.form.get("teacher_id"))
    student_id = _optional_int(request.form.get("student_id"))
    if role not in set(ROLES) - {"super_admin"}:
        flash("Select a valid non-administrator role.", "error")
    elif role == "teacher" and not get_db().execute(
        "SELECT 1 FROM teachers WHERE id=?", (teacher_id,)
    ).fetchone():
        flash("Select an existing teacher profile.", "error")
    elif role in ("student", "parent") and not get_db().execute(
        "SELECT 1 FROM students WHERE id=?", (student_id,)
    ).fetchone():
        flash("Select an existing student profile.", "error")
    else:
        cursor = get_db().execute(
            "UPDATE users SET role=?,teacher_id=?,student_id=? WHERE id=? AND role!='super_admin'",
            (
                role,
                teacher_id if role == "teacher" else None,
                student_id if role in ("student", "parent") else None,
                user_id,
            ),
        )
        if not cursor.rowcount:
            abort(404)
        get_db().commit()
        flash("Account role and linked profile updated.", "success")
    return redirect(url_for("views.users"))


@bp.post("/users/<int:user_id>/delete")
@roles_required("super_admin")
def delete_user(user_id):
    if user_id == session["user_id"]:
        flash("You cannot delete your own account.", "error")
        return redirect(url_for("views.users"))
    db = get_db()
    account = db.execute(
        "SELECT role,is_active FROM users WHERE id=?", (user_id,)
    ).fetchone()
    if account is None:
        abort(404)
    if account["role"] == "super_admin" and account["is_active"]:
        active_admins = db.execute(
            "SELECT COUNT(*) FROM users WHERE role='super_admin' AND is_active=1"
        ).fetchone()[0]
        if active_admins <= 1:
            flash("The last active super administrator cannot be deleted.", "error")
            return redirect(url_for("views.users"))
    db.execute("DELETE FROM users WHERE id=?", (user_id,))
    db.commit()
    flash("User account deleted.", "success")
    return redirect(url_for("views.users"))


@bp.get("/profile")
@roles_required(*ROLES)
def profile():
    return render_template("profile.html", user=_current_user(), role_labels=ROLE_LABELS)


@bp.route("/admin/classes", methods=("GET", "POST"))
@roles_required("super_admin")
def manage_classes():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "add":
            name = request.form.get("name", "").strip()
            code = request.form.get("code", "").strip()
            teacher_id = _optional_int(request.form.get("teacher_id"))
            if not name or len(name) > 120:
                flash("Class name must contain 1–120 characters.", "error")
            elif not code or len(code) > 40:
                flash("Class code must contain 1–40 characters.", "error")
            elif not teacher_id:
                flash("Select an assigned teacher.", "error")
            elif not db.execute(
                "SELECT 1 FROM teachers WHERE id=?", (teacher_id,)
            ).fetchone():
                flash("Select an existing teacher profile.", "error")
            else:
                try:
                    db.execute(
                        "INSERT INTO courses(name,code,teacher_id) VALUES(?,?,?)",
                        (name, code, teacher_id),
                    )
                    db.commit()
                    flash("Class added successfully.", "success")
                    return redirect(url_for("views.manage_classes"))
                except sqlite3.IntegrityError as error:
                    db.rollback()
                    flash(_integrity_message(error), "error")
        elif action == "delete":
            course_id = _optional_int(request.form.get("course_id"))
            if not course_id:
                abort(400)
            course = db.execute(
                "SELECT id FROM courses WHERE id=?", (course_id,)
            ).fetchone()
            if course is None:
                abort(404)
            dependencies = db.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM enrollments WHERE course_id=?) + "
                "(SELECT COUNT(*) FROM homework WHERE course_id=?) + "
                "(SELECT COUNT(*) FROM timetable WHERE course_id=?) AS total",
                (course_id, course_id, course_id),
            ).fetchone()["total"]
            if dependencies:
                flash(
                    "This class has dependent enrollments, homework, or timetable entries and cannot be deleted.",
                    "error",
                )
            else:
                db.execute("DELETE FROM courses WHERE id=?", (course_id,))
                db.commit()
                flash("Class deleted successfully.", "success")
                return redirect(url_for("views.manage_classes"))
        else:
            abort(400)

    academic_year_row = db.execute(
        "SELECT value FROM settings WHERE key='academic_year'"
    ).fetchone()
    academic_year = academic_year_row["value"] if academic_year_row else "2026-2027"
    year_match = re.fullmatch(r"(\d{4})-(\d{4})", academic_year)
    year_start = int(year_match.group(1)) if year_match else date.today().year
    classes = db.execute(
        "SELECT c.id,c.name,c.code,c.teacher_id,t.name AS teacher_name,"
        "COUNT(DISTINCT e.id) AS enrollment_count,"
        "COALESCE(SUM(CASE WHEN e.academic_year=? THEN 1 ELSE 0 END),0) AS current_year_count "
        "FROM courses c JOIN teachers t ON t.id=c.teacher_id "
        "LEFT JOIN enrollments e ON e.course_id=c.id "
        "GROUP BY c.id ORDER BY c.name",
        (year_start,),
    ).fetchall()
    rosters = {}
    for course in classes:
        rosters[course["id"]] = db.execute(
            "SELECT DISTINCT s.name,s.grade_level,e.academic_year "
            "FROM enrollments e JOIN students s ON s.id=e.student_id "
            "WHERE e.course_id=? ORDER BY e.academic_year DESC,s.name",
            (course["id"],),
        ).fetchall()
    teachers = db.execute(
        "SELECT id,name,subject FROM teachers ORDER BY name"
    ).fetchall()
    return render_template(
        "manage_classes.html",
        classes=classes,
        rosters=rosters,
        teachers=teachers,
        academic_year=academic_year,
    )


@bp.route("/admin/parent-meetings", methods=("GET", "POST"))
@roles_required("super_admin", "manager", "admin")
def parent_meetings():
    db = get_db()
    if request.method == "POST":
        meeting_id = _optional_int(request.form.get("meeting_id"))
        student_id = _optional_int(request.form.get("student_id"))
        meeting_date = request.form.get("meeting_date", "").strip()
        notes = request.form.get("notes", "").strip()
        if not student_id or not db.execute(
            "SELECT 1 FROM students WHERE id=?", (student_id,)
        ).fetchone():
            flash("Choose a valid student.", "error")
        elif not _valid_date(meeting_date):
            flash("Enter a valid meeting date.", "error")
        elif not notes or len(notes) > 4000:
            flash("Meeting notes are required and must not exceed 4,000 characters.", "error")
        else:
            if meeting_id:
                cursor = db.execute(
                    "UPDATE parent_meetings SET student_id=?,meeting_date=?,notes=? WHERE id=?",
                    (student_id, meeting_date, notes, meeting_id),
                )
                if not cursor.rowcount:
                    abort(404)
                message = "Parent meeting record updated."
            else:
                db.execute(
                    "INSERT INTO parent_meetings(student_id,meeting_date,notes,created_by) "
                    "VALUES(?,?,?,?)",
                    (student_id, meeting_date, notes, session["user_id"]),
                )
                message = "Parent meeting record saved."
            db.commit()
            flash(message, "success")
            return redirect(url_for("views.parent_meetings"))
    edit_id = request.args.get("edit", type=int)
    editing = None
    if edit_id:
        editing = db.execute(
            "SELECT * FROM parent_meetings WHERE id=?", (edit_id,)
        ).fetchone()
        if editing is None:
            abort(404)
    students = db.execute(
        "SELECT id,name,grade_level FROM students ORDER BY name"
    ).fetchall()
    records = db.execute(
        "SELECT m.id,m.student_id,m.meeting_date,m.notes,m.created_at,"
        "s.name AS student_name,s.grade_level,u.username AS author "
        "FROM parent_meetings m JOIN students s ON s.id=m.student_id "
        "LEFT JOIN users u ON u.id=m.created_by "
        "ORDER BY m.meeting_date DESC,m.id DESC LIMIT 500"
    ).fetchall()
    return render_template(
        "admin_records.html", module="parent-meetings", records=records,
        students=students, editing=editing,
    )


@bp.post("/admin/parent-meetings/<int:meeting_id>/delete")
@roles_required("super_admin", "manager")
def delete_parent_meeting(meeting_id):
    cursor = get_db().execute(
        "DELETE FROM parent_meetings WHERE id=?", (meeting_id,)
    )
    if not cursor.rowcount:
        abort(404)
    get_db().commit()
    flash("Parent meeting record deleted.", "success")
    return redirect(url_for("views.parent_meetings"))


@bp.route("/admin/health-reports", methods=("GET", "POST"))
@roles_required("super_admin", "manager", "admin")
def health_reports():
    db = get_db()
    if request.method == "POST":
        report_id = _optional_int(request.form.get("report_id"))
        student_id = _optional_int(request.form.get("student_id"))
        report_type = request.form.get("report_type", "").strip()
        reported_at = request.form.get("reported_at", "").strip()
        details = request.form.get("details", "").strip()
        if not student_id or not db.execute(
            "SELECT 1 FROM students WHERE id=?", (student_id,)
        ).fetchone():
            flash("Choose a valid student.", "error")
        elif report_type not in {"health", "incident", "emergency"}:
            flash("Choose a valid health or incident report type.", "error")
        elif not _valid_datetime(reported_at):
            flash("Enter a valid report date and time.", "error")
        elif not details or len(details) > 4000:
            flash("Report details are required and must not exceed 4,000 characters.", "error")
        else:
            if report_id:
                cursor = db.execute(
                    "UPDATE health_reports SET student_id=?,report_type=?,reported_at=?,details=? "
                    "WHERE id=?",
                    (student_id, report_type, reported_at, details, report_id),
                )
                if not cursor.rowcount:
                    abort(404)
                message = "Health or incident report updated."
            else:
                db.execute(
                    "INSERT INTO health_reports(student_id,report_type,reported_at,details,created_by) "
                    "VALUES(?,?,?,?,?)",
                    (student_id, report_type, reported_at, details, session["user_id"]),
                )
                message = "Health or incident report saved."
            db.commit()
            flash(message, "success")
            return redirect(url_for("views.health_reports"))
    edit_id = request.args.get("edit", type=int)
    editing = None
    if edit_id:
        editing = db.execute(
            "SELECT * FROM health_reports WHERE id=?", (edit_id,)
        ).fetchone()
        if editing is None:
            abort(404)
    students = db.execute(
        "SELECT id,name,grade_level FROM students ORDER BY name"
    ).fetchall()
    records = db.execute(
        "SELECT h.id,h.student_id,h.report_type,h.reported_at,h.details,h.created_at,"
        "s.name AS student_name,s.grade_level,u.username AS author "
        "FROM health_reports h JOIN students s ON s.id=h.student_id "
        "LEFT JOIN users u ON u.id=h.created_by "
        "ORDER BY h.reported_at DESC,h.id DESC LIMIT 500"
    ).fetchall()
    return render_template(
        "admin_records.html", module="health-reports", records=records,
        students=students, editing=editing,
    )


@bp.post("/admin/health-reports/<int:report_id>/delete")
@roles_required("super_admin", "manager")
def delete_health_report(report_id):
    cursor = get_db().execute(
        "DELETE FROM health_reports WHERE id=?", (report_id,)
    )
    if not cursor.rowcount:
        abort(404)
    get_db().commit()
    flash("Health or incident report deleted.", "success")
    return redirect(url_for("views.health_reports"))


@bp.route("/admin/boarding", methods=("GET", "POST"))
@roles_required("super_admin", "manager", "admin", "boarding_master")
def boarding_management():
    _require_module("boarding")
    db = get_db()
    year_row = db.execute(
        "SELECT value FROM settings WHERE key='academic_year'"
    ).fetchone()
    academic_year = year_row["value"] if year_row else "2026-2027"
    match = re.fullmatch(r"(\d{4})-(\d{4})", academic_year)
    year_start = int(match.group(1)) if match else date.today().year
    if request.method == "POST":
        student_id = _optional_int(request.form.get("student_id"))
        residency = request.form.get("residency", "").strip()
        room = request.form.get("room", "").strip()
        if not student_id or not db.execute(
            "SELECT 1 FROM students WHERE id=?", (student_id,)
        ).fetchone():
            flash("Choose a valid student.", "error")
        elif residency not in {"Boarding", "Day"}:
            flash("Choose Boarding or Day status.", "error")
        elif len(room) > 80:
            flash("Room must not exceed 80 characters.", "error")
        elif residency == "Boarding" and not room:
            flash("Enter a room or hostel for boarding students.", "error")
        else:
            db.execute(
                "INSERT INTO boarding_assignments "
                "(student_id,academic_year,residency,room,updated_by) VALUES(?,?,?,?,?) "
                "ON CONFLICT(student_id,academic_year) DO UPDATE SET "
                "residency=excluded.residency,room=excluded.room,"
                "updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP",
                (student_id, year_start, residency, room if residency == "Boarding" else "", session["user_id"]),
            )
            db.commit()
            flash("Boarding assignment saved.", "success")
            return redirect(url_for("views.boarding_management"))
    students = db.execute(
        "SELECT id,name,grade_level FROM students ORDER BY name"
    ).fetchall()
    records = db.execute(
        "SELECT b.id,b.student_id,b.academic_year,b.residency,b.room,"
        "s.name AS student_name,s.grade_level "
        "FROM boarding_assignments b JOIN students s ON s.id=b.student_id "
        "WHERE b.academic_year=? ORDER BY s.name",
        (year_start,),
    ).fetchall()
    return render_template(
        "boarding.html", students=students, records=records,
        academic_year=academic_year,
    )


@bp.route("/admin/bulksms", methods=("GET", "POST"))
@roles_required("super_admin", "manager", "admin")
def bulk_sms():
    if request.method == "GET":
        return redirect(url_for("views.portal", module="bulk-sms"))
    title = request.form.get("title", "").strip()
    details = request.form.get("description", "").strip()
    if not title or len(title) > 160 or len(details) > 4000:
        flash("Enter a recipient group and message draft within the allowed length.", "error")
        return redirect(url_for("views.portal", module="bulk-sms"))
    db = get_db()
    db.execute(
        "INSERT INTO service_requests(module,title,details,created_by) "
        "VALUES('bulk-sms',?,?,?)",
        (title, details, session["user_id"]),
    )
    db.commit()
    flash(
        "Request recorded. No SMS was sent because no messaging provider is configured.",
        "success",
    )
    return redirect(url_for("views.portal", module="bulk-sms"))


@bp.post("/requests/<int:request_id>/status")
@roles_required("super_admin", "manager", "admin")
def update_request_status(request_id):
    status = request.form.get("status", "")
    module = request.form.get("module", "")
    if status not in {"Open", "In progress", "Complete"}:
        abort(400)
    if module not in {
        "orders", "bulk-sms", "tutoring", "parent-meetings",
    }:
        abort(400)
    cursor = get_db().execute(
        "UPDATE service_requests SET status=? WHERE id=? AND module=?",
        (status, request_id, module),
    )
    if not cursor.rowcount:
        abort(404)
    get_db().commit()
    flash("Request status updated.", "success")
    return redirect(url_for("views.portal", module=module))


@bp.post("/requests/<int:request_id>/delete")
@roles_required("super_admin", "manager", "admin")
def delete_request(request_id):
    module = request.form.get("module", "")
    if module not in {
        "orders", "bulk-sms", "tutoring", "parent-meetings",
    }:
        abort(400)
    cursor = get_db().execute(
        "DELETE FROM service_requests WHERE id=? AND module=?", (request_id, module)
    )
    if not cursor.rowcount:
        abort(404)
    get_db().commit()
    flash("Request deleted.", "success")
    return redirect(url_for("views.portal", module=module))


def _load_attendance_students(db, course_id, attendance_date):
    return db.execute(
        "SELECT e.id AS enrollment_id,s.id AS student_id,s.name,s.grade_level,"
        "a.status FROM enrollments e JOIN students s ON s.id=e.student_id "
        "LEFT JOIN attendance a ON a.enrollment_id=e.id AND a.attendance_date=? "
        "WHERE e.course_id=? ORDER BY s.name",
        (attendance_date, course_id),
    ).fetchall()


def _save_attendance_students(db, students, attendance_date):
    for student in students:
        status = request.form.get(f"status_{student['enrollment_id']}", "").strip()
        if not status:
            db.execute(
                "DELETE FROM attendance WHERE enrollment_id=? AND attendance_date=?",
                (student["enrollment_id"], attendance_date),
            )
        elif status in {"Present", "Absent", "Late", "Excused"}:
            db.execute(
                "INSERT INTO attendance (enrollment_id,attendance_date,status) "
                "VALUES (?,?,?) ON CONFLICT(enrollment_id,attendance_date) "
                "DO UPDATE SET status=excluded.status",
                (student["enrollment_id"], attendance_date, status),
            )
        else:
            abort(400)
    db.commit()


@bp.route("/student-registration", methods=("GET", "POST"))
@roles_required("super_admin", "manager", "admin")
def student_registration():
    db = get_db()
    errors = []
    values = {
        "name": request.form.get("name", "").strip(),
        "email": request.form.get("email", "").strip().lower(),
        "course_id": request.form.get("course_id", "").strip(),
        "father_phone": request.form.get("father_phone", "").strip(),
        "mother_phone": request.form.get("mother_phone", "").strip(),
        "father_occupation": request.form.get("father_occupation", "").strip(),
        "mother_occupation": request.form.get("mother_occupation", "").strip(),
    }
    courses = db.execute(
        "SELECT id,name,code FROM courses ORDER BY name"
    ).fetchall()
    academic_year_row = db.execute(
        "SELECT value FROM settings WHERE key='academic_year'"
    ).fetchone()
    configured_year = academic_year_row["value"] if academic_year_row else "2026-2027"
    year_match = re.fullmatch(r"(\d{4})-(\d{4})", configured_year)
    academic_year = int(year_match.group(1)) if year_match else date.today().year
    requested_registered_course_id = request.args.get(
        "registered_course_id", ""
    ).strip()
    registered_course_id = _optional_int(requested_registered_course_id)
    if requested_registered_course_id and registered_course_id is None:
        abort(400)
    if registered_course_id is not None and not any(
        course["id"] == registered_course_id for course in courses
    ):
        abort(404)
    registered_course = next(
        (course for course in courses if course["id"] == registered_course_id),
        None,
    )
    registered_students = db.execute(
        "SELECT s.id,s.name,s.email,s.grade_level,s.father_name,s.father_phone,"
        "s.father_occupation,s.mother_name,s.mother_phone,s.mother_occupation,"
        "s.home_address,s.profile_picture,"
        "COALESCE((SELECT group_concat(c.name, ', ') FROM enrollments e "
        "JOIN courses c ON c.id=e.course_id "
        "WHERE e.student_id=s.id AND e.academic_year=?),s.grade_level) AS class_names "
        "FROM students s "
        "WHERE (? IS NULL OR EXISTS ("
        "SELECT 1 FROM enrollments filtered "
        "WHERE filtered.student_id=s.id AND filtered.academic_year=? "
        "AND filtered.course_id=?)) "
        "ORDER BY s.name",
        (academic_year, registered_course_id, academic_year, registered_course_id),
    ).fetchall()
    action = request.form.get("action", "register")
    attendance_enabled = _module_states(db)["attendance"]
    if action == "attendance" or (
        not attendance_enabled and request.values.get("tab") == "attendance"
    ):
        _require_module("attendance")
    attendance_date = request.values.get("attendance_date", date.today().isoformat()).strip()
    if not _valid_date(attendance_date):
        flash("Choose a valid attendance date.", "error")
        attendance_date = date.today().isoformat()
    if action == "attendance":
        values["course_id"] = request.form.get("course_id", "").strip()
    elif request.method == "GET":
        values["course_id"] = request.args.get("course_id", "").strip()
    selected_course_id = _optional_int(values["course_id"])
    selected_course = next(
        (course for course in courses if course["id"] == selected_course_id),
        None,
    )
    if selected_course_id is not None and selected_course is None:
        abort(404)
    students = (
        _load_attendance_students(db, selected_course_id, attendance_date)
        if selected_course else []
    )

    if request.method == "POST" and action == "attendance":
        if selected_course is None:
            abort(400)
        if not _valid_date(attendance_date):
            abort(400)
        _save_attendance_students(db, students, attendance_date)
        flash("Attendance saved for the selected class.", "success")
        return redirect(url_for(
            "views.student_registration",
            tab="attendance",
            course_id=selected_course_id,
            attendance_date=attendance_date,
        ))

    if request.method == "POST":
        if not values["name"] or len(values["name"]) > 120:
            errors.append("Enter a student name of 1–120 characters.")
        if (
            len(values["email"]) > 254
            or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", values["email"])
        ):
            errors.append("Enter a valid student email address.")
        if selected_course_id is None:
            errors.append("Choose a valid class.")
        elif not any(course["id"] == selected_course_id for course in courses):
            errors.append("Choose a valid class.")
        for key, label, limit in (
            ("father_phone", "Father's phone", 40),
            ("mother_phone", "Mother's phone", 40),
            ("father_occupation", "Father's occupation", 120),
            ("mother_occupation", "Mother's occupation", 120),
        ):
            if len(values[key]) > limit:
                errors.append(f"{label} must not exceed {limit} characters.")
        if db.execute(
            "SELECT 1 FROM students WHERE lower(email)=lower(?)",
            (values["email"],),
        ).fetchone():
            errors.append("A student with that email address already exists.")

        if not errors:
            try:
                cursor = db.execute(
                    "INSERT INTO students "
                    "(name,email,grade_level,father_phone,mother_phone,"
                    "father_occupation,mother_occupation) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        values["name"],
                        values["email"],
                        next(course["name"] for course in courses if course["id"] == selected_course_id),
                        values["father_phone"],
                        values["mother_phone"],
                        values["father_occupation"],
                        values["mother_occupation"],
                    ),
                )
                db.execute(
                    "INSERT INTO enrollments(student_id,course_id,academic_year) "
                    "VALUES(?,?,?)",
                    (cursor.lastrowid, selected_course_id, academic_year),
                )
                db.commit()
                flash("Student registered and enrolled in the selected class.", "success")
                return redirect(
                    url_for("views.student_registration", tab="student")
                )
            except sqlite3.IntegrityError:
                db.rollback()
                flash("The student could not be registered; verify the email and class.", "error")

    return render_template(
        "student_registration.html",
        courses=courses,
        values=values,
        errors=errors,
        today=date.today().isoformat(),
        attendance_date=attendance_date,
        selected_course=selected_course,
        attendance_students=students,
        registered_students=registered_students,
        registered_course_id=registered_course_id,
        registered_course=registered_course,
        academic_year_label=configured_year,
        attendance_enabled=attendance_enabled,
    )


@bp.route("/attendance", methods=("GET", "POST"))
@roles_required(*ACADEMIC_WRITE_ROLES)
def attendance_roster():
    _require_module("attendance")
    db = get_db()
    user = _current_user()
    course_scope, course_params = _course_scope(user)
    courses = db.execute(
        "SELECT c.id,c.name,c.code FROM courses c WHERE 1=1"
        + course_scope + " ORDER BY c.name",
        course_params,
    ).fetchall()
    course_id = _optional_int(request.values.get("course_id"))
    class_name = request.values.get("class_name", "").strip()
    if course_id is None and class_name:
        matching_course = next(
            (
                course for course in courses
                if course["name"].casefold() == class_name.casefold()
            ),
            None,
        )
        if matching_course is None:
            abort(403 if user["role"] == "teacher" else 404)
        course_id = matching_course["id"]
    attendance_date = request.values.get("attendance_date", date.today().isoformat()).strip()
    if not _valid_date(attendance_date):
        flash("Choose a valid attendance date.", "error")
        attendance_date = date.today().isoformat()
    selected_course = next((course for course in courses if course["id"] == course_id), None)
    if course_id and selected_course is None:
        abort(403 if user["role"] == "teacher" else 404)

    students = (
        _load_attendance_students(db, course_id, attendance_date)
        if selected_course else []
    )

    if request.method == "POST":
        if not selected_course:
            abort(400)
        if not _valid_date(attendance_date):
            abort(400)
        _save_attendance_students(db, students, attendance_date)
        flash("Attendance saved for the selected class.", "success")
        return redirect(
            url_for(
                "views.attendance_roster",
                course_id=course_id,
                attendance_date=attendance_date,
            )
        )
    return render_template(
        "attendance.html",
        courses=courses,
        selected_course=selected_course,
        attendance_date=attendance_date,
        students=students,
    )


@bp.route("/gradebook", methods=("GET", "POST"))
@roles_required(*ACADEMIC_WRITE_ROLES)
def gradebook():
    db = get_db()
    user = _current_user()
    course_scope, course_params = _course_scope(user)
    courses = db.execute(
        "SELECT c.id,c.name,c.code FROM courses c WHERE 1=1"
        + course_scope + " ORDER BY c.name",
        course_params,
    ).fetchall()
    course_id = _optional_int(request.values.get("course_id"))
    exam_name = request.values.get("exam_name", "Exam").strip()
    graded_on = request.values.get("graded_on", date.today().isoformat()).strip()
    selected_course = next((course for course in courses if course["id"] == course_id), None)
    if course_id and selected_course is None:
        abort(403 if user["role"] == "teacher" else 404)
    if request.method == "POST":
        if not selected_course:
            abort(400)
        if not exam_name or len(exam_name) > 100 or not _valid_date(graded_on):
            flash("Enter an exam name (up to 100 characters) and a valid exam date.", "error")
        else:
            students = db.execute(
                "SELECT e.id AS enrollment_id FROM enrollments e "
                "WHERE e.course_id=? ORDER BY e.id",
                (course_id,),
            ).fetchall()
            scores = []
            invalid_score = False
            for student in students:
                value = request.form.get(f"score_{student['enrollment_id']}", "").strip()
                if not value:
                    continue
                try:
                    score = float(value)
                except ValueError:
                    invalid_score = True
                    break
                if not 0 <= score <= 100:
                    invalid_score = True
                    break
                scores.append((student["enrollment_id"], score))
            if invalid_score:
                flash("Every entered score must be a number between 0 and 100.", "error")
            elif not scores:
                flash("Enter at least one score before saving.", "error")
            else:
                for enrollment_id, score in scores:
                    existing = db.execute(
                        "SELECT id FROM grades WHERE enrollment_id=? AND exam_name=? "
                        "AND graded_on=? ORDER BY id DESC LIMIT 1",
                        (enrollment_id, exam_name, graded_on),
                    ).fetchone()
                    if existing:
                        db.execute(
                            "UPDATE grades SET score=? WHERE id=?",
                            (score, existing["id"]),
                        )
                    else:
                        db.execute(
                            "INSERT INTO grades (enrollment_id,score,graded_on,exam_name) "
                            "VALUES (?,?,?,?)",
                            (enrollment_id, score, graded_on, exam_name),
                        )
                db.commit()
                flash("Exam scores saved.", "success")
                return redirect(
                    url_for(
                        "views.gradebook", course_id=course_id,
                        exam_name=exam_name, graded_on=graded_on,
                    )
                )
    rows = []
    if selected_course and exam_name and _valid_date(graded_on):
        rows = db.execute(
            "SELECT e.id AS enrollment_id,s.name,s.grade_level,g.id AS grade_id,g.score "
            "FROM enrollments e JOIN students s ON s.id=e.student_id "
            "LEFT JOIN grades g ON g.id=(SELECT MAX(g2.id) FROM grades g2 "
            "WHERE g2.enrollment_id=e.id AND g2.exam_name=? AND g2.graded_on=?) "
            "WHERE e.course_id=? ORDER BY s.name",
            (exam_name, graded_on, course_id),
        ).fetchall()
    return render_template(
        "gradebook.html",
        courses=courses,
        selected_course=selected_course,
        exam_name=exam_name,
        graded_on=graded_on,
        students=rows,
    )


@bp.post("/gradebook/<int:grade_id>/delete")
@roles_required(*ACADEMIC_WRITE_ROLES)
def delete_grade(grade_id):
    db = get_db()
    user = _current_user()
    query = "DELETE FROM grades WHERE id=?"
    params = [grade_id]
    if user["role"] == "teacher":
        if not user["teacher_id"]:
            abort(403)
        query += (
            " AND enrollment_id IN (SELECT e.id FROM enrollments e "
            "JOIN courses c ON c.id=e.course_id WHERE c.teacher_id=?)"
        )
        params.append(user["teacher_id"])
    cursor = db.execute(query, params)
    if not cursor.rowcount:
        abort(404)
    db.commit()
    flash("Exam score deleted.", "success")
    return redirect(
        url_for(
            "views.gradebook",
            course_id=request.form.get("course_id", type=int),
            exam_name=request.form.get("exam_name", "Exam"),
            graded_on=request.form.get("graded_on", date.today().isoformat()),
        )
    )


@bp.route("/staff-updates", methods=("GET", "POST"))
@roles_required(*STAFF_ROLES)
def staff_updates():
    db = get_db()
    user = _current_user()
    if request.method == "POST":
        update_id = _optional_int(request.form.get("update_id"))
        category = request.form.get("category", "").strip()
        title = request.form.get("title", "").strip()
        body = request.form.get("body", "").strip()
        student_id = _optional_int(request.form.get("student_id"))
        scheduled_at = request.form.get("scheduled_at", "").strip()
        if category not in {"newsletter", "student_log", "meeting", "update"}:
            abort(400)
        if not title or len(title) > 160 or len(body) > 4000:
            flash("Enter a title (up to 160 characters) and text (up to 4,000 characters).", "error")
        elif category == "meeting" and not _valid_datetime(scheduled_at):
            flash("Choose a valid meeting date and time.", "error")
        else:
            if category == "student_log":
                if not student_id:
                    flash("Choose a student for this internal log.", "error")
                    return redirect(url_for("views.staff_updates"))
                _, student_params = _student_scope(user)
                allowed = db.execute(
                    "SELECT s.id FROM students s JOIN enrollments e ON e.student_id=s.id "
                    "JOIN courses c ON c.id=e.course_id WHERE s.id=?"
                    + (" AND c.teacher_id=?" if user["role"] == "teacher" else "")
                    + " LIMIT 1",
                    (student_id, *student_params),
                ).fetchone()
                if not allowed:
                    abort(403)
            else:
                student_id = None
            if update_id:
                existing = db.execute(
                    "SELECT id,created_by FROM staff_updates WHERE id=?", (update_id,)
                ).fetchone()
                if existing is None:
                    abort(404)
                if user["role"] == "teacher" and existing["created_by"] != user["id"]:
                    abort(403)
                db.execute(
                    "UPDATE staff_updates SET category=?,student_id=?,title=?,body=?,"
                    "scheduled_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (category, student_id, title, body, scheduled_at if category == "meeting" else None, update_id),
                )
            else:
                db.execute(
                    "INSERT INTO staff_updates (category,student_id,title,body,created_by,scheduled_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (category, student_id, title, body, user["id"], scheduled_at if category == "meeting" else None),
                )
            db.commit()
            flash("Staff update saved.", "success")
            return redirect(url_for("views.staff_updates"))
    editing_id = request.args.get("edit", type=int)
    editing = None
    if editing_id:
        editing = db.execute(
            "SELECT u.*,s.name AS student_name FROM staff_updates u "
            "LEFT JOIN students s ON s.id=u.student_id WHERE u.id=?",
            (editing_id,),
        ).fetchone()
        if editing is None:
            abort(404)
        if user["role"] == "teacher" and editing["created_by"] != user["id"]:
            abort(403)
    students = db.execute(
        "SELECT DISTINCT s.id,s.name,s.grade_level FROM students s "
        "JOIN enrollments e ON e.student_id=s.id JOIN courses c ON c.id=e.course_id "
        "WHERE 1=1" + (" AND c.teacher_id=?" if user["role"] == "teacher" else "")
        + " ORDER BY s.name",
        (user["teacher_id"],) if user["role"] == "teacher" else (),
    ).fetchall()
    updates = db.execute(
        "SELECT u.*,s.name AS student_name,creator.username AS creator_name "
        "FROM staff_updates u LEFT JOIN students s ON s.id=u.student_id "
        "LEFT JOIN users creator ON creator.id=u.created_by "
        "ORDER BY u.created_at DESC,u.id DESC LIMIT 200"
    ).fetchall()
    return render_template(
        "staff_updates.html", updates=updates, students=students, editing=editing
    )


@bp.post("/staff-updates/<int:update_id>/delete")
@roles_required(*STAFF_ROLES)
def delete_staff_update(update_id):
    db = get_db()
    user = _current_user()
    query = "DELETE FROM staff_updates WHERE id=?"
    params = [update_id]
    if user["role"] == "teacher":
        query += " AND created_by=?"
        params.append(user["id"])
    cursor = db.execute(query, params)
    if not cursor.rowcount:
        abort(404)
    db.commit()
    flash("Staff update deleted.", "success")
    return redirect(url_for("views.staff_updates"))


@bp.route("/manage/<resource>", methods=("GET", "POST"))
@roles_required(*ROLES)
def manage(resource):
    definition = RESOURCES.get(resource)
    if definition is None:
        abort(404)
    user = _current_user()
    role = user["role"]
    allowed = {
        "super_admin": set(RESOURCES),
        "admin": set(RESOURCES),
        "manager": set(RESOURCES),
        "teacher": {"students", "courses", "grades", "attendance"},
    }.get(role, set())
    if resource not in allowed:
        abort(403)
    if resource == "attendance":
        _require_module("attendance")
    elif resource == "teachers":
        _require_module("teachers")
    if resource == "attendance" and (
        request.method == "GET"
        or "class_name" in request.values
        or "course_id" in request.values
        or any(key.startswith("status_") for key in request.form)
    ):
        return attendance_roster()
    db = get_db()
    editable = (
        role == "super_admin"
        if resource == "courses"
        else role in {"super_admin", "manager", "admin"}
        or role == "teacher" and resource in {"grades", "attendance"}
    )
    if request.method == "POST" and not editable:
        abort(403)
    editing_id = request.args.get("edit", type=int)
    editing = None
    if editing_id is not None:
        if not editable:
            abort(403)
        editing_query = f"SELECT * FROM {definition['table']} WHERE id = ?"
        params = [editing_id]
        if role == "teacher" and resource in {"grades", "attendance"}:
            editing_query += (
                " AND EXISTS (SELECT 1 FROM enrollments e JOIN courses c "
                "ON c.id=e.course_id WHERE e.id=enrollment_id AND c.teacher_id=?)"
            )
            params.append(user["teacher_id"])
        editing = db.execute(editing_query, params).fetchone()
        if editing is None:
            abort(404)

    if request.method == "POST":
        values, error = _read_form(definition, role, user)
        if error:
            flash(error, "error")
        else:
            fields = [field[0] for field in definition["fields"]]
            picture = request.files.get("profile_picture")
            if picture and picture.filename:
                try:
                    values["profile_picture"] = _store_uploaded_image(picture, profile=True)
                except ValueError as error:
                    flash(str(error), "error")
                    values = None
            elif editing is not None and "profile_picture" in editing.keys():
                values["profile_picture"] = editing["profile_picture"]
            else:
                values["profile_picture"] = ""
            if values is None:
                query = definition["select"]
                params = ()
                if role == "teacher":
                    query, params = _teacher_course_filter(user, query)
                records = db.execute(query, params).fetchall()
                form_fields = _form_fields(definition, dict(editing or {}), role, user)
                return render_template(
                    "manage.html", resource=resource, definition=definition,
                    records=records, form_fields=form_fields, editing=editing,
                    editable=editable,
                )
            try:
                if editing_id is None:
                    placeholders = ", ".join("?" for _ in fields)
                    columns = ", ".join(fields)
                    db.execute(
                        f"INSERT INTO {definition['table']} ({columns}) VALUES ({placeholders})",
                        [values[field] for field in fields],
                    )
                    flash(f"{definition['singular'].capitalize()} added.", "success")
                else:
                    assignments = ", ".join(f"{field} = ?" for field in fields)
                    db.execute(
                        f"UPDATE {definition['table']} SET {assignments} WHERE id = ?",
                        [values[field] for field in fields] + [editing_id],
                    )
                    flash(f"{definition['singular'].capitalize()} updated.", "success")
                db.commit()
                if resource == "attendance":
                    enrollment = db.execute(
                        "SELECT course_id FROM enrollments WHERE id=?",
                        (values["enrollment_id"],),
                    ).fetchone()
                    if enrollment is not None:
                        return redirect(
                            url_for(
                                "views.attendance_roster",
                                course_id=enrollment["course_id"],
                                attendance_date=values["attendance_date"],
                            )
                        )
                return redirect(url_for("views.manage", resource=resource))
            except sqlite3.IntegrityError as error:
                db.rollback()
                flash(_integrity_message(error), "error")

    query = definition["select"]
    params = ()
    if role == "teacher":
        if not user["teacher_id"]:
            abort(403)
        if resource == "students":
            query = (
                "SELECT DISTINCT s.id,s.name,s.grade_level,"
                "CASE WHEN s.gender IN ('Male','Female') THEN s.gender ELSE '' END AS gender "
                "FROM students s JOIN enrollments e ON e.student_id=s.id "
                "JOIN courses c ON c.id=e.course_id WHERE c.teacher_id=? ORDER BY s.name"
            )
            params = (user["teacher_id"],)
        else:
            query, params = _teacher_course_filter(user, query)
    records = db.execute(query, params).fetchall()
    form_values = dict(editing) if editing is not None else {}
    form_fields = _form_fields(definition, form_values, role, user)
    return render_template(
        "manage.html", resource=resource, definition=definition, records=records,
        form_fields=form_fields, editing=editing, editable=editable,
        teacher_student_view=role == "teacher" and resource == "students",
    )


@bp.post("/manage/<resource>/<int:record_id>/delete")
@roles_required(*ACADEMIC_WRITE_ROLES)
def delete_record(resource, record_id):
    definition = RESOURCES.get(resource)
    if definition is None:
        abort(404)
    db = get_db()
    try:
        user = _current_user()
        if resource == "courses" and user["role"] != "super_admin":
            abort(403)
        if user["role"] == "admin" and resource not in {"grades", "attendance"}:
            abort(403)
        if resource == "courses":
            dependencies = db.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM enrollments WHERE course_id=?) + "
                "(SELECT COUNT(*) FROM homework WHERE course_id=?) + "
                "(SELECT COUNT(*) FROM timetable WHERE course_id=?) AS total",
                (record_id, record_id, record_id),
            ).fetchone()["total"]
            if dependencies:
                flash(
                    "This class has dependent enrollments, homework, or timetable entries and cannot be deleted.",
                    "error",
                )
                return redirect(url_for("views.manage_classes"))
        query = f"DELETE FROM {definition['table']} WHERE id = ?"
        params = [record_id]
        if user["role"] == "teacher":
            if resource not in {"grades", "attendance"} or not user["teacher_id"]:
                abort(403)
            query += (
                " AND EXISTS (SELECT 1 FROM enrollments e JOIN courses c "
                "ON c.id=e.course_id WHERE e.id=enrollment_id AND c.teacher_id=?)"
            )
            params.append(user["teacher_id"])
        cursor = db.execute(query, params)
        if cursor.rowcount == 0:
            abort(404)
        db.commit()
        flash(f"{definition['singular'].capitalize()} deleted.", "success")
    except sqlite3.IntegrityError as error:
        db.rollback()
        flash(_integrity_message(error), "error")
    return redirect(url_for("views.manage", resource=resource))


@bp.route("/homework", methods=("GET", "POST"))
@roles_required(*STAFF_ROLES, "student", "parent")
def homework():
    db = get_db()
    user = _current_user()
    if user["role"] in ("student", "parent"):
        if not user["student_id"]:
            abort(403)
        rows = db.execute(
            "SELECT h.title,h.description,h.due_date,c.name AS course_name FROM homework h "
            "JOIN courses c ON c.id=h.course_id JOIN enrollments e ON e.course_id=c.id "
            "WHERE e.student_id=? ORDER BY h.due_date",
            (user["student_id"],),
        ).fetchall()
        return render_template("homework.html", records=rows, editable=False)
    teacher = user["role"] == "teacher"
    editable = user["role"] in STAFF_ROLES
    editing_id = request.args.get("edit", type=int)
    editing = None
    if editing_id is not None:
        if not editable:
            abort(403)
        query = "SELECT h.* FROM homework h JOIN courses c ON c.id=h.course_id WHERE h.id=?"
        params = [editing_id]
        if teacher:
            query += " AND c.teacher_id=?"
            params.append(user["teacher_id"])
        editing = db.execute(query, params).fetchone()
        if editing is None:
            abort(404)
    if request.method == "POST":
        if not editable:
            abort(403)
        course_id = _optional_int(request.form.get("course_id"))
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        due_date = request.form.get("due_date", "").strip()
        course = db.execute(
            "SELECT id FROM courses WHERE id=?"
            + (" AND teacher_id=?" if teacher else ""),
            (course_id, user["teacher_id"]) if teacher else (course_id,),
        ).fetchone() if course_id else None
        if not course or not title or not _valid_date(due_date):
            flash("Choose an assigned class, enter a title and provide a valid due date.", "error")
        else:
            if editing_id:
                db.execute(
                    "UPDATE homework SET course_id=?,title=?,description=?,due_date=? WHERE id=?",
                    (course_id, title, description, due_date, editing_id),
                )
            else:
                db.execute(
                    "INSERT INTO homework (course_id,title,description,due_date,created_by) VALUES (?,?,?,?,?)",
                    (course_id, title, description, due_date, user["id"]),
                )
            db.commit()
            flash("Homework updated." if editing_id else "Homework assigned.", "success")
            return redirect(url_for("views.homework"))
    query = (
        "SELECT h.id,h.title,h.description,h.due_date,c.name AS course_name FROM homework h "
        "JOIN courses c ON c.id=h.course_id"
    )
    courses_query = "SELECT id,name FROM courses ORDER BY name"
    if teacher:
        query += " WHERE c.teacher_id=?"
        params = (user["teacher_id"],)
        courses = db.execute(courses_query.replace(" ORDER BY", " WHERE teacher_id=? ORDER BY"), params).fetchall()
    else:
        params = ()
        courses = db.execute(courses_query).fetchall()
    records = db.execute(query + " ORDER BY h.due_date", params).fetchall()
    return render_template(
        "homework.html", records=records, editable=editable, courses=courses, editing=editing
    )


@bp.post("/homework/<int:homework_id>/delete")
@roles_required(*STAFF_ROLES)
def delete_homework(homework_id):
    user = _current_user()
    db = get_db()
    query = "DELETE FROM homework WHERE id=?"
    params = [homework_id]
    if user["role"] == "teacher":
        query += " AND course_id IN (SELECT id FROM courses WHERE teacher_id=?)"
        params.append(user["teacher_id"])
    cursor = db.execute(query, params)
    if not cursor.rowcount:
        abort(404)
    db.commit()
    flash("Homework deleted.", "success")
    return redirect(url_for("views.homework"))


@bp.route("/portal/<module>", methods=("GET", "POST"))
@roles_required(*ROLES)
def portal(module):
    if module not in {"orders", "appointments", "bulk-sms", "tutoring", "bulletin", "parent-meetings"}:
        abort(404)
    user = _current_user()
    role = user["role"]
    if role not in STAFF_ROLES:
        abort(403)
    if role == "teacher" and module not in {"bulletin", "tutoring"}:
        abort(403)
    if role == "admin" and module not in {
        "appointments", "bulletin", "tutoring", "parent-meetings", "bulk-sms",
    }:
        abort(403)
    editing_id = request.args.get("edit", type=int)
    editing = None
    if editing_id:
        if module not in {"appointments", "bulletin"}:
            abort(400)
        table = "announcements" if module == "bulletin" else "appointments"
        editing = get_db().execute(
            f"SELECT * FROM {table} WHERE id=?", (editing_id,)
        ).fetchone()
        if editing is None:
            abort(404)
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        starts_at = request.form.get("starts_at", "").strip()
        record_id = _optional_int(request.form.get("record_id"))
        if record_id and (module not in {"appointments", "bulletin"} or not editing or editing["id"] != record_id):
            abort(400)
        if not title:
            flash("A title is required.", "error")
        elif module in {
            "orders", "bulk-sms", "tutoring", "parent-meetings",
        } and (
            role in {"super_admin", "manager"}
            or module == "bulk-sms" and role == "admin"
            or module == "tutoring" and role in {"admin", "teacher"}
        ) and len(description) <= 4000:
            get_db().execute(
                "INSERT INTO service_requests (module,title,details,created_by) "
                "VALUES (?,?,?,?)",
                (module, title, description, user["id"]),
            )
            get_db().commit()
            if module == "bulk-sms":
                flash(
                    "Request recorded. No SMS was sent because no messaging provider is configured.",
                    "success",
                )
            else:
                flash("Request recorded. No external service was contacted.", "success")
            return redirect(url_for("views.portal", module=module))
        elif module == "bulletin" and len(description) <= 4000:
            if record_id:
                get_db().execute(
                    "UPDATE announcements SET title=?,body=? WHERE id=?",
                    (title, description, record_id),
                )
            else:
                get_db().execute(
                    "INSERT INTO announcements (title, body) VALUES (?, ?)",
                    (title, description),
                )
            get_db().commit()
            flash("Announcement updated." if record_id else "Announcement published.", "success")
            return redirect(url_for("views.portal", module=module))
        elif module == "appointments" and _valid_datetime(starts_at):
            if record_id:
                get_db().execute(
                    "UPDATE appointments SET title=?,starts_at=?,notes=? WHERE id=?",
                    (title, starts_at, description, record_id),
                )
            else:
                get_db().execute(
                    "INSERT INTO appointments (title, starts_at, notes) VALUES (?, ?, ?)",
                    (title, starts_at, description),
                )
            get_db().commit()
            flash("Appointment updated." if record_id else "Appointment added to the calendar.", "success")
            return redirect(url_for("views.portal", module=module))
        else:
            flash("Enter a title, valid details and a valid appointment date and time.", "error")
    if module == "appointments":
        records = get_db().execute(
            "SELECT id,title, starts_at, notes FROM appointments ORDER BY starts_at LIMIT 50"
        ).fetchall()
    elif module == "bulletin":
        records = get_db().execute(
            "SELECT id,title, body AS notes, published_at AS starts_at FROM announcements ORDER BY published_at DESC LIMIT 50"
        ).fetchall()
    else:
        records = get_db().execute(
            "SELECT id,title,details AS notes,status,created_at AS starts_at "
            "FROM service_requests WHERE module=? ORDER BY created_at DESC LIMIT 50",
            (module,),
        ).fetchall()
    can_create = (
        module == "bulletin" and role in STAFF_ROLES
        or module == "appointments" and role in {"super_admin", "manager", "admin"}
        or module == "tutoring" and role in STAFF_ROLES
        or module == "bulk-sms" and role in {"super_admin", "manager", "admin"}
        or module in {"orders", "bulk-sms", "parent-meetings"}
        and role in {"super_admin", "manager"}
    )
    return render_template(
        "portal_module.html", module=module, records=records, editing=editing,
        can_create=can_create,
    )


@bp.get("/announcements")
@roles_required(*STAFF_ROLES)
def announcements():
    return redirect(url_for("views.portal", module="bulletin"))


@bp.post("/calendar/<kind>/<int:record_id>/delete")
@roles_required(*STAFF_ROLES)
def delete_calendar_record(kind, record_id):
    user = _current_user()
    table = {"appointments": "appointments", "bulletin": "announcements"}.get(kind)
    if table is None:
        abort(404)
    if kind == "appointments" and user["role"] == "teacher":
        abort(403)
    cursor = get_db().execute(f"DELETE FROM {table} WHERE id=?", (record_id,))
    if not cursor.rowcount:
        abort(404)
    get_db().commit()
    flash("Calendar item deleted.", "success")
    return redirect(url_for("views.portal", module=kind))


@bp.route("/timetable", methods=("GET", "POST"))
@roles_required(*STAFF_ROLES, "student", "parent")
def timetable():
    db = get_db()
    user = _current_user()
    if user["role"] in ("student", "parent"):
        return redirect(url_for("views.dashboard"))
    teacher = user["role"] == "teacher"
    if request.method == "POST":
        if teacher or user["role"] not in {"super_admin", "manager", "admin"}:
            abort(403)
        course_id = _optional_int(request.form.get("course_id"))
        weekday = _optional_int(request.form.get("weekday"))
        starts_at = request.form.get("starts_at", "").strip()
        ends_at = request.form.get("ends_at", "").strip()
        room = request.form.get("room", "").strip()
        if (
            not course_id
            or weekday is None
            or weekday not in range(7)
            or not _valid_time(starts_at)
            or not _valid_time(ends_at)
            or starts_at >= ends_at
        ):
            flash("Choose a class, weekday and valid start and end times.", "error")
        else:
            course = db.execute(
                "SELECT id FROM courses WHERE id=?", (course_id,)
            ).fetchone()
            if not course:
                abort(400)
            db.execute(
                "INSERT INTO timetable (course_id,weekday,starts_at,ends_at,room) VALUES (?,?,?,?,?)",
                (course_id, weekday, starts_at, ends_at, room),
            )
            db.commit()
            flash("Timetable entry added.", "success")
            return redirect(url_for("views.timetable"))
    query = (
        "SELECT t.id,c.name AS course_name,t.weekday,t.starts_at,t.ends_at,t.room "
        "FROM timetable t JOIN courses c ON c.id=t.course_id"
    )
    params = ()
    if teacher:
        query += " WHERE c.teacher_id=?"
        params = (user["teacher_id"],)
    records = db.execute(query + " ORDER BY t.weekday,t.starts_at", params).fetchall()
    courses_query = "SELECT id,name FROM courses ORDER BY name"
    courses = (
        db.execute(
            courses_query.replace(" ORDER BY", " WHERE teacher_id=? ORDER BY"),
            (user["teacher_id"],),
        ).fetchall()
        if teacher else db.execute(courses_query).fetchall()
    )
    return render_template(
        "timetable.html", records=records, courses=courses,
        editable=user["role"] in {"super_admin", "manager", "admin"},
    )


@bp.post("/timetable/<int:entry_id>/delete")
@roles_required("super_admin", "manager", "admin")
def delete_timetable_entry(entry_id):
    db = get_db()
    cursor = db.execute("DELETE FROM timetable WHERE id=?", (entry_id,))
    if not cursor.rowcount:
        abort(404)
    db.commit()
    flash("Timetable entry deleted.", "success")
    return redirect(url_for("views.timetable"))


def _optional_int(value):
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def _valid_date(value):
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _valid_time(value):
    try:
        datetime.strptime(value, "%H:%M")
        return True
    except ValueError:
        return False


def _valid_datetime(value):
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M")
        return True
    except ValueError:
        return False


def _read_form(definition, role, user):
    values = {}
    for name, label, kind, *rest in definition["fields"]:
        value = request.form.get(name, "").strip()
        required = not (rest and isinstance(rest[-1], bool) and not rest[-1])
        if kind == "file":
            values[name] = ""
            continue
        if not value and required:
            return None, f"{label} is required."
        if not value:
            values[name] = "Exam" if name == "exam_name" else ""
            continue
        if kind == "select":
            options = rest[0]
            if isinstance(options, str):
                query = options
                params = ()
                if role == "teacher" and name == "enrollment_id":
                    query = query.replace(
                        " ORDER BY",
                        " WHERE e.id IN (SELECT e2.id FROM enrollments e2 JOIN courses c2 "
                        "ON c2.id=e2.course_id WHERE c2.teacher_id=?) ORDER BY",
                    )
                    params = (user["teacher_id"],)
                valid_ids = {
                    str(row["id"]) for row in get_db().execute(query, params).fetchall()
                }
                if value not in valid_ids:
                    return None, f"Choose a valid option for {label.lower()}."
                value = int(value)
            elif value not in options:
                return None, f"Choose a valid option for {label.lower()}."
        elif name == "score":
            try:
                value = float(value)
            except ValueError:
                return None, "Score must be a number from 0 to 100."
            if not 0 <= value <= 100:
                return None, "Score must be between 0 and 100."
        elif kind == "number":
            try:
                numeric = float(value)
            except ValueError:
                return None, f"{label} must be a number."
            if name == "academic_year":
                if not numeric.is_integer() or numeric < 2000:
                    return None, f"{label} must be a whole year of 2000 or later."
                value = int(numeric)
        elif kind == "date" and not _valid_date(value):
            return None, f"{label} must be a valid date."
        elif name == "email":
            value = value.lower()
        values[name] = value
    return values, None


def _form_fields(definition, values, role, user):
    result = []
    for name, label, kind, *rest in definition["fields"]:
        choices = []
        if kind == "select":
            options = rest[0]
            if isinstance(options, str):
                query, params = options, ()
                if role == "teacher" and name == "enrollment_id":
                    query = query.replace(
                        " ORDER BY",
                        " WHERE e.id IN (SELECT e2.id FROM enrollments e2 JOIN courses c2 "
                        "ON c2.id=e2.course_id WHERE c2.teacher_id=?) ORDER BY",
                    )
                    params = (user["teacher_id"],)
                choices = [dict(row) for row in get_db().execute(query, params).fetchall()]
            else:
                choices = [{"id": item, "name": item} for item in options]
        result.append({
            "name": name, "label": label, "kind": kind,
            "value": values.get(name, ""), "choices": choices,
            "required": not (rest and isinstance(rest[-1], bool) and not rest[-1]),
        })
    return result


def _image_extension(content):
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if content.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "webp"
    return None


def _store_uploaded_image(upload, profile=False):
    content = upload.read(1024 * 1024 + 1)
    extension = _image_extension(content)
    if len(content) > 1024 * 1024 or extension is None:
        raise ValueError("Upload a valid PNG, JPEG, GIF or WebP image (maximum 1 MB).")
    prefix = "profile-" if profile else ""
    filename = f"{prefix}{secrets.token_hex(16)}.{extension}"
    upload_dir = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(upload_dir, exist_ok=True)
    with open(os.path.join(upload_dir, filename), "wb") as image_file:
        image_file.write(content)
    return filename


def _integrity_message(error):
    message = str(error)
    if "UNIQUE constraint failed" in message:
        return "That record conflicts with an existing unique value or record."
    if "FOREIGN KEY constraint failed" in message:
        return "This record is still in use and cannot be deleted."
    if "CHECK constraint failed" in message:
        return "One or more values are outside the allowed range."
    return "The record could not be saved because it conflicts with existing data."
