"""
models.py — Standalone Bulk Plagiarism Detection
==================================================
No users, no courses, no assignments. Just scan runs and results.
"""
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class BulkCheckRun(db.Model):
    """One bulk plagiarism scan session."""
    __tablename__ = 'bulk_check_run'

    id              = db.Column(db.Integer,    primary_key=True)
    title           = db.Column(db.String(200), nullable=True, default='Untitled Scan')
    total_files     = db.Column(db.Integer,    default=0)
    processed_count = db.Column(db.Integer,    default=0)
    status          = db.Column(db.String(20), default='pending')  # pending|processing|completed|error
    threshold       = db.Column(db.Integer,    default=40)         # similarity threshold %

    accepted        = db.Column(db.Integer, default=0)
    rejected        = db.Column(db.Integer, default=0)
    manual_review   = db.Column(db.Integer, default=0)
    elapsed_sec     = db.Column(db.Float,   nullable=True)

    created_at      = db.Column(db.DateTime, default=datetime.utcnow)

    results = db.relationship(
        'BulkCheckResult', backref='run', lazy=True,
        cascade='all, delete-orphan',
        order_by='BulkCheckResult.id',
    )


class BulkCheckResult(db.Model):
    """One file's result inside a BulkCheckRun."""
    __tablename__ = 'bulk_check_result'

    id              = db.Column(db.Integer,    primary_key=True)
    run_id          = db.Column(db.Integer,    db.ForeignKey('bulk_check_run.id'), nullable=False)

    filename        = db.Column(db.String(255), nullable=True)
    verdict         = db.Column(db.String(20),  nullable=True)   # accepted|rejected|manual_review|error
    reason          = db.Column(db.String(255), nullable=True)
    peer_score      = db.Column(db.Float,  default=0.0)
    external_score  = db.Column(db.Float,  default=0.0)
    ocr_confidence  = db.Column(db.Float,  default=0.0)
    is_digital      = db.Column(db.Boolean, default=True)        # True = digital text, False = OCR was used
    analysis_text   = db.Column(db.Text,   nullable=True)
    peer_details    = db.Column(db.Text,   nullable=True)        # JSON
    sentence_map    = db.Column(db.Text,   nullable=True)        # JSON heatmap data