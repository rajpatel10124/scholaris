from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
import secrets
import string

db = SQLAlchemy()

# ── Many-to-Many: student ↔ course ──────────────────────────────────────────
enrollments = db.Table('enrollments',
    db.Column('student_id', db.Integer, db.ForeignKey('user.id'),   primary_key=True),
    db.Column('course_id',  db.Integer, db.ForeignKey('course.id'), primary_key=True)
)


def _gen_invite_code(length: int = 8) -> str:
    """Generate an 8-character alphanumeric invite code."""
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


# ── USER ─────────────────────────────────────────────────────────────────────
class User(db.Model, UserMixin):
    id       = db.Column(db.Integer,     primary_key=True)
    username = db.Column(db.String(80),  unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role     = db.Column(db.String(10),  nullable=False)  # 'admin'|'faculty'|'student'

    # Extended profile
    first_name = db.Column(db.String(60),  nullable=True)
    last_name  = db.Column(db.String(60),  nullable=True)
    email      = db.Column(db.String(120), unique=True, nullable=True)

    # Auth state
    is_active       = db.Column(db.Boolean,  default=True)
    email_verified  = db.Column(db.Boolean,  default=False)
    last_login      = db.Column(db.DateTime, nullable=True)
    failed_attempts = db.Column(db.Integer,  default=0)
    locked_until    = db.Column(db.DateTime, nullable=True)

    # Password reset
    reset_token         = db.Column(db.String(64), nullable=True)
    reset_token_expires = db.Column(db.DateTime,   nullable=True)

    enrolled_courses = db.relationship(
        'Course', secondary=enrollments,
        backref=db.backref('students', lazy='dynamic')
    )

    @property
    def full_name(self):
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}"
        return self.username

    def get_id(self):
        return str(self.id)

    def is_locked(self):
        return bool(self.locked_until and datetime.utcnow() < self.locked_until)

    def minutes_locked(self):
        if self.locked_until:
            delta = self.locked_until - datetime.utcnow()
            return max(0, int(delta.total_seconds() / 60))
        return 0


# ── COURSE ────────────────────────────────────────────────────────────────────
class Course(db.Model):
    id         = db.Column(db.Integer,     primary_key=True)
    name       = db.Column(db.String(100), nullable=False)
    code       = db.Column(db.String(20),  unique=True, nullable=False)
    faculty_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    # Extended fields
    description = db.Column(db.Text,       nullable=True)
    semester    = db.Column(db.String(20), nullable=True)
    year        = db.Column(db.Integer,    nullable=True)
    cover_color = db.Column(db.String(10), default='#5b5ef4')

    # Access
    invite_code = db.Column(db.String(12), unique=True, nullable=True)
    is_active   = db.Column(db.Boolean,   default=True)
    created_at  = db.Column(db.DateTime,  default=datetime.utcnow)

    faculty       = db.relationship('User', backref=db.backref('managed_courses', lazy=True))
    assignments   = db.relationship('Assignment',   backref='course', lazy=True,
                                    cascade='all, delete-orphan')
    announcements = db.relationship('Announcement', backref='course', lazy=True,
                                    order_by='Announcement.is_pinned.desc(), Announcement.created_at.desc()',
                                    cascade='all, delete-orphan')

    def generate_invite_code(self):
        code = _gen_invite_code()
        while Course.query.filter_by(invite_code=code).first():
            code = _gen_invite_code()
        self.invite_code = code
        return code


# ── ANNOUNCEMENT ─────────────────────────────────────────────────────────────
class Announcement(db.Model):
    id         = db.Column(db.Integer,     primary_key=True)
    course_id  = db.Column(db.Integer, db.ForeignKey('course.id'), nullable=False)
    title      = db.Column(db.String(200), nullable=False)
    content    = db.Column(db.Text,        nullable=False)
    is_pinned  = db.Column(db.Boolean,     default=False)
    created_at = db.Column(db.DateTime,    default=datetime.utcnow)


# ── ASSIGNMENT ────────────────────────────────────────────────────────────────
class Assignment(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey('course.id'), nullable=False)

    title         = db.Column(db.String(100), nullable=False)
    description   = db.Column(db.Text,        nullable=True)
    instructions  = db.Column(db.Text,        nullable=True)
    deadline      = db.Column(db.DateTime,    nullable=False)
    question_file = db.Column(db.String(255), nullable=True)
    is_published  = db.Column(db.Boolean,     default=True)

    max_marks             = db.Column(db.Integer, default=100)
    attempt_limit         = db.Column(db.Integer, default=3)
    allow_late_submission = db.Column(db.Boolean, default=False)
    allowed_file_types    = db.Column(db.String(255), default='pdf,docx,jpg,png')
    max_file_size         = db.Column(db.Integer, default=10)

    enable_plagiarism_check = db.Column(db.Boolean, default=True)
    check_handwritten       = db.Column(db.Boolean, default=True)
    similarity_threshold    = db.Column(db.Integer, default=40)

    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    submissions = db.relationship('Submission', backref='assignment', lazy=True,
                                  cascade='all, delete-orphan')


# ── SUBMISSION ────────────────────────────────────────────────────────────────
class Submission(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey('assignment.id'), nullable=False)
    user_id       = db.Column(db.Integer, db.ForeignKey('user.id'),       nullable=False)
    course_id     = db.Column(db.Integer, db.ForeignKey('course.id'),     nullable=False)

    filename       = db.Column(db.String(255), nullable=True)
    files_metadata = db.Column(db.Text,        nullable=True)  # JSON array

    text_content = db.Column(db.Text,       nullable=True)
    content_hash = db.Column(db.String(64), nullable=True)

    attempt_number = db.Column(db.Integer, default=1)
    is_late        = db.Column(db.Boolean, default=False)

    score          = db.Column(db.Float, default=0.0)
    peer_score     = db.Column(db.Float, default=0.0)
    external_score = db.Column(db.Float, default=0.0)

    status  = db.Column(db.String(20), nullable=True)
    verdict = db.Column(db.String(20), nullable=True)
    reason  = db.Column(db.String(255), nullable=True)

    manual_review        = db.Column(db.Boolean,     default=False)
    manual_review_action = db.Column(db.String(20),  nullable=True)
    manual_review_notes  = db.Column(db.Text,        nullable=True)

    ocr_confidence    = db.Column(db.Float, nullable=True)
    plagiarism_report = db.Column(db.Text,  nullable=True)  # JSON

    faculty_grade    = db.Column(db.Float, nullable=True)
    faculty_feedback = db.Column(db.Text,  nullable=True)

    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    author = db.relationship('User', backref=db.backref('submissions', lazy=True))


# ── BULK CHECK RUN ────────────────────────────────────────────────────────────
class BulkCheckRun(db.Model):
    """One bulk plagiarism check session (single ZIP upload)."""
    __tablename__ = 'bulk_check_run'

    id            = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey('assignment.id'), nullable=False)
    course_id     = db.Column(db.Integer, db.ForeignKey('course.id'),     nullable=False)
    run_by        = db.Column(db.Integer, db.ForeignKey('user.id'),       nullable=False)

    # Summary
    total_files     = db.Column(db.Integer, default=0)
    processed_count = db.Column(db.Integer, default=0)
    status          = db.Column(db.String(20), default='pending') # pending|processing|completed|error
    
    accepted      = db.Column(db.Integer, default=0)
    rejected      = db.Column(db.Integer, default=0)
    manual_review = db.Column(db.Integer, default=0)
    elapsed_sec   = db.Column(db.Float,   nullable=True)

    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    results    = db.relationship('BulkCheckResult', backref='run', lazy=True,
                                 cascade='all, delete-orphan',
                                 order_by='BulkCheckResult.id')
    faculty    = db.relationship('User',       foreign_keys=[run_by])
    assignment = db.relationship('Assignment', foreign_keys=[assignment_id])
    course     = db.relationship('Course',     foreign_keys=[course_id])


# ── BULK CHECK RESULT ─────────────────────────────────────────────────────────
class BulkCheckResult(db.Model):
    """One file's result inside a BulkCheckRun."""
    __tablename__ = 'bulk_check_result'

    id         = db.Column(db.Integer, primary_key=True)
    run_id     = db.Column(db.Integer, db.ForeignKey('bulk_check_run.id'), nullable=False)

    filename       = db.Column(db.String(255), nullable=True)
    verdict        = db.Column(db.String(20),  nullable=True)   # accepted|rejected|manual_review
    reason         = db.Column(db.String(255), nullable=True)
    peer_score     = db.Column(db.Float,  default=0.0)
    external_score = db.Column(db.Float,  default=0.0)
    ocr_confidence = db.Column(db.Float,  default=0.0)
    analysis_text  = db.Column(db.Text,   nullable=True)
    peer_details   = db.Column(db.Text,   nullable=True)   # JSON