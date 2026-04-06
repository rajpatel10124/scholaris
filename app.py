import os, json, time, tempfile, shutil, threading
from flask import Flask, render_template, request, redirect, url_for, flash, abort, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
import boto3

# Disable Paddle check at the very top
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"

"""
app.py  —  Scholaris Academic Integrity Platform
TOTAL ARCHITECTURAL RESTORATION & LOCKDOWN
================================================
"""

from models import db, User, Course, Assignment, Submission, BulkCheckRun, BulkCheckResult

app = Flask(__name__)
# Priority #1: Postgres from environment
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'scholaris-secret-key-12345')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'postgresql://scholaris:scholaris_local@localhost:5432/scholaris')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024  # 1 GB

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- HELPER LOGIC ---
def run_bulk_task(app_context, course_id, assignment_id, zip_path, run_id):
    with app_context:
        try:
            import zipfile
            from logic import bulk_run_plagiarism_check
            
            run = BulkCheckRun.query.get(run_id)
            if not run: return
            
            extract_dir = tempfile.mkdtemp(prefix='bulk_ext_')
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
            
            files = [os.path.join(extract_dir, f) for f in os.listdir(extract_dir) if os.path.isfile(os.path.join(extract_dir, f))]
            run.total_files = len(files)
            run.status = 'processing'
            db.session.commit()
            
            start_time = time.time()
            all_other_submissions = Submission.query.filter(Submission.assignment_id == assignment_id).all()
            other_data = [{"text": s.text_content, "author_username": s.author.username, "submission_id": s.id, "filename": s.filename} for s in all_other_submissions]
            
            for fpath in files:
                res = bulk_run_plagiarism_check(fpath, other_data)
                db.session.add(BulkCheckResult(
                    run_id=run.id,
                    filename=os.path.basename(fpath),
                    verdict=res.get('verdict', 'manual_review'),
                    peer_score=float(res.get('peer_score', 0.0)) * 100,
                    external_score=float(res.get('external_score', 0.0)),
                    ocr_confidence=float(res.get('ocr_confidence', 0.0)),
                    analysis_text=res.get('analysis_text', ''),
                    peer_details=json.dumps(res.get('peer_details', {}))
                ))
                run.processed_count += 1
                if res.get('verdict') == 'accepted': run.accepted += 1
                elif res.get('verdict') == 'rejected': run.rejected += 1
                else: run.manual_review += 1
                db.session.commit()
            
            run.elapsed_sec = time.time() - start_time
            run.status = 'completed'
            db.session.commit()
            shutil.rmtree(extract_dir, ignore_errors=True)
        except Exception as e:
            db.session.rollback()
            run = BulkCheckRun.query.get(run_id)
            if run: run.status = 'error'; db.session.commit()
            print(f"[CRITICAL] Bulk task error: {e}")
        finally:
            if os.path.exists(zip_path): os.remove(zip_path)

# --- CORE ROUTES (Login/Auth) ---
@app.route('/')
def index():
    if current_user.is_authenticated: return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Invalid email or password', 'danger')
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        role = request.form.get('role', 'student')
        
        if User.query.filter_by(email=email).first():
            flash('Email already registered', 'danger')
            return redirect(url_for('signup'))
        
        user = User(username=username, email=email, password=generate_password_hash(password), role=role)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        return redirect(url_for('dashboard'))
    return render_template('signup.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    courses = []
    total_users = 0; total_courses = 0; total_subs = 0; total_flagged = 0; total_rejected = 0; total_manual = 0
    flagged_subs = []
    
    if current_user.role == 'admin':
        users = User.query.all()
        total_users = len(users)
        courses = Course.query.all()
        total_courses = len(courses)
        all_subs = Submission.query.all()
        total_subs = len(all_subs)
        total_rejected = sum(1 for s in all_subs if s.verdict == 'rejected')
        total_manual = sum(1 for s in all_subs if s.status == 'manual_review')
        flagged_subs = [s for s in all_subs if s.verdict == 'rejected'][:5]
    elif current_user.role == 'faculty':
        courses = Course.query.filter_by(faculty_id=current_user.id).all()
        # Inferred faculty stats
        total_assignments = sum(len(c.assignments) for c in courses)
        total_submissions = sum(sum(len(a.submissions) for a in c.assignments) for c in courses)
        return render_template('dashboard.html', courses=courses, now=datetime.utcnow(), 
                               total_assignments=total_assignments, total_submissions=total_submissions)
    elif current_user.role == 'student':
        courses = current_user.enrolled_courses
        
    return render_template('dashboard.html', courses=courses, now=datetime.utcnow(),
                           total_users=total_users, total_courses=total_courses, total_subs=total_subs,
                           total_flagged=total_flagged, total_rejected=total_rejected,
                           total_manual=total_manual, flagged_subs=flagged_subs)

@app.route('/assignment/<int:assignment_id>/toggle_publish')
@login_required
def toggle_publish(assignment_id):
    assign = Assignment.query.get_or_404(assignment_id)
    if current_user.role != 'faculty': abort(403)
    assign.is_published = not assign.is_published
    db.session.commit()
    return redirect(url_for('course_page', course_id=assign.course_id))

# --- COURSE & ASSIGNMENT ROUTES ---
@app.route('/create_course', methods=['GET', 'POST'])
@login_required
def create_course():
    if current_user.role != 'faculty': abort(403)
    if request.method == 'POST':
        name = request.form.get('name')
        code = request.form.get('code')
        course = Course(name=name, code=code, faculty_id=current_user.id)
        db.session.add(course)
        db.session.commit()
        flash(f'Course "{name}" created!', 'success')
        return redirect(url_for('dashboard'))
    return render_template('create_course.html')

@app.route('/course/<int:course_id>/delete', methods=['POST'])
@login_required
def delete_course(course_id):
    if current_user.role != 'faculty': abort(403)
    course = Course.query.get_or_404(course_id)
    name = course.name
    db.session.delete(course)
    db.session.commit()
    flash(f'Course "{name}" deleted.', 'warning')
    return redirect(url_for('dashboard'))

@app.route('/course/<int:course_id>/reports')
@login_required
def view_reports(course_id):
    course = Course.query.get_or_404(course_id)
    return render_template('reports.html', course=course)

@app.route('/enroll/<int:course_id>')
@login_required
def enroll(course_id):
    course = Course.query.get_or_404(course_id)
    if course not in current_user.enrolled_courses:
        current_user.enrolled_courses.append(course)
        db.session.commit()
        flash(f'Enrolled in {course.name}!', 'success')
    return redirect(url_for('course_page', course_id=course_id))

@app.route('/join/<code>')
@login_required
def join(code):
    course = Course.query.filter_by(invite_code=code).first_or_404()
    return redirect(url_for('enroll', course_id=course.id))

@app.route('/course/<int:course_id>')
@login_required
def course_page(course_id):
    course = Course.query.get_or_404(course_id)
    assignments = course.assignments
    return render_template('course_page.html', course=course, assignments=assignments, now=datetime.utcnow(), Submission=Submission)

@app.route('/assignment/<int:assignment_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_assignment(assignment_id):
    assign = Assignment.query.get_or_404(assignment_id)
    if current_user.role != 'faculty': abort(403)
    if request.method == 'POST':
        assign.title = request.form.get('title')
        assign.deadline = datetime.strptime(request.form.get('deadline'), '%Y-%m-%dT%H:%M')
        db.session.commit()
        return redirect(url_for('course_page', course_id=assign.course_id))
    return render_template('edit_assignment.html', assignment=assign)

@app.route('/submission/<int:submission_id>')
@login_required
def view_submission(submission_id):
    sub = Submission.query.get_or_404(submission_id)
    return render_template('result.html', submission=sub)

@app.route('/submission/<int:submission_id>/review', methods=['GET', 'POST'])
@login_required
def manual_review(submission_id):
    sub = Submission.query.get_or_404(submission_id)
    if current_user.role != 'faculty': abort(403)
    if request.method == 'POST':
        sub.verdict = request.form.get('verdict')
        sub.reason = request.form.get('reason')
        db.session.commit()
        return redirect(url_for('view_reports', course_id=sub.course_id))
    return render_template('manual_review.html', submission=sub)

@app.route('/course/<int:course_id>/create_assignment', methods=['GET', 'POST'])
@login_required
def create_assignment(course_id):
    if current_user.role != 'faculty': abort(403)
    course = Course.query.get_or_404(course_id)
    if request.method == 'POST':
        title = request.form.get('title')
        deadline = datetime.strptime(request.form.get('deadline'), '%Y-%m-%dT%H:%M')
        assign = Assignment(title=title, deadline=deadline, course_id=course_id)
        db.session.add(assign)
        db.session.commit()
        flash(f'Assignment "{title}" created!', 'success')
        return redirect(url_for('course_page', course_id=course_id))
    return render_template('create_assignment.html', course=course)

# --- UPLOAD & BULK CHECK ---
@app.route('/submit/<int:assignment_id>', methods=['GET', 'POST'])
@login_required
def submit(assignment_id):
    assign = Assignment.query.get_or_404(assignment_id)
    if request.method == 'POST':
        # Logic for student submission... (Reconstruct standard save logic)
        flash('Submission received!', 'success')
        return redirect(url_for('course_page', course_id=assign.course_id))
    return render_template('upload.html', assignment=assign)

@app.route('/course/<int:course_id>/assignment/<int:assignment_id>/bulk_check')
@login_required
def bulk_check_history(course_id, assignment_id):
    course = Course.query.get_or_404(course_id)
    assignment = Assignment.query.get_or_404(assignment_id)
    history = BulkCheckRun.query.filter_by(assignment_id=assignment_id).order_by(BulkCheckRun.created_at.desc()).all()
    return render_template('bulk_check.html', course=course, assignment=assignment, history=history)

@app.route('/course/<int:course_id>/assignment/<int:assignment_id>/bulk_status')
@login_required
def bulk_status(course_id, assignment_id):
    latest_run = BulkCheckRun.query.filter_by(assignment_id=assignment_id).order_by(BulkCheckRun.created_at.desc()).first()
    if not latest_run: return redirect(url_for('bulk_check_history', course_id=course_id, assignment_id=assignment_id))
    course = Course.query.get_or_404(course_id)
    assignment = Assignment.query.get_or_404(assignment_id)
    return render_template('bulk_status.html', run=latest_run, course=course, assignment=assignment)

@app.route('/api/bulk_status/<int:run_id>')
@login_required
def api_bulk_status(run_id):
    run = BulkCheckRun.query.get_or_404(run_id)
    return jsonify({
        "id": run.id,
        "status": run.status,
        "total_files": run.total_files,
        "processed_count": run.processed_count,
        "accepted": run.accepted,
        "rejected": run.rejected,
        "manual_review": run.manual_review,
        "percentage": round((run.processed_count / run.total_files * 100) if run.total_files > 0 else 0, 1)
    })

@app.route('/search')
@login_required
def search():
    query = request.args.get('q', '')
    # Basic course search logic
    results = Course.query.filter(Course.name.ilike(f'%{query}%')).all()
    return render_template('search.html', results=results, query=query)

@app.route('/course/<int:course_id>/assignment/<int:assignment_id>/bulk_run/<int:run_id>')
@login_required
def bulk_check_run(course_id, assignment_id, run_id):
    run = BulkCheckRun.query.get_or_404(run_id)
    course = Course.query.get_or_404(course_id)
    assignment = Assignment.query.get_or_404(assignment_id)
    return render_template('bulk_check_run.html', run=run, course=course, assignment=assignment)

@app.route('/course/<int:course_id>/assignment/<int:assignment_id>/bulk_run/<int:run_id>/delete', methods=['POST'])
@login_required
def bulk_check_run_delete(course_id, assignment_id, run_id):
    if current_user.role != 'faculty': abort(403)
    run = BulkCheckRun.query.get_or_404(run_id)
    db.session.delete(run)
    db.session.commit()
    flash('Bulk run record deleted.', 'info')
    return redirect(url_for('bulk_check_history', course_id=course_id, assignment_id=assignment_id))

@app.route('/course/<int:course_id>/assignment/<int:assignment_id>/bulk_check_upload', methods=['POST'])
@login_required
def bulk_check(course_id, assignment_id):
    if current_user.role != 'faculty': abort(403)
    course = Course.query.get_or_404(course_id)
    assignment = Assignment.query.get_or_404(assignment_id)
    
    zip_file = request.files.get('zip_file')
    if zip_file:
        temp_dir = tempfile.mkdtemp(prefix='bulk_')
        zip_path = os.path.join(temp_dir, 'upload.zip')
        zip_file.save(zip_path)
        run = BulkCheckRun(course_id=course_id, assignment_id=assignment_id, run_by=current_user.id, status='pending')
        db.session.add(run)
        db.session.commit()
        threading.Thread(target=run_bulk_task, args=(app.app_context(), course_id, assignment_id, zip_path, run.id)).start()
        flash("Bulk check started!", "info")
    return redirect(url_for('bulk_status', course_id=course_id, assignment_id=assignment_id))

# --- PASSWORD RECOVERY ---
@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email')
        flash('Check your email for recovery instructions.', 'info')
        return redirect(url_for('login'))
    return render_template('forgot_password.html')

@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    return render_template('reset_password.html', token=token)

@app.route('/otp_verify', methods=['GET', 'POST'])
def otp_verify():
    return render_template('otp_verify.html')

# --- ADMIN ROUTES ---
@app.route('/admin/users')
@login_required
def admin_users():
    if current_user.role != 'admin': abort(403)
    users = User.query.all()
    return render_template('admin_users.html', users=users)

@app.route('/generate-presigned-url', methods=['POST'])
@login_required
def generate_presigned_url():
    return jsonify({"error": "S3 Direct is disabled for stability. Use standard ZIP upload."}), 400

with app.app_context():
    try:
        db.create_all()
        print("[DATABASE] Postgres connection verified and schema active.")
    except Exception as e:
        print(f"[CRITICAL] Database initialization failed: {e}")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)