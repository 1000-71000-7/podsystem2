from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import json

db = SQLAlchemy()


# ========== МОДЕЛЬ ПОЛЬЗОВАТЕЛЯ ==========
class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False, index=True)
    email = db.Column(db.String(200), unique=True, nullable=True)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(50), default='viewer')  # admin, analyst, viewer
    is_active = db.Column(db.Boolean, default=True)
    last_login = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    audit_logs = db.relationship('AuditLog', backref='user', lazy='dynamic')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_admin(self):
        return self.role == 'admin'

    def can_edit(self):
        return self.role in ['admin', 'analyst']

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'role': self.role,
            'is_active': self.is_active,
            'last_login': self.last_login.isoformat() if self.last_login else None
        }


# ========== МОДЕЛЬ САЙТА ==========
class Site(db.Model):
    __tablename__ = 'sites'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    url = db.Column(db.String(500), nullable=False)
    tracker_id = db.Column(db.String(100), unique=True, index=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    events = db.relationship('Event', backref='site', lazy='dynamic')
    metrics = db.relationship('PageSpeedMetric', backref='site', lazy='dynamic')

    __table_args__ = (
        db.Index('idx_site_tracker', 'tracker_id'),
    )


# ========== МОДЕЛЬ СОБЫТИЙ (С ИНДЕКСАМИ) ==========
class Event(db.Model):
    __tablename__ = 'events'

    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey('sites.id'), nullable=False, index=True)
    session_id = db.Column(db.String(100), nullable=False, index=True)
    event_type = db.Column(db.String(50), nullable=False, index=True)
    url = db.Column(db.String(1000))
    referrer = db.Column(db.String(1000))
    user_agent = db.Column(db.String(500))
    ip_hash = db.Column(db.String(64))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    event_data = db.Column(db.Text)

    # Составной индекс для частых запросов
    __table_args__ = (
        db.Index('idx_event_site_time_type', 'site_id', 'timestamp', 'event_type'),
        db.Index('idx_event_session', 'session_id', 'event_type'),
        db.Index('idx_event_timestamp', 'timestamp'),
    )

    def get_event_data(self):
        return json.loads(self.event_data) if self.event_data else {}

    def set_event_data(self, data):
        self.event_data = json.dumps(data) if data else None


# ========== МОДЕЛЬ МЕТРИК PAGESPEED ==========
class PageSpeedMetric(db.Model):
    __tablename__ = 'pagespeed_metrics'

    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey('sites.id'), nullable=False, index=True)
    strategy = db.Column(db.String(20), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    lcp = db.Column(db.Float)
    fid = db.Column(db.Float)
    cls = db.Column(db.Float)
    ttfb = db.Column(db.Float)
    speed_index = db.Column(db.Float)
    performance_score = db.Column(db.Integer)

    __table_args__ = (
        db.Index('idx_pagespeed_site_time', 'site_id', 'timestamp'),
        db.Index('idx_pagespeed_strategy', 'strategy'),
    )


# ========== МОДЕЛЬ АГРЕГИРОВАННЫХ ДАННЫХ ==========
class DailyAggregate(db.Model):
    __tablename__ = 'daily_aggregates'

    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey('sites.id'), nullable=False, index=True)
    date = db.Column(db.Date, nullable=False, index=True)
    pageviews = db.Column(db.Integer, default=0)
    form_starts = db.Column(db.Integer, default=0)
    form_submits = db.Column(db.Integer, default=0)
    form_errors = db.Column(db.Integer, default=0)
    unique_sessions = db.Column(db.Integer, default=0)
    avg_lcp = db.Column(db.Float)
    avg_ttfb = db.Column(db.Float)

    __table_args__ = (
        db.Index('idx_aggregate_site_date', 'site_id', 'date'),
        db.UniqueConstraint('site_id', 'date', name='uq_aggregate_site_date'),
    )


# ========== МОДЕЛЬ ВЗАИМОДЕЙСТВИЙ С ФОРМАМИ ==========
class FormInteraction(db.Model):
    __tablename__ = 'form_interactions'

    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey('sites.id'), nullable=False, index=True)
    session_id = db.Column(db.String(100), nullable=False, index=True)
    form_id = db.Column(db.String(200))
    form_name = db.Column(db.String(200))
    start_time = db.Column(db.DateTime, index=True)
    submit_time = db.Column(db.DateTime)
    time_spent_seconds = db.Column(db.Integer)
    total_fields = db.Column(db.Integer, default=0)
    fields_filled = db.Column(db.Integer, default=0)
    completion_rate = db.Column(db.Integer)
    was_submitted = db.Column(db.Boolean, default=False, index=True)
    had_validation_errors = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.Index('idx_form_site_time', 'site_id', 'start_time'),
        db.Index('idx_form_session', 'session_id'),
    )


# ========== МОДЕЛЬ CORE WEB VITALS (С ИНДЕКСАМИ) ==========
class CoreWebVitals(db.Model):
    __tablename__ = 'core_web_vitals'

    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey('sites.id'), nullable=False, index=True)
    session_id = db.Column(db.String(100), index=True)
    url = db.Column(db.String(1000))
    lcp_ms = db.Column(db.Integer)
    inp_ms = db.Column(db.Integer)
    cls_score = db.Column(db.Float)
    lcp_rating = db.Column(db.String(20))
    inp_rating = db.Column(db.String(20))
    cls_rating = db.Column(db.String(20))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        db.Index('idx_vitals_site_time', 'site_id', 'timestamp'),
        db.Index('idx_vitals_rating', 'lcp_rating'),
    )


# ========== МОДЕЛЬ АНОМАЛИЙ ==========
class AnomalyDetection(db.Model):
    __tablename__ = 'anomalies'

    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey('sites.id'), nullable=False, index=True)
    metric_name = db.Column(db.String(100), index=True)
    expected_value = db.Column(db.Float)
    actual_value = db.Column(db.Float)
    deviation_sigma = db.Column(db.Float)
    severity = db.Column(db.String(20), index=True)
    is_resolved = db.Column(db.Boolean, default=False, index=True)
    detected_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    resolved_at = db.Column(db.DateTime)

    __table_args__ = (
        db.Index('idx_anomaly_site_resolved', 'site_id', 'is_resolved'),
    )


# ========== МОДЕЛЬ ОПОВЕЩЕНИЙ ==========
class Alert(db.Model):
    __tablename__ = 'alerts'

    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey('sites.id'), nullable=False, index=True)
    metric_name = db.Column(db.String(100), nullable=False)
    value = db.Column(db.Float)
    threshold = db.Column(db.Float)
    message = db.Column(db.Text)
    status = db.Column(db.String(20), default='sent', index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        db.Index('idx_alert_site_status', 'site_id', 'status'),
    )


# ========== МОДЕЛЬ АУДИТА ==========
class AuditLog(db.Model):
    __tablename__ = 'audit_logs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    username = db.Column(db.String(100), index=True)
    action = db.Column(db.String(200), nullable=False, index=True)
    resource = db.Column(db.String(200))
    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.String(500))
    details = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        db.Index('idx_audit_user_time', 'user_id', 'created_at'),
        db.Index('idx_audit_action', 'action'),
    )


# ========== МОДЕЛЬ ПУТЕЙ ПОЛЬЗОВАТЕЛЯ ==========
class UserJourney(db.Model):
    __tablename__ = 'user_journeys'

    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey('sites.id'), nullable=False, index=True)
    session_id = db.Column(db.String(100), nullable=False, index=True)
    page_view_id = db.Column(db.String(50))
    step = db.Column(db.Integer)
    url = db.Column(db.String(1000))
    page_title = db.Column(db.String(500))
    referrer = db.Column(db.String(1000))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        db.Index('idx_journey_session', 'session_id', 'timestamp'),
        db.Index('idx_journey_site_time', 'site_id', 'timestamp'),
    )