"""
app.py  —  Scholaris Academic Integrity Platform
Fully aligned with all HTML templates. Covers:
  - Auth: signup (first/last/email), login with lockout, logout, forgot-password,
    OTP verify, resend-OTP
  - Dashboard: role-aware context (admin stats, faculty stats, student search)
  - Courses: create (all fields + auto invite_code), join by invite code,
    enroll by ID, invite by email, announcements
  - Assignments: create / edit (all fields), toggle publish
  - Submissions: multi-file upload, deadline + attempt enforcement,
    full synchronous plagiarism pipeline, late flag
  - Reports: faculty view with full context, fromjson Jinja filter
  - Grading: faculty grade + feedback
  - Manual review: approve / reject with notes
  - Admin: user list placeholder
"""

import json
import uuid
import datetime
import os
import secrets
import random
import smtplib
import time
import tempfile
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

# Load .env file explicitly
load_dotenv()
import zipfile
import shutil
import csv
import io
import mimetypes
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from flask import (Flask, render_template, redirect, url_for, request,
                   flash, jsonify, abort, session, Response, send_file, current_app)
import threading
from flask_login import (LoginManager, login_user, logout_user,
                         login_required, current_user)
from flask_bcrypt import Bcrypt
from werkzeug.utils import secure_filename

from models import db, User, Course, Submission, Assignment, Announcement, BulkCheckRun, BulkCheckResult
import logic


# ─────────────────────────────────────────────────────────────────────────────
# APP SETUP
# ─────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///scholaris.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024  # 1 GB (bulk ZIP uploads)

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ── EMAIL CONFIG — set these in environment or replace with real values ───────
# For Gmail: enable "App Passwords" and use that as MAIL_PASSWORD.
# Leave MAIL_SERVER blank to disable email (OTP printed to console instead).

# ENABLE_EMAIL_VERIFICATION = False  # If True, new users must verify their email with a 6-digit OTP before logging in.

# app.config['MAIL_SERVER']   = os.environ.get('MAIL_SERVER',   'smtp.gmail.com')
# app.config['MAIL_PORT']     = int(os.environ.get('MAIL_PORT', 587))
# app.config['MAIL_USE_TLS']  = True
# app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', '')   # your@gmail.com
# app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', '')   # app password
# app.config['MAIL_FROM']     = os.environ.get('MAIL_FROM', 'noreply@scholaris.app')

# # OTP TTL in seconds (10 minutes)
# OTP_TTL = 600

# db.init_app(app)
# bcrypt      = Bcrypt(app)
# login_mgr   = LoginManager(app)
# login_mgr.login_view     = 'login'
# login_mgr.login_message  = 'Please log in to continue.'
# login_mgr.login_message_category = 'warning'

# # ─────────────────────────────────────────────────────────────────────────────
# # OTP HELPERS
# # ─────────────────────────────────────────────────────────────────────────────
# def _generate_otp() -> str:
#     """Return a zero-padded 6-digit OTP string."""
#     return f"{random.randint(0, 999999):06d}"


# def _send_otp_email(to_email: str, otp: str, username: str) -> bool:
#     """
#     Send OTP via SMTP. Returns True on success, False on failure.
#     Falls back to console print when MAIL_USERNAME is not configured.
#     """
#     if not app.config.get('MAIL_USERNAME'):
#         # Dev fallback — print to console so development works without email
#         print(f"[OTP] {username} <{to_email}>: {otp}")
#         return True
#     try:
#         msg = MIMEMultipart('alternative')
#         msg['Subject'] = 'Your Scholaris Verification Code'
#         msg['From']    = app.config['MAIL_FROM']
#         msg['To']      = to_email

#         html_body = f"""
#         <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px;">
#           <h2 style="color:#0f0f11;margin-bottom:4px;">Verify your email</h2>
#           <p style="color:#7a7a8a;">Hi {username}, use the code below to complete your registration.</p>
#           <div style="background:#f5f5f7;border-radius:12px;padding:24px;text-align:center;margin:24px 0;">
#             <span style="font-family:monospace;font-size:2.5rem;font-weight:700;letter-spacing:0.2em;color:#5b5ef4;">{otp}</span>
#           </div>
#           <p style="color:#7a7a8a;font-size:0.85rem;">This code expires in 10 minutes. If you did not sign up, ignore this email.</p>
#         </div>
#         """
#         msg.attach(MIMEText(html_body, 'html'))

#         with smtplib.SMTP(app.config['MAIL_SERVER'], app.config['MAIL_PORT']) as smtp:
#             smtp.ehlo()
#             if app.config['MAIL_USE_TLS']:
#                 smtp.starttls()
#             smtp.login(app.config['MAIL_USERNAME'], app.config['MAIL_PASSWORD'])
#             smtp.sendmail(app.config['MAIL_FROM'], [to_email], msg.as_string())
#         return True
#     except Exception as e:
#         print(f"[EMAIL ERROR] Could not send OTP to {to_email}: {e}")
#         return False


# ── Jinja filter used in reports.html: {{ sub.plagiarism_report|fromjson }} ──
@app.template_filter('fromjson')
def fromjson_filter(value):
    if not value:
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}

# import re

# @app.template_filter('regex_replace')
# def regex_replace(s, find, replace):
#     if s is None:
#         return ""
#     return re.sub(find, replace, s)

# @login_mgr.user_loader
# def load_user(user_id):
#     return db.session.get(User, int(user_id))

ENABLE_EMAIL_VERIFICATION = True   # OTP email verification active — set MAIL_USERNAME + MAIL_PASSWORD env vars

app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USE_SSL'] = False

# Credentials (DO NOT hardcode in production)
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')

# Sender address (defaults to authenticated Gmail)
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get(
    'MAIL_FROM',
    app.config['MAIL_USERNAME']
)

# OTP configuration
OTP_TTL = 300            # 5 minutes
OTP_RESEND_DELAY = 60    # prevent email spam
OTP_MAX_ATTEMPTS = 3     # brute-force protection


# ── EXTENSIONS ────────────────────────────────────────────────────────────────
db.init_app(app)

# --- GLOBAL SELF-HEALING DB SYNC (DIRECT-ACCESS) ---
with app.app_context():
    import psycopg2
    try:
        # Use a raw connection to bypass SQLAlchemy metadata caching issues
        raw_conn = psycopg2.connect(app.config['SQLALCHEMY_DATABASE_URI'])
        raw_conn.autocommit = True
        with raw_conn.cursor() as cur:
            for col, dtype in [('processed_count','INT'),('status','VARCHAR(20)'),('accepted','INT'),('rejected','INT'),('manual_review','INT'),('elapsed_sec','FLOAT')]:
                try:
                    cur.execute(f"ALTER TABLE bulk_check_run ADD COLUMN IF NOT EXISTS {col} {dtype}")
                except Exception: pass
        raw_conn.close()
        print("[DB] Global Sync (Direct) Complete.")
    except Exception as e:
        print(f"[DB] Global Sync (Direct) Error: {e}")

bcrypt = Bcrypt(app)

login_mgr = LoginManager(app)
login_mgr.login_view = 'login'
login_mgr.login_message = 'Please log in to continue.'
login_mgr.login_message_category = 'warning'

from flask_mail import Mail
mail = Mail(app)

# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# OTP HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _generate_otp() -> str:
    """Return a zero-padded 6-digit OTP string."""
    return f"{random.randint(0, 999999):06d}"


def _send_otp_email(to_email: str, otp: str, username: str) -> bool:
    """
    Send OTP via SMTP. Returns True on success, False on failure.
    Falls back to console print when MAIL_USERNAME is not configured.
    """

    mail_user   = app.config.get('MAIL_USERNAME')
    mail_pass   = app.config.get('MAIL_PASSWORD')
    mail_server = app.config.get('MAIL_SERVER')
    mail_port   = app.config.get('MAIL_PORT')
    mail_sender = app.config.get('MAIL_DEFAULT_SENDER', mail_user)

    # Development fallback (no email configured)
    if not mail_user or not mail_pass:
        print(f"[OTP] {username} <{to_email}>: {otp}")
        return True

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = 'Your Scholaris Verification Code'
        msg['From']    = mail_sender
        msg['To']      = to_email

        html_body = f"""
        <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px;">
          <h2 style="color:#0f0f11;margin-bottom:4px;">Verify your email</h2>
          <p style="color:#7a7a8a;">Hi {username}, use the code below to complete your registration.</p>

          <div style="background:#f5f5f7;border-radius:12px;padding:24px;text-align:center;margin:24px 0;">
            <span style="font-family:monospace;font-size:2.5rem;font-weight:700;letter-spacing:0.2em;color:#5b5ef4;">
              {otp}
            </span>
          </div>

          <p style="color:#7a7a8a;font-size:0.85rem;">
            This code expires in 5 minutes. If you did not sign up, ignore this email.
          </p>
        </div>
        """

        msg.attach(MIMEText(html_body, 'html'))

        with smtplib.SMTP(mail_server, mail_port) as smtp:
            smtp.ehlo()

            if app.config.get('MAIL_USE_TLS'):
                smtp.starttls()

            smtp.login(mail_user, mail_pass)

            smtp.sendmail(
                mail_sender,
                [to_email],
                msg.as_string()
            )

        return True

    except Exception as e:
        print(f"[EMAIL ERROR] Could not send OTP to {to_email}: {e}")
        return False


# ── Jinja filter used in reports.html: {{ sub.plagiarism_report|fromjson }} ──
@app.template_filter('fromjson')
def fromjson_filter(value):
    if not value:
        return {}   
    try:
        return json.loads(value)
    except Exception:
        return {}

import re

@app.template_filter('regex_replace')
def regex_replace(s, find, replace):
    if s is None:
        return ""
    return re.sub(find, replace, s)

@login_mgr.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _now():
    return datetime.datetime.now()


def _allowed_file(filename: str, allowed_types: list) -> bool:
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    return ext in [t.strip().lower() for t in allowed_types]


def _save_file(file, user_id: int, assignment_id: int) -> dict:
    """Save one uploaded file with UUID name. Returns metadata dict."""
    original = file.filename
    ext      = original.rsplit('.', 1)[-1].lower() if '.' in original else 'bin'
    uid_name = f"{uuid.uuid4().hex}.{ext}"
    folder   = os.path.join(app.config['UPLOAD_FOLDER'], str(user_id))
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, uid_name)
    file.save(dest)
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    return {
        'name':         uid_name,
        'originalName': original,
        'path':         dest,
        'size':         size,
        'mimetype':     file.content_type or 'application/octet-stream',
    }


def _faculty_dashboard_stats(faculty_id: int) -> dict:
    """Extra context vars needed by faculty dashboard."""
    course_ids = [c.id for c in Course.query.filter_by(faculty_id=faculty_id).all()]
    total_assignments = Assignment.query.filter(
        Assignment.course_id.in_(course_ids)).count() if course_ids else 0
    total_submissions = Submission.query.filter(
        Submission.course_id.in_(course_ids)).count() if course_ids else 0
    pending_reviews = Submission.query.filter(
        Submission.course_id.in_(course_ids),
        Submission.manual_review == True).count() if course_ids else 0
    return dict(
        total_assignments=total_assignments,
        total_submissions=total_submissions,
        pending_reviews=pending_reviews,
    )


def _admin_dashboard_stats() -> dict:
    """System-wide stats for admin dashboard."""
    return dict(
        total_users    = User.query.count(),
        total_courses  = Course.query.count(),
        total_subs     = Submission.query.count(),
        total_flagged  = Submission.query.filter(
            Submission.status.in_(['rejected', 'manual_review'])).count(),
        total_rejected = Submission.query.filter_by(status='rejected').count(),
        total_manual   = Submission.query.filter_by(manual_review=True).count(),
        flagged_subs   = Submission.query.filter(
            Submission.status.in_(['rejected', 'manual_review'])
        ).order_by(Submission.timestamp.desc()).limit(10).all(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# DEPENDENCY CHECK — run at startup, prints exactly what is missing
# ─────────────────────────────────────────────────────────────────────────────
def check_dependencies():
    issues = []
    # Core plagiarism deps
    for pkg, pip_name in [
        ('faiss',               'faiss-cpu'),
        ('sentence_transformers','sentence-transformers'),
        ('cv2',                  'opencv-python'),
        ('pytesseract',          'pytesseract'),
        ('PyPDF2',               'PyPDF2'),
        ('pdf2image',            'pdf2image'),
        ('docx',                 'python-docx'),
        ('nltk',                 'nltk'),
        ('rapidfuzz',            'rapidfuzz'),
    ]:
        try:
            __import__(pkg)
        except (ImportError, OSError, Exception) as e:
            # OSError catches DLL loading errors on Windows
            issues.append(f"  pip install {pip_name}")

    if issues:
        print("\n[SCHOLARIS] WARNING — plagiarism pipeline will fall back to manual_review:")
        for i in issues:
            print(i)
        print("  Run the above commands, then restart.\n")
    else:
        print("[SCHOLARIS] OK - All plagiarism dependencies found.")
    return len(issues) == 0


# ─────────────────────────────────────────────────────────────────────────────
# FAISS STARTUP SYNC
# ─────────────────────────────────────────────────────────────────────────────
def sync_vector_engine():
    with app.app_context():
        texts = [s.text_content for s in Submission.query.all() if s.text_content]
        if texts:
            logic.build_index(texts)
            print(f"[INFO] FAISS synced — {len(texts)} documents.")


# =============================================================================
# LANDING
# =============================================================================
@app.route('/')
def index():
    return render_template('index.html')


# =============================================================================
# AUTH — SIGNUP
# =============================================================================
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        first_name = request.form.get('first_name', '').strip()
        last_name  = request.form.get('last_name',  '').strip()
        username   = request.form.get('username',   '').strip()
        email      = request.form.get('email',      '').strip().lower()
        password   = request.form.get('password',   '')
        role       = request.form.get('role', 'student')

        # Role guard — admin cannot self-register
        if role not in ('student', 'faculty'):
            flash('Invalid role selected.', 'danger')
            return redirect(url_for('signup'))

        # Uniqueness checks
        if User.query.filter_by(username=username).first():
            flash('Username already taken.', 'danger')
            return redirect(url_for('signup'))
        if email and User.query.filter_by(email=email).first():
            flash('Email already registered.', 'danger')
            return redirect(url_for('signup'))

        # Password rules: min 8, upper, lower, digit
        if len(password) < 8:
            flash('Password must be at least 8 characters.', 'danger')
            return redirect(url_for('signup'))
        import re
        if not re.search(r'[A-Z]', password):
            flash('Password must contain at least one uppercase letter.', 'danger')
            return redirect(url_for('signup'))
        if not re.search(r'[a-z]', password):
            flash('Password must contain at least one lowercase letter.', 'danger')
            return redirect(url_for('signup'))
        if not re.search(r'[0-9]', password):
            flash('Password must contain at least one number.', 'danger')
            return redirect(url_for('signup'))

        hashed = bcrypt.generate_password_hash(password).decode('utf-8')
        user   = User(
            username=username, password=hashed, role=role,
            first_name=first_name, last_name=last_name,
            email=email if email else None,
            email_verified=not bool(email),  # skip verification if no email provided
        )
        db.session.add(user)
        db.session.commit()

        # if email:
        #     otp = _generate_otp()
        #     # Store OTP + expiry + user_id in session
        #     session['otp_code']    = otp
        #     session['otp_expires'] = (datetime.datetime.utcnow() +
        #                               datetime.timedelta(seconds=OTP_TTL)).isoformat()
        #     session['otp_user_id'] = user.id
        #     session['otp_email']   = email

        #     sent = _send_otp_email(email, otp, username)
        #     if sent:
        #         flash('Account created! Check your email for a 6-digit verification code.', 'success')
        #     else:
        #         flash('Account created but email delivery failed. Contact support.', 'warning')
        #     return redirect(url_for('verify_otp', email=email))
        # else:
        #     flash('Account created! Please log in.', 'success')
        #     return redirect(url_for('login'))

        if email and ENABLE_EMAIL_VERIFICATION:
            otp = _generate_otp()

            session['otp_code'] = otp
            session['otp_expires'] = (
                datetime.datetime.utcnow() +
                datetime.timedelta(seconds=OTP_TTL)
            ).isoformat()

            session['otp_user_id'] = user.id
            session['otp_email'] = email

            sent = _send_otp_email(email, otp, username)

            if sent:
                flash('Account created! Check your email for a verification code.', 'success')
            else:
                flash('Account created but email delivery failed.', 'warning')

            return redirect(url_for('verify_otp', email=email))

        else:
            # DIRECT ACTIVATION (TEST MODE)
            user.email_verified = True
            db.session.commit()

            flash('Account created successfully!', 'success')
            return redirect(url_for('login'))
    return render_template('signup.html')

    # return render_template('signup.html')


# =============================================================================
# AUTH — OTP VERIFY  (stub — wire Redis + email sender for production)
# =============================================================================
@app.route('/verify-otp', methods=['GET', 'POST'])
def verify_otp():
    email = request.args.get('email', '') or session.get('otp_email', '')

    if request.method == 'POST':
        entered = request.form.get('otp_code', '').strip()

        stored_otp     = session.get('otp_code')
        stored_expires = session.get('otp_expires')
        stored_uid     = session.get('otp_user_id')

        if not stored_otp or not stored_expires or not stored_uid:
            flash('Session expired. Please sign up again.', 'danger')
            return redirect(url_for('signup'))

        # Check expiry
        expires_at = datetime.datetime.fromisoformat(stored_expires)
        if datetime.datetime.utcnow() > expires_at:
            session.pop('otp_code', None)
            session.pop('otp_expires', None)
            flash('OTP expired. Please request a new one.', 'danger')
            return redirect(url_for('verify_otp', email=email))

        if entered != stored_otp:
            flash('Incorrect code. Please try again.', 'danger')
            return redirect(url_for('verify_otp', email=email))

        # OTP correct — mark user as verified
        user = db.session.get(User, stored_uid)
        if user:
            user.email_verified = True
            db.session.commit()

        # Clear OTP from session
        session.pop('otp_code', None)
        session.pop('otp_expires', None)
        session.pop('otp_user_id', None)
        session.pop('otp_email', None)

        flash('Email verified successfully! Please log in.', 'success')
        return redirect(url_for('login'))

    return render_template('otp_verify.html', email=email)


@app.route('/resend-otp')
def resend_otp():
    email    = session.get('otp_email', '')
    user_id  = session.get('otp_user_id')
    username = ''
    if user_id:
        user = db.session.get(User, user_id)
        if user:
            username = user.username
            email    = email or user.email or ''

    if not email:
        flash('No pending verification found. Please sign up again.', 'danger')
        return redirect(url_for('signup'))

    otp = _generate_otp()
    session['otp_code']    = otp
    session['otp_expires'] = (datetime.datetime.utcnow() +
                               datetime.timedelta(seconds=OTP_TTL)).isoformat()
    sent = _send_otp_email(email, otp, username)
    if sent:
        flash('A new verification code has been sent to your email.', 'info')
    else:
        flash('Could not send email. Check server mail config.', 'danger')
    return redirect(url_for('verify_otp', email=email))


# =============================================================================
# AUTH — LOGIN  (with lockout)
# =============================================================================
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES     = 15

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user     = User.query.filter_by(username=username).first()

        if user:
            # Lockout check
            if user.is_locked():
                mins = user.minutes_locked()
                flash(f'Account locked. Try again in {mins} minute(s).', 'danger')
                return redirect(url_for('login'))

            if bcrypt.check_password_hash(user.password, password):
                if not user.is_active:
                    flash('Your account has been deactivated. Contact an admin.', 'danger')
                    return redirect(url_for('login'))
                # Success — reset counter
                user.failed_attempts = 0
                user.locked_until    = None
                user.last_login      = datetime.datetime.utcnow()
                db.session.commit()
                if not user.email_verified:
                    flash('Please verify your email before logging in. Check your inbox for the OTP.', 'warning')
                    # Re-send OTP if they come back before verifying
                    if user.email:
                        otp = _generate_otp()
                        session['otp_code']    = otp
                        session['otp_expires'] = (datetime.datetime.utcnow() +
                                                   datetime.timedelta(seconds=OTP_TTL)).isoformat()
                        session['otp_user_id'] = user.id
                        session['otp_email']   = user.email
                        _send_otp_email(user.email, otp, user.username)
                    return redirect(url_for('verify_otp', email=user.email or ''))
                login_user(user)
                return redirect(url_for('dashboard'))
            else:
                user.failed_attempts = (user.failed_attempts or 0) + 1
                if user.failed_attempts >= MAX_FAILED_ATTEMPTS:
                    user.locked_until    = datetime.datetime.utcnow() + datetime.timedelta(minutes=LOCKOUT_MINUTES)
                    user.failed_attempts = 0
                    db.session.commit()
                    flash(f'Too many failed attempts. Account locked for {LOCKOUT_MINUTES} minutes.', 'danger')
                else:
                    remaining = MAX_FAILED_ATTEMPTS - user.failed_attempts
                    db.session.commit()
                    flash(f'Invalid password. {remaining} attempt(s) remaining.', 'danger')
        else:
            flash('Invalid username or password.', 'danger')

    return render_template('login.html')


# =============================================================================
# AUTH — LOGOUT / FORGOT PASSWORD
# =============================================================================
@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        # Always return same message to prevent email enumeration
        email = request.form.get('email', '').strip().lower()
        user  = User.query.filter_by(email=email).first()
        if user:
            token = secrets.token_hex(32)
            user.reset_token         = token
            user.reset_token_expires = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
            db.session.commit()
            # TODO: send reset email with token
        flash('If that email is registered, a reset link has been sent.', 'info')
        return redirect(url_for('login'))
    return render_template('login.html')   # reuse login template or make forgot_password.html


# =============================================================================
# DASHBOARD  (role-aware)
# =============================================================================
@app.route('/dashboard')
@login_required
def dashboard():
    now = _now()

    if current_user.role == 'admin':
        courses = Course.query.order_by(Course.created_at.desc()).all()
        return render_template('dashboard.html',
                               courses=courses, now=now,
                               **_admin_dashboard_stats())

    if current_user.role == 'faculty':
        courses = Course.query.filter_by(faculty_id=current_user.id).all()
        return render_template('dashboard.html',
                               courses=courses, now=now,
                               **_faculty_dashboard_stats(current_user.id))

    # Student
    enrolled      = current_user.enrolled_courses
    all_available = Course.query.filter_by(is_active=True).all()
    return render_template('dashboard.html',
                           courses=enrolled,
                           all_available_courses=all_available,
                           now=now)


# =============================================================================
# ADMIN — USER MANAGEMENT
# =============================================================================
@app.route('/admin/users')
@login_required
def admin_users():
    if current_user.role != 'admin':
        abort(403)
    
    # Get stats for the admin page
    stats = {
        'total': User.query.count(),
        'students': User.query.filter_by(role='student').count(),
        'faculty': User.query.filter_by(role='faculty').count(),
        'admins': User.query.filter_by(role='admin').count(),
    }
    
    users = User.query.order_by(User.id.desc()).all()
    return render_template('admin_users.html', users=users, stats=stats)


@app.route('/admin/user/<int:user_id>/toggle', methods=['POST'])
@login_required
def admin_toggle_user(user_id):
    if current_user.role != 'admin':
        abort(403)
    user = db.get_or_404(User, user_id)
    
    # Prevent self-deactivation
    if user.id == current_user.id:
        flash("You cannot deactivate your own account.", "danger")
        return redirect(url_for('admin_users'))
        
    user.is_active = not user.is_active
    db.session.commit()
    status = "activated" if user.is_active else "deactivated"
    flash(f"User '{user.username}' has been {status}.", "info")
    return redirect(url_for('admin_users'))


@app.route('/admin/user/<int:user_id>/delete', methods=['POST'])
@login_required
def admin_delete_user(user_id):
    if current_user.role != 'admin':
        abort(403)
    user = db.get_or_404(User, user_id)
    
    # Safety Check
    if user.id == current_user.id:
        flash("You cannot delete your own account.", "danger")
        return redirect(url_for('admin_users'))

    username = user.username
    db.session.delete(user)
    db.session.commit()
    flash(f"User '{username}' was permanently deleted.", "success")
    return redirect(url_for('admin_users'))


# =============================================================================
# COURSES — CREATE
# =============================================================================
@app.route('/create_course', methods=['GET', 'POST'])
@login_required
def create_course():
    if current_user.role != 'faculty':
        flash('Only faculty can create courses.', 'danger')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        name        = request.form.get('name',        '').strip()
        code        = request.form.get('code',        '').strip().upper()
        description = request.form.get('description', '').strip()
        semester    = request.form.get('semester',    '').strip()
        year_str    = request.form.get('year',        str(_now().year))
        cover_color = request.form.get('cover_color', '#5b5ef4').strip()
        invite_emails = request.form.get('invite_emails', '').strip()

        try:
            year = int(year_str)
        except ValueError:
            year = _now().year

        if Course.query.filter_by(code=code).first():
            flash(f'Course code "{code}" already exists.', 'danger')
            return render_template('create_course.html', now=_now())

        course = Course(
            name=name, code=code, faculty_id=current_user.id,
            description=description, semester=semester, year=year,
            cover_color=cover_color,
        )
        course.generate_invite_code()
        db.session.add(course)
        db.session.commit()

        # Invite by email (stub — wire real email sender)
        if invite_emails:
            for addr in [e.strip() for e in invite_emails.split(',') if e.strip()]:
                pass  # TODO: send email with course.invite_code

        flash(f'Course "{name}" created! Invite code: {course.invite_code}', 'success')
        return redirect(url_for('dashboard'))

    return render_template('create_course.html', now=_now())


# =============================================================================
# COURSES — JOIN BY INVITE CODE  (student)
# =============================================================================
@app.route('/join/<string:code>')
@login_required
def join_by_invite(code):
    course = Course.query.filter_by(invite_code=code.upper().strip()).first()
    if not course:
        flash('Invalid invite code.', 'danger')
        return redirect(url_for('dashboard'))
    if course in current_user.enrolled_courses:
        flash(f'You are already enrolled in {course.name}.', 'info')
        return redirect(url_for('course_page', course_id=course.id))
    current_user.enrolled_courses.append(course)
    db.session.commit()
    flash(f'Joined "{course.name}" successfully!', 'success')
    return redirect(url_for('course_page', course_id=course.id))


# =============================================================================
# COURSES — ENROLL BY ID  (legacy / search widget)
# =============================================================================
@app.route('/enroll/<int:course_id>')
@login_required
def enroll(course_id):
    course = db.get_or_404(Course, course_id)
    if course in current_user.enrolled_courses:
        flash('Already enrolled.', 'info')
    else:
        current_user.enrolled_courses.append(course)
        db.session.commit()
        flash(f'Enrolled in {course.name}!', 'success')
    return redirect(url_for('dashboard'))


# =============================================================================
# COURSES — INVITE BY EMAIL  (faculty, from course_page sidebar)
# =============================================================================
@app.route('/course/<int:course_id>/invite', methods=['POST'])
@login_required
def invite_by_email(course_id):
    if current_user.role != 'faculty':
        abort(403)
    course = db.get_or_404(Course, course_id)
    emails = request.form.get('invite_emails', '')
    count  = 0
    for addr in [e.strip() for e in emails.split(',') if e.strip()]:
        # TODO: send actual email with course.invite_code
        count += 1
    flash(f'Invite sent to {count} address(es).', 'success')
    return redirect(url_for('course_page', course_id=course_id))


# =============================================================================
# COURSE PAGE
# =============================================================================
@app.route('/course/<int:course_id>')
@login_required
def course_page(course_id):
    course      = db.get_or_404(Course, course_id)
    assignments = (Assignment.query
                   .filter_by(course_id=course_id)
                   .order_by(Assignment.deadline.asc())
                   .all())
    announcements = (Announcement.query
                     .filter_by(course_id=course_id)
                     .order_by(Announcement.is_pinned.desc(),
                               Announcement.created_at.desc())
                     .all())
    return render_template('course_page.html',
                           course=course,
                           assignments=assignments,
                           announcements=announcements,
                           now=_now(),
                           Submission=Submission)


# =============================================================================
# ASSIGNMENTS — CREATE
# =============================================================================
@app.route('/course/<int:course_id>/create_assignment', methods=['GET', 'POST'])
@login_required
def create_assignment(course_id):
    if current_user.role != 'faculty':
        abort(403)
    course = db.get_or_404(Course, course_id)

    if request.method == 'POST':
        title        = request.form.get('title',        '').strip()
        description  = request.form.get('description',  '').strip()
        instructions = request.form.get('instructions', '').strip()
        deadline_str = request.form.get('deadline',     '')

        try:
            deadline = datetime.datetime.strptime(deadline_str, '%Y-%m-%dT%H:%M')
        except ValueError:
            flash('Invalid deadline format.', 'danger')
            return render_template('create_assignment.html', course=course)

        max_marks      = int(request.form.get('max_marks',      100))
        attempt_limit  = max(1, min(20, int(request.form.get('attempt_limit', 3))))
        allow_late     = request.form.get('allow_late_submission') == 'on'

        # File type toggle checkboxes send individual values;
        # hidden input "allowed_file_types" is assembled by JS
        allowed_types  = request.form.get('allowed_file_types', 'pdf,docx,jpg,png')
        max_file_size  = int(request.form.get('max_file_size', 10))

        enable_plag    = request.form.get('enable_plagiarism_check') == 'on'
        check_hw       = request.form.get('check_handwritten') == 'on'
        threshold      = max(0, min(100, int(request.form.get('similarity_threshold', 40))))

        # Resource files
        q_files   = request.files.getlist('question_files')
        filenames = []
        for f in q_files:
            if f and f.filename:
                ts   = int(_now().timestamp())
                safe = secure_filename(f.filename)
                dest = os.path.join(app.config['UPLOAD_FOLDER'],
                                    f'Q_{course_id}_{ts}_{safe}')
                f.save(dest)
                filenames.append(f'Q_{course_id}_{ts}_{safe}')

        assignment = Assignment(
            title=title, description=description, instructions=instructions,
            deadline=deadline, course_id=course.id,
            question_file=','.join(filenames) if filenames else None,
            max_marks=max_marks, attempt_limit=attempt_limit,
            allow_late_submission=allow_late,
            allowed_file_types=allowed_types, max_file_size=max_file_size,
            enable_plagiarism_check=enable_plag,
            check_handwritten=check_hw,
            similarity_threshold=threshold,
            is_published=True,
        )
        db.session.add(assignment)
        db.session.commit()
        flash(f'Assignment "{title}" published!', 'success')
        return redirect(url_for('course_page', course_id=course_id))

    return render_template('create_assignment.html', course=course)


# =============================================================================
# ASSIGNMENTS — EDIT
# =============================================================================
@app.route('/assignment/<int:assignment_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_assignment(assignment_id):
    if current_user.role != 'faculty':
        abort(403)
    assignment = db.get_or_404(Assignment, assignment_id)

    if request.method == 'POST':
        assignment.title        = request.form.get('title',        '').strip()
        assignment.description  = request.form.get('description',  '').strip()
        assignment.instructions = request.form.get('instructions', '').strip()
        assignment.max_marks    = int(request.form.get('max_marks',    100))
        assignment.attempt_limit = max(1, min(20, int(request.form.get('attempt_limit', 3))))
        assignment.allow_late_submission = request.form.get('allow_late_submission') == 'on'
        assignment.is_published  = request.form.get('is_published') == 'on'
        assignment.enable_plagiarism_check = request.form.get('enable_plagiarism_check') == 'on'
        assignment.check_handwritten       = request.form.get('check_handwritten') == 'on'
        assignment.similarity_threshold    = max(0, min(100, int(
            request.form.get('similarity_threshold', 40))))

        deadline_str = request.form.get('deadline', '')
        try:
            assignment.deadline = datetime.datetime.strptime(deadline_str, '%Y-%m-%dT%H:%M')
        except ValueError:
            flash('Invalid deadline format.', 'danger')
            return render_template('edit_assignment.html', assignment=assignment)

        db.session.commit()
        flash('Assignment updated.', 'success')
        return redirect(url_for('view_reports', course_id=assignment.course_id))

    return render_template('edit_assignment.html', assignment=assignment)


# =============================================================================
# ASSIGNMENTS — TOGGLE PUBLISH
# =============================================================================
@app.route('/toggle_publish/<int:assignment_id>')
@login_required
def toggle_publish(assignment_id):
    if current_user.role != 'faculty':
        abort(403)
    assign = db.get_or_404(Assignment, assignment_id)
    assign.is_published = not assign.is_published
    db.session.commit()
    flash(f"Assignment {'published' if assign.is_published else 'hidden'}.", 'info')
    return redirect(url_for('course_page', course_id=assign.course_id))


# =============================================================================
# ASSIGNMENTS — DELETE
# =============================================================================
@app.route('/assignment/<int:assignment_id>/delete', methods=['POST'])
@login_required
def delete_assignment(assignment_id):
    if current_user.role != 'faculty':
        abort(403)
    assign = db.get_or_404(Assignment, assignment_id)
    course_id = assign.course_id
    # Verify ownership
    course = db.get_or_404(Course, course_id)
    if course.faculty_id != current_user.id:
        abort(403)
    db.session.delete(assign)
    db.session.commit()
    flash(f'Assignment "{assign.title}" deleted.', 'success')
    return redirect(url_for('course_page', course_id=course_id))


# =============================================================================
# COURSES — DELETE
# =============================================================================
@app.route('/course/<int:course_id>/delete', methods=['POST'])
@login_required
def delete_course(course_id):
    if current_user.role not in ('faculty', 'admin'):
        abort(403)
    course = db.get_or_404(Course, course_id)
    if current_user.role == 'faculty' and course.faculty_id != current_user.id:
        abort(403)
    db.session.delete(course)
    db.session.commit()
    flash(f'Course "{course.name}" deleted.', 'success')
    return redirect(url_for('dashboard'))


# =============================================================================
# SUBMISSIONS — DELETE (faculty / admin)
# =============================================================================
@app.route('/submission/<int:submission_id>/delete', methods=['POST'])
@login_required
def delete_submission(submission_id):
    if current_user.role not in ('faculty', 'admin'):
        abort(403)
    sub = db.get_or_404(Submission, submission_id)
    course_id = sub.course_id
    db.session.delete(sub)
    db.session.commit()
    flash('Submission deleted.', 'success')
    return redirect(url_for('view_reports', course_id=course_id))


# =============================================================================
# SUBMISSIONS — UPLOAD + PLAGIARISM PIPELINE
# =============================================================================
@app.route('/submit/<int:assignment_id>', methods=['GET', 'POST'])
@login_required
def submit(assignment_id):
    assignment = db.get_or_404(Assignment, assignment_id)

    existing       = Submission.query.filter_by(
        user_id=current_user.id, assignment_id=assignment_id
    ).all()
    attempts_used    = len(existing)
    attempts_allowed = assignment.attempt_limit
    remaining        = max(0, attempts_allowed - attempts_used)

    # ── Attempt limit ────────────────────────────────────────────────────────
    if attempts_used >= attempts_allowed:
        if request.method == 'POST':
            return jsonify({
                'error': f'You have used all {attempts_allowed} attempt(s).',
                'attemptsUsed': attempts_used,
                'attemptsAllowed': attempts_allowed,
                'remainingAttempts': 0,
            }), 403
        flash(f'No attempts remaining ({attempts_used}/{attempts_allowed}).', 'danger')
        return redirect(url_for('course_page', course_id=assignment.course_id))

    # ── Deadline ─────────────────────────────────────────────────────────────
    now             = _now()
    is_past         = now > assignment.deadline
    is_late         = False

    if is_past:
        if not assignment.allow_late_submission:
            if request.method == 'POST':
                return jsonify({'error': 'Deadline has passed.'}), 403
            flash('Submission deadline has passed.', 'danger')
            return redirect(url_for('course_page', course_id=assignment.course_id))
        is_late = True

    # ── GET — show upload form ────────────────────────────────────────────────
    if request.method == 'GET':
        allowed_types = [t.strip() for t in assignment.allowed_file_types.split(',')]
        return render_template('upload.html',
                               assignment=assignment,
                               attempts_used=attempts_used,
                               attempts_allowed=attempts_allowed,
                               remaining_attempts=remaining,
                               is_late=is_late,
                               allowed_types=allowed_types,
                               max_file_size=assignment.max_file_size)

    # ── POST — process submission ─────────────────────────────────────────────
    files       = request.files.getlist('files')
    valid_files = [f for f in files if f.filename]

    if not valid_files:
        flash('Please select at least one file.', 'warning')
        return redirect(request.url)
    if len(valid_files) > 5:
        flash('Maximum 5 files per submission.', 'warning')
        return redirect(request.url)

    allowed_types  = [t.strip().lower() for t in assignment.allowed_file_types.split(',')]
    max_size_bytes = assignment.max_file_size * 1024 * 1024

    for f in valid_files:
        if not _allowed_file(f.filename, allowed_types):
            flash(f'"{f.filename}" — file type not allowed. Allowed: {", ".join(allowed_types)}', 'danger')
            return redirect(request.url)
        f.seek(0, 2)
        if f.tell() > max_size_bytes:
            flash(f'"{f.filename}" exceeds {assignment.max_file_size} MB limit.', 'danger')
            return redirect(request.url)
        f.seek(0)

    # Save files
    saved_meta  = []
    saved_paths = []
    for f in valid_files:
        f.seek(0)
        meta = _save_file(f, current_user.id, assignment_id)
        saved_meta.append(meta)
        saved_paths.append(meta['path'])

    primary_path     = saved_paths[0]
    # Store filename with user_id folder prefix (e.g., "2/abc123.pdf")
    primary_filename = f"{current_user.id}/{saved_meta[0]['name']}"
    attempt_number   = attempts_used + 1

    # ── Plagiarism pipeline ───────────────────────────────────────────────────
    verdict          = 'accepted'
    reason           = 'Original Work'
    peer_score       = 0.0
    external_score   = 0.0
    ocr_confidence   = 100.0
    manual_review    = False
    plagiarism_report_data = {}
    extracted_text   = ''
    file_hash        = None

    if assignment.enable_plagiarism_check:
        try:
            other_subs = Submission.query.filter(
                Submission.course_id  == assignment.course_id,
                Submission.assignment_id == assignment_id,
                Submission.user_id    != current_user.id,
                Submission.status     == 'accepted',
            ).all()
            other_texts = []
            for s in other_subs:
                if not s.text_content:
                    continue
                # Resolve original filename from files_metadata JSON if available
                orig_name = s.filename or ''
                try:
                    import json as _j
                    meta = _j.loads(s.files_metadata or '[]')
                    if meta and isinstance(meta, list):
                        orig_name = meta[0].get('originalName', s.filename or '')
                except Exception:
                    pass
                other_texts.append({
                    'text':              s.text_content,
                    'author_username':   s.author.username,
                    'submission_id':     s.id,
                    'filename':          s.filename or '',
                    'original_filename': orig_name,
                })

            result = logic.run_plagiarism_check(
                file_path=primary_path,
                other_submissions=other_texts,
                threshold=assignment.similarity_threshold,
                check_handwritten=assignment.check_handwritten,
            )

            extracted_text  = result['text']
            file_hash       = result['file_hash']
            ocr_confidence  = result['ocr_confidence']
            verdict         = result['verdict']
            reason          = result['reason']
            peer_score      = result['peer_score']
            external_score  = result['external_score']

            # Exact-hash duplicate check (fastest path)
            if file_hash:
                dup = Submission.query.filter(
                    Submission.content_hash  == file_hash,
                    Submission.assignment_id == assignment_id,
                    Submission.user_id       != current_user.id,
                ).first()
                if dup:
                    verdict    = 'rejected'
                    reason     = f'Exact duplicate of {dup.author.username}\'s submission'
                    peer_score = 1.0
                    result['is_exact_duplicate'] = True

            if verdict == 'manual_review':
                manual_review = True

            plagiarism_report_data = {
                'verdict':           verdict,
                'threshold_used':    assignment.similarity_threshold,
                'peer_score':        round(peer_score * 100, 1),
                'external_score':    round(external_score, 1),
                'ocr_confidence':    round(ocr_confidence, 1),
                'is_exact_duplicate': result.get('is_exact_duplicate', False),
                'peer_details':      result.get('peer_details', {}),
                'external_details':  result.get('external_details', {}),
                'analysis_text':     result.get('analysis_text', ''),
                'preprocessing_applied': assignment.check_handwritten,
            }

        except Exception as exc:
            import traceback
            print(f'[ERROR] Plagiarism pipeline crashed:')
            traceback.print_exc()
            # Only fall back to manual_review for genuine infra failures
            # (missing model, FAISS crash, etc.) — NOT for logic errors.
            # The real error is now printed above so you can debug it.
            manual_review = True
            verdict       = 'manual_review'
            reason        = f'Pipeline error: {str(exc)[:120]}'

    else:
        # Plagiarism disabled — just extract text for future use
        try:
            extracted_text, _, file_hash, ocr_confidence = logic.extract_text(
                primary_path, check_handwritten=assignment.check_handwritten)
        except Exception as exc:
            print(f'[ERROR] Text extraction: {exc}')

    final_status = (
        'manual_review' if manual_review else
        'rejected'      if verdict == 'rejected' else
        'accepted'
    )

    new_sub = Submission(
        assignment_id=assignment_id,
        user_id=current_user.id,
        course_id=assignment.course_id,
        filename=primary_filename,
        files_metadata=json.dumps(saved_meta),
        text_content=extracted_text or None,
        content_hash=file_hash,
        attempt_number=attempt_number,
        is_late=is_late,
        score=max(peer_score, external_score / 100),
        peer_score=peer_score,
        external_score=external_score,
        status=final_status,
        verdict=final_status,
        reason=reason,
        manual_review=manual_review,
        ocr_confidence=ocr_confidence if ocr_confidence < 100 else None,
        plagiarism_report=json.dumps(plagiarism_report_data) if plagiarism_report_data else None,
        timestamp=_now(),
    )
    db.session.add(new_sub)
    db.session.commit()

    new_remaining = max(0, attempts_allowed - attempt_number)

    # Student-facing verdict (no scores, no matched names)
    if final_status == 'accepted':
        flash('✅ Your work passed the plagiarism check and has been received.', 'success')
    elif final_status == 'rejected':
        msg = '❌ Copied content was detected. Please resubmit with your own original work.'
        if new_remaining > 0:
            msg += f' ({new_remaining} attempt(s) remaining)'
        flash(msg, 'danger')
    else:
        flash('⏳ Your file quality requires manual review by your instructor.', 'warning')

    if is_late:
        flash('Note: This submission was received after the deadline.', 'info')

    return redirect(url_for('course_page', course_id=assignment.course_id))


# =============================================================================
# MANUAL REVIEW  (faculty)
# =============================================================================
@app.route('/submission/<int:submission_id>/review', methods=['GET', 'POST'])
@login_required
def manual_review(submission_id):
    if current_user.role != 'faculty':
        abort(403)
    sub = db.get_or_404(Submission, submission_id)

    if request.method == 'POST':
        action = request.form.get('action')
        notes  = request.form.get('notes', '').strip()
        if action not in ('approved', 'rejected'):
            flash('Invalid action.', 'danger')
            return redirect(request.url)
        sub.manual_review_action = action
        sub.manual_review_notes  = notes
        sub.status               = 'accepted' if action == 'approved' else 'rejected'
        sub.verdict              = sub.status
        sub.manual_review        = False
        db.session.commit()
        flash(f'Submission {action} successfully.', 'success')
        return redirect(url_for('view_reports', course_id=sub.course_id))

    report = {}
    if sub.plagiarism_report:
        try:
            report = json.loads(sub.plagiarism_report)
        except Exception:
            pass

    return render_template('manual_review.html', submission=sub, report=report)


# =============================================================================
# REPORTS  (faculty)
# =============================================================================
@app.route('/course/<int:course_id>/reports')
@login_required
def view_reports(course_id):
    if current_user.role not in ('faculty', 'admin'):
        abort(403)
    course      = db.get_or_404(Course, course_id)
    assignments = Assignment.query.filter_by(course_id=course_id).all()
    total       = Submission.query.filter_by(course_id=course_id).count()
    rejected    = Submission.query.filter_by(course_id=course_id, status='rejected').count()
    pending     = Submission.query.filter_by(course_id=course_id, manual_review=True).count()

    return render_template('reports.html',
                           course=course,
                           assignments=assignments,
                           total=total,
                           rejected=rejected,
                           pending_review=pending)



# =============================================================================
# FACULTY BULK PLAGIARISM CHECK (BACKGROUND TASK)
# =============================================================================
def run_bulk_check_task(app, run_id, temp_dir, assignment_id, course_id, current_user_id):
    """Background task to run bulk plagiarism check."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from sqlalchemy.orm import joinedload

    with app.app_context():
        t0 = time.time()
        try:
            bulk_run = BulkCheckRun.query.get(run_id)
            if not bulk_run: return
            bulk_run.status = 'processing'
            db.session.commit()
            assignment = Assignment.query.get(assignment_id)
            allowed_types = [t.strip().lower() for t in (assignment.allowed_file_types or '').split(',') if t.strip()]

            # --- Phase 0: Extraction (Moved to Background) ---
            # Extract any ZIPS found in temp_dir
            for root, _, fs in os.walk(temp_dir):
                for f in fs:
                    if f.lower().endswith('.zip'):
                        zp = os.path.join(root, f)
                        try:
                            with zipfile.ZipFile(zp, 'r') as z:
                                for member in z.namelist():
                                    if member.endswith('/'): continue
                                    dest = os.path.normpath(os.path.join(temp_dir, member))
                                    if not dest.startswith(temp_dir): continue
                                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                                    with z.open(member) as src, open(dest, 'wb') as dst: dst.write(src.read())
                            os.remove(zp)
                        except Exception as e:
                            print(f"[Bulk-BG] ZIP Extraction Error: {e}", flush=True)

            # Re-scan for full file list
            filtered_paths = []
            for root, _, files in os.walk(temp_dir):
                for f in files:
                    p = os.path.join(root, f)
                    ext = f.rsplit('.', 1)[-1].lower() if '.' in f else ''
                    if not allowed_types or ext in allowed_types:
                        filtered_paths.append(p)
            
            bulk_run.total_files = len(filtered_paths)
            db.session.commit()
            print(f"[Bulk-BG] Task #{run_id} starting for {len(filtered_paths)} files...", flush=True)

            if not filtered_paths:
                bulk_run.status = 'completed'; db.session.commit()
                return

            # --- Phase 1: Text extraction ---
            extracted = {}
            def _extract_one(p):
                return p, logic.extract_text_bulk(p)

            _workers = min(2, len(filtered_paths))
            with ThreadPoolExecutor(max_workers=_workers) as pool:
                futures = {pool.submit(_extract_one, p): p for p in filtered_paths}
                for fut in as_completed(futures):
                    p_current = futures[fut]
                    try:
                        path, result = fut.result()
                        extracted[path] = result
                        bulk_run.processed_count += 1
                        db.session.commit()
                        print(f"   ↳ BG Prog: {bulk_run.processed_count}/{len(filtered_paths)} ({os.path.basename(path)})", flush=True)
                    except Exception as e:
                        print(f"   ↳ [ERROR] BG Extraction: {e}", flush=True)
                        extracted[p_current] = ("", None, None, 0.0)
                        bulk_run.processed_count += 1
                        db.session.commit()

            # --- Phase 2: Build submission lists ---
            base_others = []
            db_submissions = Submission.query.filter(
                Submission.course_id == course_id,
                Submission.assignment_id == assignment_id,
                Submission.status == 'accepted'
            ).all()
            for sub in db_submissions:
                if not sub.text_content: continue
                orig_name = sub.filename or ''
                try:
                    m = json.loads(sub.files_metadata or '[]')
                    if isinstance(m, list) and m:
                        orig_name = m[0].get('originalName', orig_name)
                except Exception: pass
                base_others.append({
                    'text': sub.text_content,
                    'author_username': sub.author.username if sub.author else 'Unknown',
                    'submission_id': sub.id,
                    'filename': sub.filename or '',
                    'original_filename': orig_name,
                    '_unique_id': f'db_{sub.id}',
                })

            local_submissions = []
            for p in filtered_paths:
                txt, _, fhash, conf = extracted.get(p, ("", None, None, 0.0))
                local_submissions.append({
                    'text': txt, 'author_username': os.path.basename(p),
                    'submission_id': None, 'filename': os.path.basename(p),
                    'original_filename': os.path.basename(p),
                    '_unique_id': f'local_{p}', '_path': p,
                    '_file_hash': fhash, '_ocr_confidence': conf,
                })
            all_submissions = base_others + local_submissions

            # --- Phase 3: Embeddings ---
            precomputed_embeddings = None
            if hasattr(logic, '_HAS_ST') and logic._HAS_ST:
                try:
                    st_model = logic._get_st_model() # Lazy loading for 'Instant Wake-up'
                    unique_texts = []
                    seen = set()
                    for s in all_submissions:
                        text = s.get('text')
                        if not text: continue
                        cl = logic.clean_text(text)
                        if cl and cl not in seen:
                            seen.add(cl); unique_texts.append(cl)
                    if unique_texts:
                        embeddings = st_model.encode(unique_texts, batch_size=16, convert_to_numpy=True).astype("float32")
                        if hasattr(logic, '_HAS_FAISS') and logic._HAS_FAISS:
                            import faiss as _faiss
                            _faiss.normalize_L2(embeddings)
                        else:
                            import numpy as np
                            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
                            embeddings = embeddings / np.maximum(norms, 1e-10)
                        precomputed_embeddings = {t: emb for t, emb in zip(unique_texts, embeddings)}
                except Exception as e:
                    print(f"[Bulk-BG] Embedding error: {e}", flush=True)

            # --- Phase 4: Plagiarism checks ---
            results = []
            _threshold = assignment.similarity_threshold
            for lsub in local_submissions:
                try:
                    _path, _txt, _hash, _conf, _uid = lsub['_path'], lsub['text'], lsub['_file_hash'], lsub['_ocr_confidence'], lsub['_unique_id']
                    _others = [s for s in all_submissions if s['_unique_id'] != _uid]
                    _rep = logic.bulk_run_plagiarism_check_preextracted(
                        text=_txt, file_hash=_hash, ocr_confidence=_conf or 100.0,
                        other_submissions=_others, threshold=_threshold,
                        precomputed_embeddings=precomputed_embeddings, filename=os.path.basename(_path),
                    )
                    if _hash:
                        for s in _others:
                            oh = s.get('_file_hash') or s.get('content_hash')
                            if oh and oh == _hash:
                                _rep['verdict'] = 'rejected'
                                _rep['reason']  = f"Exact duplicate of {s['author_username']}"
                                _rep['peer_score'] = 1.0; _rep['is_exact_duplicate'] = True
                                break
                    results.append({
                        'filename': os.path.relpath(_path, temp_dir),
                        'verdict': _rep.get('verdict', 'unknown'),
                        'reason': _rep.get('reason', ''),
                        'peer_score': round(_rep.get('peer_score', 0.0) * 100, 1),
                        'external_score': _rep.get('external_score', 0.0),
                        'ocr_confidence': _rep.get('ocr_confidence', 0.0),
                        'analysis_text': _rep.get('analysis_text', ''),
                        'peer_details': _rep.get('peer_details', {}),
                    })
                except Exception as e:
                    results.append({
                        'filename': os.path.basename(lsub.get('_path', '')),
                        'verdict': 'error', 'reason': str(e),
                        'peer_score': 0.0, 'external_score': 0.0, 'ocr_confidence': 0.0,
                        'analysis_text': '', 'peer_details': {},
                    })

            # --- Phase 5: Finalize ---
            elapsed = round(time.time() - t0, 1)
            for row in results:
                db.session.add(BulkCheckResult(
                    run_id=run_id, filename=row['filename'],
                    verdict=row['verdict'], reason=str(row['reason'])[:255],
                    peer_score=row['peer_score'], external_score=row['external_score'],
                    ocr_confidence=row['ocr_confidence'], analysis_text=row['analysis_text'],
                    peer_details=json.dumps(row['peer_details']),
                ))
            
            bulk_run.status = 'completed'
            bulk_run.elapsed_sec = elapsed
            bulk_run.accepted = sum(1 for r in results if r['verdict'] == 'accepted')
            bulk_run.rejected = sum(1 for r in results if r['verdict'] == 'rejected')
            bulk_run.manual_review = sum(1 for r in results if r['verdict'] == 'manual_review' or r['verdict'] == 'error')
            db.session.commit()
            print(f"[Bulk-BG] Task #{run_id} finished in {elapsed}s", flush=True)

        except Exception as e:
            db.session.rollback()
            try:
                br = BulkCheckRun.query.get(run_id)
                if br: br.status = 'error'; db.session.commit()
            except: pass
            print(f"[Bulk-BG] Task #{run_id} failed: {e}", flush=True)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


@app.route('/course/<int:course_id>/assignment/<int:assignment_id>/bulk_check', methods=['GET', 'POST'])
@login_required
def bulk_check(course_id, assignment_id):
    if current_user.role != 'faculty': abort(403)
    course = db.get_or_404(Course, course_id)
    assignment = db.get_or_404(Assignment, assignment_id)
    if assignment.course_id != course.id: abort(404)

    if request.method == 'GET':
        history = BulkCheckRun.query.filter_by(assignment_id=assignment_id, course_id=course_id).order_by(BulkCheckRun.created_at.desc()).limit(10).all()
        return render_template('bulk_check.html', course=course, assignment=assignment, history=history)

    upload_zip = request.files.get('zipfile')
    upload_files = request.files.getlist('files')
    if not (upload_zip and upload_zip.filename) and not (upload_files and upload_files[0].filename):
        flash('Please provide files.', 'danger'); return redirect(request.url)

    temp_dir = tempfile.mkdtemp(prefix=f'bulk_{course_id}_{assignment_id}_')
    try:
        # Save ZIP or files to temp_dir
        if upload_zip and upload_zip.filename:
            upload_zip.save(os.path.join(temp_dir, secure_filename(upload_zip.filename)))
        for fs in upload_files:
            if fs and fs.filename:
                fs.save(os.path.join(temp_dir, secure_filename(fs.filename)))

        bulk_run = BulkCheckRun(assignment_id=assignment.id, course_id=course_id, run_by=current_user.id,
                                total_files=0, processed_count=0, status='pending')
        db.session.add(bulk_run); db.session.commit()

        # Start Background Thread (Instant Start)
        threading.Thread(target=run_bulk_check_task, args=(current_app._get_current_object(), bulk_run.id, temp_dir, assignment.id, course.id, current_user.id), daemon=True).start()

        return redirect(url_for('bulk_status', course_id=course_id, assignment_id=assignment_id, run_id=bulk_run.id))

    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        flash(f'Scan error: {e}', 'danger'); return redirect(request.url)


@app.route('/course/<int:course_id>/assignment/<int:assignment_id>/bulk/status/<int:run_id>')
@login_required
def bulk_status(course_id, assignment_id, run_id):
    if current_user.role != 'faculty': abort(403)
    run = db.get_or_404(BulkCheckRun, run_id)
    db.session.refresh(run) # Force sync with background scanning thread
    if run.status == 'completed':
        return redirect(url_for('bulk_check_run_view', course_id=course_id, assignment_id=assignment_id, run_id=run.id))
    return render_template('bulk_status.html', course=db.get_or_404(Course, course_id), assignment=db.get_or_404(Assignment, assignment_id), run=run)


@app.route('/course/<int:course_id>/assignment/<int:assignment_id>/download_bulk_csv')
@login_required
def download_bulk_csv(course_id, assignment_id):
    if current_user.role != 'faculty':
        abort(403)

    course = db.get_or_404(Course, course_id)
    assignment = db.get_or_404(Assignment, assignment_id)
    if assignment.course_id != course.id:
        abort(404)

    run_id = request.args.get('run_id', type=int)
    results = []
    
    if run_id:
        db_results = BulkCheckResult.query.filter_by(run_id=run_id).all()
        for r in db_results:
            results.append({
                'filename': r.filename,
                'verdict': r.verdict,
                'reason': r.reason,
                'peer_score': r.peer_score,
                'external_score': r.external_score,
                'ocr_confidence': r.ocr_confidence,
                'analysis_text': r.analysis_text
            })
    else:
        # Fallback to session for very old unsaved runs
        session_key = f'bulk_check_{course_id}_{assignment_id}'
        results = session.get(session_key, [])

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Filename', 'Verdict', 'Reason', 'Peer Score (%)', 'External Score (%)', 'OCR Confidence (%)', 'Analysis Text'])

    for row in results:
        writer.writerow([
            row.get('filename', ''),
            row.get('verdict', ''),
            row.get('reason', ''),
            row.get('peer_score', 0),
            row.get('external_score', 0),
            row.get('ocr_confidence', 0),
            row.get('analysis_text', '').replace('\n', ' | ')
        ])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=bulk_check_{assignment.title}_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'}
    )


@app.route('/course/<int:course_id>/assignment/<int:assignment_id>/download_bulk_excel')
@login_required
def download_bulk_excel(course_id, assignment_id):
    if current_user.role != 'faculty':
        abort(403)

    course = db.get_or_404(Course, course_id)
    assignment = db.get_or_404(Assignment, assignment_id)
    if assignment.course_id != course.id:
        abort(404)

    run_id = request.args.get('run_id', type=int)
    results = []
    
    if run_id:
        db_results = BulkCheckResult.query.filter_by(run_id=run_id).all()
        for r in db_results:
            results.append({
                'filename': r.filename,
                'verdict': r.verdict,
                'reason': r.reason,
                'peer_score': r.peer_score,
                'external_score': r.external_score,
                'ocr_confidence': r.ocr_confidence,
                'analysis_text': r.analysis_text
            })
    else:
        session_key = f'bulk_check_{course_id}_{assignment_id}'
        results = session.get(session_key, [])

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Filename', 'Verdict', 'Reason', 'Peer Score (%)', 'External Score (%)', 'OCR Confidence (%)', 'Analysis Text'])

    for row in results:
        writer.writerow([
            row.get('filename', ''),
            row.get('verdict', ''),
            row.get('reason', ''),
            row.get('peer_score', 0),
            row.get('external_score', 0),
            row.get('ocr_confidence', 0),
            row.get('analysis_text', '').replace('\n', ' | ')
        ])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='application/vnd.ms-excel',
        headers={'Content-Disposition': f'attachment; filename=bulk_check_{assignment.title}_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'}
    )


# =============================================================================
# BULK CHECK HISTORY — view / delete a saved run
# =============================================================================
@app.route('/course/<int:course_id>/assignment/<int:assignment_id>/bulk_check/run/<int:run_id>')
@login_required
def bulk_check_run_view(course_id, assignment_id, run_id):
    if current_user.role != 'faculty':
        abort(403)
    course     = db.get_or_404(Course, course_id)
    assignment = db.get_or_404(Assignment, assignment_id)
    if assignment.course_id != course.id:
        abort(404)
    run = db.get_or_404(BulkCheckRun, run_id)
    if run.assignment_id != assignment.id or run.course_id != course.id:
        abort(404)
    return render_template('bulk_check_run.html',
                           course=course, assignment=assignment, run=run)


@app.route('/course/<int:course_id>/assignment/<int:assignment_id>/bulk_check/run/<int:run_id>/delete',
           methods=['POST'])
@login_required
def bulk_check_run_delete(course_id, assignment_id, run_id):
    if current_user.role != 'faculty':
        abort(403)
    run = db.get_or_404(BulkCheckRun, run_id)
    if run.assignment_id != assignment_id or run.course_id != course_id:
        abort(404)
    db.session.delete(run)
    db.session.commit()
    flash('Bulk check run deleted.', 'success')
    return redirect(url_for('bulk_check', course_id=course_id, assignment_id=assignment_id))



@app.route('/course/<int:course_id>/download_reports_csv')
@login_required
def download_reports_csv(course_id):
    if current_user.role not in ('faculty', 'admin'):
        abort(403)

    course = db.get_or_404(Course, course_id)
    assignments = Assignment.query.filter_by(course_id=course_id).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Assignment', 'Student', 'Attempt', 'Similarity (%)', 'Verdict', 'Status', 'Grade', 'Feedback', 'Timestamp'])

    for assign in assignments:
        for sub in assign.submissions:
            report = sub.plagiarism_report or '{}'
            try:
                report_data = json.loads(report)
                score_pct = round((sub.score or 0) * 100, 1)
            except:
                score_pct = 0

            writer.writerow([
                assign.title,
                sub.author.username,
                sub.attempt_number or 1,
                score_pct,
                sub.verdict or sub.status,
                sub.status,
                sub.faculty_grade or '',
                sub.faculty_feedback or '',
                sub.timestamp.strftime('%Y-%m-%d %H:%M:%S')
            ])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=reports_{course.code}_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'}
    )


@app.route('/course/<int:course_id>/download_reports_excel')
@login_required
def download_reports_excel(course_id):
    # Same as CSV but with Excel mimetype
    if current_user.role not in ('faculty', 'admin'):
        abort(403)

    course = db.get_or_404(Course, course_id)
    assignments = Assignment.query.filter_by(course_id=course_id).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Assignment', 'Student', 'Attempt', 'Similarity (%)', 'Verdict', 'Status', 'Grade', 'Feedback', 'Timestamp'])

    for assign in assignments:
        for sub in assign.submissions:
            report = sub.plagiarism_report or '{}'
            try:
                report_data = json.loads(report)
                score_pct = round((sub.score or 0) * 100, 1)
            except:
                score_pct = 0

            writer.writerow([
                assign.title,
                sub.author.username,
                sub.attempt_number or 1,
                score_pct,
                sub.verdict or sub.status,
                sub.status,
                sub.faculty_grade or '',
                sub.faculty_feedback or '',
                sub.timestamp.strftime('%Y-%m-%d %H:%M:%S')
            ])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='application/vnd.ms-excel',
        headers={'Content-Disposition': f'attachment; filename=reports_{course.code}_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'}
    )


# Full JSON report endpoint — faculty only
@app.route('/submission/<int:submission_id>/plagiarism_report')
@login_required
def plagiarism_report_json(submission_id):
    if current_user.role not in ('faculty', 'admin'):
        abort(403)
    sub = db.get_or_404(Submission, submission_id)
    if not sub.plagiarism_report:
        return jsonify({'error': 'No report available.'}), 404
    try:
        report = json.loads(sub.plagiarism_report)
    except Exception:
        return jsonify({'error': 'Report data corrupt.'}), 500
    return jsonify({
        'submission_id':  sub.id,
        'student':        sub.author.username,
        'attempt_number': sub.attempt_number,
        'is_late':        sub.is_late,
        'timestamp':      sub.timestamp.isoformat(),
        'report':         report,
    })


# =============================================================================
# GRADING  (faculty)
# =============================================================================
@app.route('/submission/<int:submission_id>/grade', methods=['POST'])
@login_required
def grade_submission(submission_id):
    if current_user.role != 'faculty':
        abort(403)
    sub = db.get_or_404(Submission, submission_id)
    grade_str = request.form.get('grade', '').strip()
    try:
        sub.faculty_grade = float(grade_str)
    except ValueError:
        flash('Invalid grade value.', 'danger')
        return redirect(url_for('view_reports', course_id=sub.course_id))
    sub.faculty_feedback = request.form.get('feedback', '').strip()
    db.session.commit()
    flash('Grade saved.', 'success')
    return redirect(url_for('view_reports', course_id=sub.course_id))


# =============================================================================
# ANNOUNCEMENTS  (faculty)
# =============================================================================
@app.route('/course/<int:course_id>/announce', methods=['POST'])
@login_required
def post_announcement(course_id):
    if current_user.role != 'faculty':
        abort(403)
    ann = Announcement(
        course_id=course_id,
        title=request.form.get('title', '').strip(),
        content=request.form.get('content', '').strip(),
        is_pinned=request.form.get('is_pinned') == 'on',
    )
    db.session.add(ann)
    db.session.commit()
    flash('Announcement posted.', 'success')
    return redirect(url_for('course_page', course_id=course_id))


# =============================================================================
# FILE DOWNLOAD  (download static files with proper headers)
# =============================================================================
@app.route('/download_file/<path:filepath>')
@login_required
def download_file(filepath):
    """
    Download a file from the uploads folder with correct headers.
    This ensures files are downloaded instead of displayed in browser.
    """
    # Security: prevent directory traversal attacks
    filepath = filepath.replace('..', '').replace('\\', '/')
    
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filepath)
    
    # Verify the file exists and is within the uploads folder
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        abort(404)
    
    # Ensure the resolved path is still within UPLOAD_FOLDER
    real_base = os.path.realpath(app.config['UPLOAD_FOLDER'])
    real_file = os.path.realpath(file_path)
    if not real_file.startswith(real_base):
        abort(403)
    
    # Get the filename for the download
    filename = os.path.basename(file_path)
    
    # Determine MIME type
    mime_type, _ = mimetypes.guess_type(file_path)
    if mime_type is None:
        mime_type = 'application/octet-stream'
    
    # Send the file with proper headers
    return send_file(
        file_path,
        mimetype=mime_type,
        as_attachment=True,
        download_name=filename
    )


# =============================================================================
# ENTRY POINT
# =============================================================================
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        check_dependencies()
        # Try to sync vector engine, but don't crash if torch/FAISS fails to load
        try:
            sync_vector_engine()
        except Exception as e:
            print(f"[WARN] Vector engine sync failed (app will run without it): {e}")
        # Pre-load ML models at startup so bulk checks don't trigger downloads mid-request
        try:
            logic.warmup_models()
        except Exception as e:
            print(f"[WARN] Model warmup failed (will load on first use): {e}")
    app.run(debug=True, use_reloader=False)