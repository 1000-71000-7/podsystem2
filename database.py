from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import json

db = SQLAlchemy()


class Site(db.Model):
    """Таблица сайтов"""
    __tablename__ = 'sites'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    url = db.Column(db.String(500), nullable=False)
    tracker_id = db.Column(db.String(100), unique=True)  # ID для трекера
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Связи
    events = db.relationship('Event', backref='site', lazy='dynamic')
    metrics = db.relationship('PageSpeedMetric', backref='site', lazy='dynamic')


class Event(db.Model):
    """События с сайта (просмотры, отправки форм и т.д.)"""
    __tablename__ = 'events'

    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey('sites.id'), nullable=False)
    session_id = db.Column(db.String(100), nullable=False)
    event_type = db.Column(db.String(50), nullable=False)  # 'pageview', 'form_submit', 'form_error'
    url = db.Column(db.String(1000))
    referrer = db.Column(db.String(1000))
    user_agent = db.Column(db.String(500))
    ip_address = db.Column(db.String(45))  # IPv6 поддерживает 45 символов
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    # Дополнительные данные в JSON
    event_data = db.Column(db.Text)  # Храним JSON строку

    def get_event_data(self):
        return json.loads(self.event_data) if self.event_data else {}

    def set_event_data(self, data):
        self.event_data = json.dumps(data) if data else None


class PageSpeedMetric(db.Model):
    """Метрики производительности от PageSpeed Insights"""
    __tablename__ = 'pagespeed_metrics'

    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey('sites.id'), nullable=False)
    strategy = db.Column(db.String(20), nullable=False)  # 'mobile' или 'desktop'
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    # Core Web Vitals
    lcp = db.Column(db.Float)  # Largest Contentful Paint (секунды)
    fid = db.Column(db.Float)  # First Input Delay
    cls = db.Column(db.Float)  # Cumulative Layout Shift
    ttfb = db.Column(db.Float)  # Time To First Byte
    speed_index = db.Column(db.Float)

    # Общая оценка
    performance_score = db.Column(db.Integer)  # 0-100


class DailyAggregate(db.Model):
    """Агрегированные данные по дням (для быстрых графиков)"""
    __tablename__ = 'daily_aggregates'

    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey('sites.id'), nullable=False)
    date = db.Column(db.Date, nullable=False, index=True)

    # Статистика
    pageviews = db.Column(db.Integer, default=0)
    form_submits = db.Column(db.Integer, default=0)
    form_errors = db.Column(db.Integer, default=0)
    unique_sessions = db.Column(db.Integer, default=0)

    # Производительность (средние значения за день)
    avg_lcp = db.Column(db.Float)
    avg_ttfb = db.Column(db.Float)


class Alert(db.Model):
    """Лог оповещений"""
    __tablename__ = 'alerts'

    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey('sites.id'), nullable=False)
    metric_name = db.Column(db.String(100), nullable=False)
    value = db.Column(db.Float)
    threshold = db.Column(db.Float)
    message = db.Column(db.Text)
    status = db.Column(db.String(20), default='sent')  # 'sent', 'acknowledged'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class UserJourney(db.Model):
    """Путь пользователя по сайту"""
    __tablename__ = 'user_journeys'

    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey('sites.id'), nullable=False)
    session_id = db.Column(db.String(100), nullable=False)
    page_view_id = db.Column(db.String(50))
    step = db.Column(db.Integer)
    url = db.Column(db.String(1000))
    page_title = db.Column(db.String(500))
    referrer = db.Column(db.String(1000))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    # Время до следующего шага
    time_to_next_seconds = db.Column(db.Integer)


class FormInteraction(db.Model):
    """Детальная статистика по формам"""
    __tablename__ = 'form_interactions'

    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey('sites.id'), nullable=False)
    session_id = db.Column(db.String(100), nullable=False)
    form_id = db.Column(db.String(200))
    form_name = db.Column(db.String(200))

    # Временные метки
    start_time = db.Column(db.DateTime)
    submit_time = db.Column(db.DateTime)
    time_spent_seconds = db.Column(db.Integer)

    # Заполнение
    total_fields = db.Column(db.Integer)
    fields_filled = db.Column(db.Integer)
    completion_rate = db.Column(db.Integer)  # 0-100

    # Результат
    was_submitted = db.Column(db.Boolean, default=False)
    had_validation_errors = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class CoreWebVitals(db.Model):
    """Собранные метрики производительности"""
    __tablename__ = 'core_web_vitals'

    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey('sites.id'), nullable=False)
    session_id = db.Column(db.String(100))
    url = db.Column(db.String(1000))

    lcp_ms = db.Column(db.Integer)  # Largest Contentful Paint
    inp_ms = db.Column(db.Integer)  # Interaction to Next Paint
    cls_score = db.Column(db.Float)  # Cumulative Layout Shift

    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    # Оценка
    lcp_rating = db.Column(db.String(20))  # good, needs_improvement, poor
    inp_rating = db.Column(db.String(20))
    cls_rating = db.Column(db.String(20))


class AnomalyDetection(db.Model):
    """Обнаруженные аномалии"""
    __tablename__ = 'anomalies'

    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey('sites.id'), nullable=False)
    metric_name = db.Column(db.String(100))
    expected_value = db.Column(db.Float)
    actual_value = db.Column(db.Float)
    deviation_sigma = db.Column(db.Float)  # сколько сигм
    severity = db.Column(db.String(20))  # low, medium, high
    is_resolved = db.Column(db.Boolean, default=False)
    detected_at = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_at = db.Column(db.DateTime)


class AuditLog(db.Model):
    """Аудит действий администраторов"""
    __tablename__ = 'audit_logs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer)
    username = db.Column(db.String(100))
    action = db.Column(db.String(200))  # 'view_dashboard', 'export_report', 'change_settings'
    resource = db.Column(db.String(200))  # 'site:1', 'alert:5'
    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.String(500))
    details = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)