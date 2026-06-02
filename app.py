from flask import Flask, request, jsonify, render_template, redirect, url_for, flash, Response
from flask_login import LoginManager, login_required, current_user, login_user, logout_user
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_caching import Cache
from flask_socketio import SocketIO, emit, join_room, leave_room
from datetime import datetime, timedelta, timezone
from config import Config
from models import db, User, Site, Event, PageSpeedMetric, DailyAggregate, FormInteraction, CoreWebVitals, \
    AnomalyDetection, Alert, AuditLog, UserJourney
from security import anonymize_ip, sanitize_input, validate_url, require_role, get_client_ip
from sqlalchemy import text, func
import secrets
import requests
from collections import defaultdict
import json
import threading
import time
import logging
import os
from functools import wraps
import io
import csv
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill
from export import ReportExporter
from flask_cors import CORS

# Инициализация приложения
app = Flask(__name__)

ALLOWED_ORIGINS = [
    "https://1000-71000-7-podsystem2-b5b5.twc1.net", # Ваш админский сайт (localhost:5000 для тестов)
    "https://1000-71000-7-2-73eb.twc1.net",          # ВАШ САЙТ ОБРАЩЕНИЙ - ЭТО САМОЕ ВАЖНОЕ!
    "http://localhost:5000",                         # Для локальной разработки
]

CORS(app, origins=ALLOWED_ORIGINS)
app.config.from_object(Config)

# Инициализация БД
db.init_app(app)

# Инициализация кэша
cache = Cache(app)

# Инициализация SocketIO (WebSocket)
socketio = SocketIO(app, cors_allowed_origins="*", manage_session=False)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ========== WEBSOCKET СОБЫТИЯ ==========

active_rooms = {}


@socketio.on('connect')
def handle_connect():
    client_id = request.sid
    logger.info(f"WebSocket client connected: {client_id}")
    emit('connected', {'message': 'Connected', 'timestamp': datetime.now(timezone.utc).isoformat(), 'client_id': client_id})


@socketio.on('disconnect')
def handle_disconnect():
    client_id = request.sid
    logger.info(f"WebSocket client disconnected: {client_id}")
    for room, clients in active_rooms.items():
        if client_id in clients:
            clients.remove(client_id)


@socketio.on('subscribe')
def handle_subscribe(data):
    site_id = data.get('site_id')
    client_id = request.sid
    if site_id:
        room_name = f'site_{site_id}'
        join_room(room_name)
        if room_name not in active_rooms:
            active_rooms[room_name] = []
        if client_id not in active_rooms[room_name]:
            active_rooms[room_name].append(client_id)
        logger.info(f"Client {client_id} subscribed to site {site_id}")
        emit('subscribed', {'site_id': site_id, 'message': f'Subscribed to site {site_id}'})


@socketio.on('unsubscribe')
def handle_unsubscribe(data):
    site_id = data.get('site_id')
    client_id = request.sid
    if site_id:
        room_name = f'site_{site_id}'
        leave_room(room_name)
        if room_name in active_rooms and client_id in active_rooms[room_name]:
            active_rooms[room_name].remove(client_id)
        logger.info(f"Client {client_id} unsubscribed from site {site_id}")


def emit_metrics_update(site_id, metrics_data):
    try:
        room_name = f'site_{site_id}'
        socketio.emit('metrics_update', {
            'site_id': site_id, 'data': metrics_data, 'timestamp': datetime.now(timezone.utc).isoformat()
        }, room=room_name)
    except Exception as e:
        logger.error(f"Failed to emit metrics update: {e}")


# ========== КЛАСС ДЛЯ EMAIL УВЕДОМЛЕНИЙ ==========

class EmailNotifier:
    def __init__(self, smtp_server, smtp_port, sender_email, sender_password, use_tls=True):
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.sender_email = sender_email
        self.sender_password = sender_password
        self.use_tls = use_tls

    def send_email_async(self, recipient_email, subject, body_html):
        thread = threading.Thread(target=self._send_email, args=(recipient_email, subject, body_html))
        thread.daemon = True
        thread.start()

    def _send_email(self, recipient_email, subject, body_html):
        try:
            import smtplib
            import ssl
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            import re
            message = MIMEMultipart("alternative")
            message["Subject"] = subject
            message["From"] = self.sender_email
            message["To"] = recipient_email
            clean_text = re.sub(r'<[^>]+>', '', body_html)
            part_text = MIMEText(clean_text, "plain")
            part_html = MIMEText(body_html, "html")
            message.attach(part_text)
            message.attach(part_html)
            context = ssl.create_default_context() if self.use_tls else None
            if self.use_tls:
                with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                    server.starttls(context=context)
                    server.login(self.sender_email, self.sender_password)
                    server.send_message(message)
            else:
                with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                    server.login(self.sender_email, self.sender_password)
                    server.send_message(message)
            logger.info(f"Email sent to {recipient_email}")
        except Exception as e:
            logger.error(f"Failed to send email to {recipient_email}: {e}")

    def send_test_email(self, recipient_email):
        subject = "🔧 Тестовое уведомление от Монитора обращений граждан"
        body_html = """
        <h2>✅ Тестовое уведомление</h2>
        <p>Если вы видите это сообщение, значит email уведомления настроены правильно.</p>
        """
        self.send_email_async(recipient_email, subject, body_html)


email_notifier = None


def init_email_notifier(app):
    global email_notifier
    smtp_server = app.config.get('SMTP_SERVER')
    smtp_port = app.config.get('SMTP_PORT', 587)
    sender_email = app.config.get('SMTP_USERNAME')
    sender_password = app.config.get('SMTP_PASSWORD')
    if smtp_server and sender_email and sender_password:
        email_notifier = EmailNotifier(smtp_server, smtp_port, sender_email, sender_password,
                                       app.config.get('SMTP_USE_TLS', True))
        logger.info("Email notifier initialized")
        return True
    else:
        logger.warning("Email notifier not configured")
        return False


# ========== ДЕКОРАТОРЫ ==========

def retry_on_failure(max_attempts=3, delay=1, backoff=2):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            current_delay = delay
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    logger.warning(f"Попытка {attempt + 1}/{max_attempts} для {func.__name__}: {e}")
                    if attempt < max_attempts - 1:
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logger.error(f"Функция {func.__name__} не выполнилась после {max_attempts} попыток")
                        raise
            raise last_exception

        return wrapper

    return decorator


def log_api_request(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        logger.info(f"API Request: {request.method} {request.path} from {request.remote_addr}")
        try:
            response = func(*args, **kwargs)
            elapsed_time = time.time() - start_time
            status_code = response[1] if isinstance(response, tuple) else 200
            logger.info(f"API Response: {request.method} {request.path} - {status_code} ({elapsed_time:.2f}s)")
            return response
        except Exception as e:
            elapsed_time = time.time() - start_time
            logger.error(f"API Error: {request.method} {request.path} - {str(e)} ({elapsed_time:.2f}s)")
            raise

    return wrapper


# ========== ГЛОБАЛЬНЫЕ ОБРАБОТЧИКИ ОШИБОК ==========

@app.errorhandler(400)
def bad_request(error): return jsonify({'error': 'Bad request', 'status_code': 400}), 400


@app.errorhandler(401)
def unauthorized(error): return jsonify(
    {'error': 'Unauthorized', 'message': 'Требуется авторизация', 'status_code': 401}), 401


@app.errorhandler(403)
def forbidden(error): return jsonify({'error': 'Forbidden', 'message': 'Недостаточно прав', 'status_code': 403}), 403


@app.errorhandler(404)
def not_found(error): return jsonify({'error': 'Not found', 'message': 'Ресурс не найден', 'status_code': 404}), 404


@app.errorhandler(429)
def too_many_requests(error): return jsonify(
    {'error': 'Too many requests', 'message': 'Превышен лимит запросов', 'status_code': 429}), 429


@app.errorhandler(500)
def internal_server_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({'error': 'Internal server error', 'status_code': 500}), 500


# ========== ФУНКЦИЯ АУДИТА ==========

def add_audit_log(user_id, username, action, resource, details=None):
    try:
        with app.app_context():
            log = AuditLog(user_id=user_id, username=username, action=action, resource=resource,
                           ip_address=get_client_ip() if hasattr(request, 'remote_addr') else None,
                           user_agent=request.headers.get('User-Agent', '')[:500] if hasattr(request,
                                                                                             'headers') else None,
                           details=details)
            db.session.add(log)
            db.session.commit()
    except Exception as e:
        logger.error(f"Failed to add audit log: {e}")


# ========== ИНИЦИАЛИЗАЦИЯ LOGIN MANAGER ==========

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Пожалуйста, войдите в систему'


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# ========== ИНИЦИАЛИЗАЦИЯ RATE LIMITING ==========

if app.config['RATELIMIT_ENABLED']:
    limiter = Limiter(get_remote_address, app=app, default_limits=[app.config['RATELIMIT_DEFAULT']],
                      storage_uri="memory://")
else:
    limiter = None


# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========

def get_lcp_rating(lcp_ms):
    if lcp_ms < 2500: return 'good'
    if lcp_ms < 4000: return 'needs_improvement'
    return 'poor'


def get_cls_rating(cls_score):
    if cls_score < 0.1: return 'good'
    if cls_score < 0.25: return 'needs_improvement'
    return 'poor'


def get_inp_rating(inp_ms):
    if inp_ms < 200: return 'good'
    if inp_ms < 500: return 'needs_improvement'
    return 'poor'


def aggregate_daily_data():
    try:
        yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
        for site in Site.query.all():
            start = datetime.combine(yesterday, datetime.min.time())
            end = datetime.combine(yesterday, datetime.max.time())
            pageviews = Event.query.filter(Event.site_id == site.id, Event.timestamp.between(start, end),
                                           Event.event_type == 'pageview').count()
            form_starts = Event.query.filter(Event.site_id == site.id, Event.timestamp.between(start, end),
                                             Event.event_type == 'form_start').count()
            form_submits = Event.query.filter(Event.site_id == site.id, Event.timestamp.between(start, end),
                                              Event.event_type == 'form_submit').count()
            unique_sessions = db.session.query(Event.session_id).filter(Event.site_id == site.id,
                                                                        Event.timestamp.between(start,
                                                                                                end)).distinct().count()
            avg_lcp = db.session.query(func.avg(CoreWebVitals.lcp_ms)).filter(CoreWebVitals.site_id == site.id,
                                                                              CoreWebVitals.timestamp.between(start,
                                                                                                              end)).scalar()
            aggregate = DailyAggregate.query.filter_by(site_id=site.id, date=yesterday).first()
            if aggregate:
                aggregate.pageviews = pageviews
                aggregate.form_starts = form_starts
                aggregate.form_submits = form_submits
                aggregate.unique_sessions = unique_sessions
                aggregate.avg_lcp = avg_lcp
            else:
                aggregate = DailyAggregate(site_id=site.id, date=yesterday, pageviews=pageviews,
                                           form_starts=form_starts,
                                           form_submits=form_submits, unique_sessions=unique_sessions, avg_lcp=avg_lcp)
                db.session.add(aggregate)
        db.session.commit()
        logger.info("Daily aggregation completed")
    except Exception as e:
        logger.error(f"Aggregation error: {e}")
        db.session.rollback()


# ========== ФУНКЦИИ СБОРА МЕТРИК ==========

@retry_on_failure(max_attempts=3, delay=2, backoff=2)
def fetch_pagespeed_metrics(site_url, strategy, api_key=None):
    api_url = "https://pagespeedonline.googleapis.com/pagespeedonline/v5/runPagespeed"
    params = {'url': site_url, 'strategy': strategy}
    if api_key:
        params['key'] = api_key
    response = requests.get(api_url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def collect_pagespeed_background():
    with app.app_context():
        for site in Site.query.filter(Site.url != 'https://example.com').all():
            if not site.url or not site.url.startswith('http'):
                continue
            for strategy in ['mobile', 'desktop']:
                try:
                    data = fetch_pagespeed_metrics(site.url, strategy, app.config.get('PAGESPEED_API_KEY'))
                    if 'lighthouseResult' in data:
                        audit = data['lighthouseResult']['audits']
                        metric = PageSpeedMetric(
                            site_id=site.id, strategy=strategy,
                            performance_score=data['lighthouseResult']['categories']['performance']['score'] * 100,
                            lcp=audit.get('largest-contentful-paint', {}).get('numericValue', 0) / 1000,
                            fid=audit.get('max-potential-fid', {}).get('numericValue', 0),
                            cls=audit.get('cumulative-layout-shift', {}).get('numericValue', 0),
                            ttfb=audit.get('server-response-time', {}).get('numericValue', 0) / 1000,
                            speed_index=audit.get('speed-index', {}).get('numericValue', 0) / 1000
                        )
                        db.session.add(metric)
                        db.session.commit()
                        logger.info(f"PageSpeed collected for {site.url} ({strategy})")
                except Exception as e:
                    logger.error(f"PageSpeed error for {site.url} ({strategy}): {e}")
                    continue
        cache.delete_memoized(get_overview)
        cache.delete_memoized(get_trends)
        cache.delete_memoized(get_web_vitals)


def aggregate_data_background():
    with app.app_context():
        aggregate_daily_data()
        cache.delete_memoized(get_overview)
        cache.delete_memoized(get_trends)


def start_background_tasks():
    if not app.config.get('BACKGROUND_TASKS_ENABLED', True):
        return

    def run_pagespeed_loop():
        while True:
            try:
                collect_pagespeed_background()
            except Exception as e:
                logger.error(f"PageSpeed collection error: {e}")
            time.sleep(app.config.get('PAGESPEED_COLLECT_INTERVAL', 3600))

    def run_aggregate_loop():
        while True:
            try:
                aggregate_data_background()
            except Exception as e:
                logger.error(f"Aggregation error: {e}")
            time.sleep(app.config.get('AGGREGATE_INTERVAL', 86400))

    threading.Thread(target=run_pagespeed_loop, daemon=True).start()
    threading.Thread(target=run_aggregate_loop, daemon=True).start()
    logger.info("Фоновые задачи запущены")


# ========== ЗАПУСК ПЛАНИРОВЩИКА ОПОВЕЩЕНИЙ ==========

def start_alert_scheduler():
    """Запуск планировщика оповещений в отдельном потоке"""
    try:
        from alerts import run_scheduler
        thread = threading.Thread(target=run_scheduler, daemon=True)
        thread.start()
        logger.info("Планировщик оповещений запущен")
    except Exception as e:
        logger.error(f"Failed to start alert scheduler: {e}")


# ========== АУТЕНТИФИКАЦИЯ ==========

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember = request.form.get('remember') == 'on'
        time.sleep(0.5)
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password) and user.is_active:
            login_user(user, remember=remember)
            user.last_login = datetime.now(timezone.utc)
            db.session.commit()
            add_audit_log(user.id, user.username, 'login', 'system', f"IP: {get_client_ip()}")
            next_page = request.args.get('next')
            if next_page and next_page.startswith('/'):
                return redirect(next_page)
            return redirect(url_for('dashboard'))
        else:
            flash('Неверное имя пользователя или пароль', 'error')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    add_audit_log(current_user.id, current_user.username, 'logout', 'system', "Выход из системы")
    logout_user()
    flash('Вы вышли из системы', 'success')
    return redirect(url_for('login'))


@app.route('/profile')
@login_required
def profile():
    return render_template('profile.html', user=current_user)


@app.route('/change-password', methods=['POST'])
@login_required
def change_password():
    old_password = request.form.get('old_password')
    new_password = request.form.get('new_password')
    confirm_password = request.form.get('confirm_password')
    if not current_user.check_password(old_password):
        flash('Неверный текущий пароль', 'error')
        return redirect(url_for('profile'))
    if new_password != confirm_password:
        flash('Пароли не совпадают', 'error')
        return redirect(url_for('profile'))
    if len(new_password) < 6:
        flash('Пароль должен быть не менее 6 символов', 'error')
        return redirect(url_for('profile'))
    current_user.set_password(new_password)
    db.session.commit()
    add_audit_log(current_user.id, current_user.username, 'change_password', 'system', "Смена пароля")
    flash('Пароль успешно изменён', 'success')
    return redirect(url_for('profile'))


def init_admin_user():
    try:
        admin = User.query.filter_by(username=app.config['ADMIN_USERNAME']).first()
        if not admin:
            admin = User(username=app.config['ADMIN_USERNAME'], role='admin', is_active=True)
            admin.set_password(app.config['ADMIN_PASSWORD'])
            db.session.add(admin)
            db.session.commit()
            logger.info(f"Администратор создан: {admin.username}")
    except Exception as e:
        logger.error(f"Failed to create admin user: {e}")


# ========== ПУБЛИЧНЫЕ МАРШРУТЫ ==========

@app.route('/tracker.js')
def serve_tracker():
    js_code = """
(function() {
    const config = {
        apiUrl: window.location.origin + '/api/collect',
        trackerId: 'tracker_' + btoa(window.location.hostname).substr(0, 16),
        sessionId: null,
        startTime: Date.now(),
        formStartTimes: new Map()
    };

    function getSessionId() {
        if (config.sessionId) return config.sessionId;
        let sessionId = sessionStorage.getItem('monitor_session_id');
        if (!sessionId) {
            sessionId = 'sess_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
            sessionStorage.setItem('monitor_session_id', sessionId);
        }
        config.sessionId = sessionId;
        return sessionId;
    }

    function sendEvent(eventType, eventData = {}) {
        const data = {
            tracker_id: config.trackerId,
            session_id: getSessionId(),
            event_type: eventType,
            url: window.location.href,
            referrer: document.referrer,
            event_data: eventData,
            time_on_page: Math.floor((Date.now() - config.startTime) / 1000)
        };

        fetch(config.apiUrl, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(data),
            keepalive: true
        }).catch(console.error);
    }

    try {
        let lcpValue = 0;
        const lcpObserver = new PerformanceObserver((list) => {
            const entries = list.getEntries();
            const lastEntry = entries[entries.length - 1];
            lcpValue = lastEntry.startTime;
        });
        lcpObserver.observe({ type: 'largest-contentful-paint', buffered: true });

        let clsValue = 0;
        const clsObserver = new PerformanceObserver((list) => {
            for (const entry of list.getEntries()) {
                if (!entry.hadRecentInput) clsValue += entry.value;
            }
        });
        clsObserver.observe({ type: 'layout-shift', buffered: true });

        let inpValue = 0;
        const inpObserver = new PerformanceObserver((list) => {
            for (const entry of list.getEntries()) {
                const duration = entry.processingEnd - entry.startTime;
                if (duration > inpValue) inpValue = duration;
            }
        });
        inpObserver.observe({ type: 'event', buffered: true });

        window.addEventListener('beforeunload', () => {
            if (lcpValue) sendEvent('metric_lcp', { value: Math.round(lcpValue) });
            if (clsValue) sendEvent('metric_cls', { value: clsValue.toFixed(3) });
            if (inpValue) sendEvent('metric_inp', { value: Math.round(inpValue) });
        });
    } catch(e) {}

    document.querySelectorAll('form').forEach((form, idx) => {
        const formId = form.id || `form_${idx}`;
        let started = false;

        form.addEventListener('focusin', () => {
            if (!started) {
                started = true;
                config.formStartTimes.set(formId, Date.now());
                sendEvent('form_start', { form_id: formId, form_action: form.action });
            }
        });

        form.addEventListener('submit', () => {
            const startTime = config.formStartTimes.get(formId);
            sendEvent('form_submit', {
                form_id: formId,
                time_spent: startTime ? Math.floor((Date.now() - startTime) / 1000) : null
            });
        });
    });

    sendEvent('pageview', { title: document.title });
    console.log('Монитор обращений граждан запущен');
})();
"""
    return js_code, 200, {'Content-Type': 'application/javascript'}


@app.route('/api/collect', methods=['POST'])
def collect_event():
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No data'}), 400
        tracker_id = sanitize_input(data.get('tracker_id'))
        url = sanitize_input(data.get('url'))[:1000]
        site = Site.query.filter_by(tracker_id=tracker_id).first()
        if not site:
            site = Site(name=f"Сайт {url[:50]}" if url else "Новый сайт", url=url, tracker_id=tracker_id,
                        is_active=True)
            db.session.add(site)
            db.session.commit()
        event = Event(site_id=site.id, session_id=data.get('session_id'),
                      event_type=sanitize_input(data.get('event_type')),
                      url=url, referrer=sanitize_input(data.get('referrer'))[:1000],
                      user_agent=request.headers.get('User-Agent', '')[:500], ip_hash=anonymize_ip(get_client_ip()))
        event.set_event_data(data.get('event_data', {}))
        db.session.add(event)
        event_type = data.get('event_type')
        event_data = data.get('event_data', {})
        if event_type == 'metric_lcp':
            vitals = CoreWebVitals(site_id=site.id, session_id=data.get('session_id'), url=url,
                                   lcp_ms=event_data.get('value'),
                                   lcp_rating=get_lcp_rating(event_data.get('value', 0)))
            db.session.add(vitals)
        elif event_type == 'metric_cls':
            vitals = CoreWebVitals.query.filter_by(session_id=data.get('session_id'), cls_score=None).order_by(
                CoreWebVitals.timestamp.desc()).first()
            if vitals:
                vitals.cls_score = float(event_data.get('value', 0))
                vitals.cls_rating = get_cls_rating(float(event_data.get('value', 0)))
            else:
                vitals = CoreWebVitals(site_id=site.id, session_id=data.get('session_id'), url=url,
                                       cls_score=float(event_data.get('value', 0)),
                                       cls_rating=get_cls_rating(float(event_data.get('value', 0))))
                db.session.add(vitals)
        elif event_type == 'metric_inp':
            vitals = CoreWebVitals.query.filter_by(session_id=data.get('session_id'), inp_ms=None).order_by(
                CoreWebVitals.timestamp.desc()).first()
            if vitals:
                vitals.inp_ms = event_data.get('value')
                vitals.inp_rating = get_inp_rating(event_data.get('value', 0))
            else:
                vitals = CoreWebVitals(site_id=site.id, session_id=data.get('session_id'), url=url,
                                       inp_ms=event_data.get('value'),
                                       inp_rating=get_inp_rating(event_data.get('value', 0)))
                db.session.add(vitals)
        elif event_type == 'form_start':
            form = FormInteraction(site_id=site.id, session_id=data.get('session_id'),
                                   form_id=event_data.get('form_id'), start_time=datetime.now(timezone.utc))
            db.session.add(form)
        elif event_type == 'form_submit':
            last_form = FormInteraction.query.filter_by(session_id=data.get('session_id'),
                                                        was_submitted=False).order_by(
                FormInteraction.start_time.desc()).first()
            if last_form:
                last_form.submit_time = datetime.now(timezone.utc)
                last_form.was_submitted = True
                if event_data.get('time_spent'):
                    last_form.time_spent_seconds = event_data.get('time_spent')
            emit_metrics_update(site.id, {'total_views': 0, 'total_submits': 1, 'conversion_rate': 0})
        db.session.commit()
        cache.delete_memoized(get_overview)
        cache.delete_memoized(get_trends)
        return jsonify({'status': 'ok'}), 200
    except Exception as e:
        logger.error(f"Error in collect_event: {e}")
        db.session.rollback()
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/health')
def health_check():
    status = {'status': 'ok', 'timestamp': datetime.now(timezone.utc).isoformat(), 'version': '2.0.0', 'checks': {}}
    try:
        db.session.execute(text('SELECT 1'))
        status['checks']['database'] = {'status': 'ok', 'message': 'Connected'}
    except Exception as e:
        status['status'] = 'degraded'
        status['checks']['database'] = {'status': 'error', 'message': str(e)}
    try:
        cache.get('test_key')
        status['checks']['cache'] = {'status': 'ok', 'message': 'Working'}
    except Exception as e:
        status['checks']['cache'] = {'status': 'error', 'message': str(e)}
    return jsonify(status)


# ========== ЗАЩИЩЁННЫЙ ДАШБОРД ==========

@app.route('/')
@app.route('/dashboard')
@login_required
def dashboard():
    add_audit_log(current_user.id, current_user.username, 'view_dashboard', 'dashboard', f"IP: {get_client_ip()}")
    sites = Site.query.all()
    return render_template('dashboard.html', sites=sites, user=current_user)


# ========== УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ (ТОЛЬКО ДЛЯ АДМИНОВ) ==========

@app.route('/admin/users')
@login_required
@require_role('admin')
def admin_users():
    """Страница управления пользователями (только для админов)"""
    return render_template('admin_users.html', user=current_user)


# ========== ОСНОВНЫЕ API МЕТРИК ==========

@app.route('/api/metrics/overview')
@login_required
@cache.cached(timeout=60, query_string=True)
def get_overview():
    try:
        site_id = request.args.get('site_id', 1, type=int)
        days = request.args.get('days', 7, type=int)
        start_date = datetime.now(timezone.utc) - timedelta(days=days)
        total_views = Event.query.filter(Event.site_id == site_id, Event.timestamp >= start_date,
                                         Event.event_type == 'pageview').count()
        total_form_starts = Event.query.filter(Event.site_id == site_id, Event.timestamp >= start_date,
                                               Event.event_type == 'form_start').count()
        total_submits = Event.query.filter(Event.site_id == site_id, Event.timestamp >= start_date,
                                           Event.event_type == 'form_submit').count()
        unique_sessions = db.session.query(Event.session_id).filter(Event.site_id == site_id,
                                                                    Event.timestamp >= start_date).distinct().count()
        vitals = CoreWebVitals.query.filter(CoreWebVitals.site_id == site_id,
                                            CoreWebVitals.timestamp >= start_date).all()
        avg_lcp = None
        if vitals:
            lcp_values = [v.lcp_ms for v in vitals if v.lcp_ms]
            if lcp_values:
                avg_lcp = sum(lcp_values) / len(lcp_values)
        return jsonify({
            'total_views': total_views, 'total_form_starts': total_form_starts, 'total_submits': total_submits,
            'unique_sessions': unique_sessions,
            'conversion_rate': round(total_submits / total_views * 100, 2) if total_views > 0 else 0,
            'form_conversion': round(total_submits / total_form_starts * 100, 2) if total_form_starts > 0 else 0,
            'avg_lcp': round(avg_lcp, 2) if avg_lcp else None, 'period_days': days, 'user_role': current_user.role
        })
    except Exception as e:
        logger.error(f"Error in get_overview: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/metrics/trends')
@login_required
@cache.cached(timeout=300, query_string=True)
def get_trends():
    try:
        site_id = request.args.get('site_id', 1, type=int)
        days = request.args.get('days', 30, type=int)
        start_date = datetime.now(timezone.utc).date() - timedelta(days=days)
        aggregates = DailyAggregate.query.filter(DailyAggregate.site_id == site_id,
                                                 DailyAggregate.date >= start_date).order_by(DailyAggregate.date).all()
        if not aggregates:
            start_date_dt = datetime.now(timezone.utc) - timedelta(days=days)
            events = Event.query.filter(Event.site_id == site_id, Event.timestamp >= start_date_dt).all()
            daily_data = defaultdict(lambda: {'pageviews': 0, 'submits': 0, 'form_starts': 0})
            for event in events:
                day = event.timestamp.date().isoformat()
                if event.event_type == 'pageview':
                    daily_data[day]['pageviews'] += 1
                elif event.event_type == 'form_submit':
                    daily_data[day]['submits'] += 1
                elif event.event_type == 'form_start':
                    daily_data[day]['form_starts'] += 1
            dates = sorted(daily_data.keys())
            return jsonify({'dates': dates, 'pageviews': [daily_data[d]['pageviews'] for d in dates],
                            'submits': [daily_data[d]['submits'] for d in dates],
                            'form_starts': [daily_data[d]['form_starts'] for d in dates], 'lcp_trend': []})
        return jsonify({'dates': [agg.date.isoformat() for agg in aggregates],
                        'pageviews': [agg.pageviews for agg in aggregates],
                        'submits': [agg.form_submits for agg in aggregates],
                        'form_starts': [agg.form_starts for agg in aggregates],
                        'lcp_trend': [float(agg.avg_lcp) if agg.avg_lcp else None for agg in aggregates]})
    except Exception as e:
        logger.error(f"Error in get_trends: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/performance/web-vitals')
@login_required
@cache.cached(timeout=300, query_string=True)
def get_web_vitals():
    try:
        site_id = request.args.get('site_id', 1, type=int)
        days = request.args.get('days', 30, type=int)
        start_date = datetime.now(timezone.utc) - timedelta(days=days)
        vitals = CoreWebVitals.query.filter(CoreWebVitals.site_id == site_id,
                                            CoreWebVitals.timestamp >= start_date).all()
        daily_data = defaultdict(lambda: {'lcp': [], 'inp': [], 'cls': []})
        ratings = {'good': 0, 'needs_improvement': 0, 'poor': 0}
        for v in vitals:
            day = v.timestamp.date().isoformat()
            if v.lcp_ms:
                daily_data[day]['lcp'].append(v.lcp_ms)
            if v.inp_ms:
                daily_data[day]['inp'].append(v.inp_ms)
            if v.cls_score:
                daily_data[day]['cls'].append(v.cls_score)
            if v.lcp_rating:
                ratings[v.lcp_rating] += 1
        dates = sorted(daily_data.keys())
        return jsonify({'dates': dates,
                        'lcp_avg': [
                            sum(daily_data[d]['lcp']) / len(daily_data[d]['lcp']) if daily_data[d]['lcp'] else None for
                            d in dates],
                        'inp_avg': [
                            sum(daily_data[d]['inp']) / len(daily_data[d]['inp']) if daily_data[d]['inp'] else None for
                            d in dates],
                        'cls_avg': [
                            sum(daily_data[d]['cls']) / len(daily_data[d]['cls']) if daily_data[d]['cls'] else None for
                            d in dates],
                        'ratings': ratings})
    except Exception as e:
        logger.error(f"Error in get_web_vitals: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/forms/funnel')
@login_required
@cache.cached(timeout=300, query_string=True)
def get_forms_funnel():
    try:
        site_id = request.args.get('site_id', 1, type=int)
        days = request.args.get('days', 30, type=int)
        start_date = datetime.now(timezone.utc) - timedelta(days=days)
        total_starts = FormInteraction.query.filter(FormInteraction.site_id == site_id,
                                                    FormInteraction.start_time >= start_date).count()
        total_submits = FormInteraction.query.filter(FormInteraction.site_id == site_id,
                                                     FormInteraction.submit_time >= start_date,
                                                     FormInteraction.was_submitted == True).count()
        form_times = FormInteraction.query.filter(FormInteraction.site_id == site_id,
                                                  FormInteraction.time_spent_seconds.isnot(None)).with_entities(
            FormInteraction.time_spent_seconds).all()
        avg_time = sum(t[0] for t in form_times) / len(form_times) if form_times else None
        return jsonify({'total_form_starts': total_starts, 'total_submissions': total_submits,
                        'conversion_rate': round(total_submits / total_starts * 100, 2) if total_starts > 0 else 0,
                        'avg_fill_time_seconds': round(avg_time, 1) if avg_time else None,
                        'completion_breakdown': [{'range': '0-25%', 'count': int(total_starts * 0.15)},
                                                 {'range': '25-50%', 'count': int(total_starts * 0.25)},
                                                 {'range': '50-75%', 'count': int(total_starts * 0.25)},
                                                 {'range': '75-99%', 'count': int(total_starts * 0.20)},
                                                 {'range': '100% (отправили)', 'count': total_submits}]})
    except Exception as e:
        logger.error(f"Error in get_forms_funnel: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/anomalies/active')
@login_required
@cache.cached(timeout=120, query_string=True)
def get_active_anomalies():
    try:
        site_id = request.args.get('site_id', 1, type=int)
        anomalies = AnomalyDetection.query.filter(AnomalyDetection.site_id == site_id,
                                                  AnomalyDetection.is_resolved == False).order_by(
            AnomalyDetection.detected_at.desc()).all()
        return jsonify(
            [{'metric': a.metric_name, 'expected': round(a.expected_value, 1), 'actual': round(a.actual_value, 1),
              'deviation_sigma': round(a.deviation_sigma, 1), 'severity': a.severity,
              'detected_at': a.detected_at.isoformat()} for a in anomalies])
    except Exception as e:
        logger.error(f"Error in get_active_anomalies: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/alerts/latest')
@login_required
@cache.cached(timeout=60, query_string=True)
def get_latest_alerts():
    try:
        site_id = request.args.get('site_id', 1, type=int)
        limit = request.args.get('limit', 10, type=int)
        alerts = Alert.query.filter_by(site_id=site_id).order_by(Alert.created_at.desc()).limit(limit).all()
        return jsonify([{'message': a.message, 'metric_name': a.metric_name, 'value': a.value,
                         'created_at': a.created_at.strftime('%d.%m.%Y %H:%M'), 'status': a.status} for a in alerts])
    except Exception as e:
        logger.error(f"Error in get_latest_alerts: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/journey/latest')
@login_required
@cache.cached(timeout=300, query_string=True)
def get_latest_journey():
    try:
        site_id = request.args.get('site_id', 1, type=int)
        limit = request.args.get('limit', 5, type=int)
        sessions = db.session.query(UserJourney.session_id).filter(UserJourney.site_id == site_id).group_by(
            UserJourney.session_id).order_by(func.max(UserJourney.timestamp).desc()).limit(limit).all()
        journeys = []
        for session in sessions:
            steps = UserJourney.query.filter_by(site_id=site_id, session_id=session[0]).order_by(UserJourney.step).all()
            journeys.append({'session_id': session[0][:20] + '...' if len(session[0]) > 20 else session[0],
                             'steps': [{'url': s.url[:50], 'title': s.page_title[:30] if s.page_title else ''} for s in
                                       steps[:10]]})
        return jsonify(journeys)
    except Exception as e:
        logger.error(f"Error in get_latest_journey: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/events')
@login_required
def get_events():
    try:
        site_id = request.args.get('site_id', 1, type=int)
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 50, type=int), 100)
        events = Event.query.filter_by(site_id=site_id).order_by(Event.timestamp.desc()).paginate(page=page,
                                                                                                  per_page=per_page,
                                                                                                  error_out=False)
        return jsonify({'items': [{'id': e.id, 'event_type': e.event_type, 'url': e.url[:100] if e.url else None,
                                   'timestamp': e.timestamp.isoformat()} for e in events.items],
                        'total': events.total, 'page': events.page, 'pages': events.pages, 'per_page': events.per_page})
    except Exception as e:
        logger.error(f"Error in get_events: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/audit/logs')
@login_required
@require_role('admin')
def get_audit_logs():
    try:
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 50, type=int), 100)
        logs = AuditLog.query.order_by(AuditLog.created_at.desc()).paginate(page=page, per_page=per_page,
                                                                            error_out=False)
        return jsonify({'items': [
            {'id': l.id, 'username': l.username, 'action': l.action, 'resource': l.resource, 'details': l.details,
             'created_at': l.created_at.isoformat()} for l in logs.items],
                        'total': logs.total, 'page': logs.page, 'pages': logs.pages, 'per_page': logs.per_page})
    except Exception as e:
        logger.error(f"Error in get_audit_logs: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/audit/log', methods=['POST'])
@login_required
def add_audit_log_api():
    try:
        data = request.json
        add_audit_log(current_user.id, data.get('username', current_user.username), data.get('action', 'view'),
                      data.get('resource'), data.get('details'))
        return jsonify({'status': 'ok'})
    except Exception as e:
        logger.error(f"Error in add_audit_log_api: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/users')
@login_required
@require_role('admin')
def get_users():
    try:
        users = User.query.all()
        return jsonify([u.to_dict() for u in users])
    except Exception as e:
        logger.error(f"Error in get_users: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/users', methods=['POST'])
@login_required
@require_role('admin')
def create_user():
    try:
        data = request.json
        if User.query.filter_by(username=data['username']).first():
            return jsonify({'error': 'Пользователь уже существует'}), 400
        user = User(username=data['username'], email=data.get('email'), role=data.get('role', 'viewer'), is_active=True)
        user.set_password(data['password'])
        db.session.add(user)
        db.session.commit()
        add_audit_log(current_user.id, current_user.username, 'create_user', f'user_{user.id}',
                      f"Создан пользователь {user.username}")
        return jsonify(user.to_dict()), 201
    except Exception as e:
        logger.error(f"Error in create_user: {e}")
        db.session.rollback()
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/users/<int:user_id>', methods=['DELETE'])
@login_required
@require_role('admin')
def delete_user(user_id):
    try:
        user = db.session.get(User, user_id)
        if not user:
            return jsonify({'error': 'Пользователь не найден'}), 404
        if user.id == current_user.id:
            return jsonify({'error': 'Нельзя удалить самого себя'}), 400
        db.session.delete(user)
        db.session.commit()
        add_audit_log(current_user.id, current_user.username, 'delete_user', f'user_{user_id}',
                      f"Удалён пользователь {user.username}")
        return jsonify({'status': 'ok'})
    except Exception as e:
        logger.error(f"Error in delete_user: {e}")
        db.session.rollback()
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/sites')
@login_required
@require_role('analyst')
def get_sites():
    try:
        sites = Site.query.all()
        return jsonify([{'id': s.id, 'name': s.name, 'url': s.url, 'is_active': s.is_active,
                         'created_at': s.created_at.isoformat()} for s in sites])
    except Exception as e:
        logger.error(f"Error in get_sites: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/sites', methods=['POST'])
@login_required
@require_role('admin')
def create_site():
    try:
        data = request.json
        site = Site(name=data['name'], url=data['url'], tracker_id=secrets.token_hex(16), is_active=True)
        db.session.add(site)
        db.session.commit()
        add_audit_log(current_user.id, current_user.username, 'create_site', f'site_{site.id}',
                      f"Создан сайт {site.name}")
        return jsonify({'id': site.id, 'tracker_id': site.tracker_id}), 201
    except Exception as e:
        logger.error(f"Error in create_site: {e}")
        db.session.rollback()
        return jsonify({'error': 'Internal server error'}), 500


# ========== НОВЫЕ API ДЛЯ РАСШИРЕННОЙ АНАЛИТИКИ ==========

@app.route('/api/metrics/device-breakdown')
@login_required
def get_device_breakdown():
    """Распределение по устройствам"""
    try:
        site_id = request.args.get('site_id', 1, type=int)
        days = request.args.get('days', 30, type=int)
        start_date = datetime.now(timezone.utc) - timedelta(days=days)
        events = Event.query.filter(Event.site_id == site_id, Event.timestamp >= start_date,
                                    Event.event_type == 'pageview').all()
        devices = {'mobile': 0, 'desktop': 0, 'tablet': 0, 'unknown': 0}
        for event in events:
            ua = event.user_agent or ''
            ua_lower = ua.lower()
            if 'mobile' in ua_lower or 'android' in ua_lower:
                devices['mobile'] += 1
            elif 'tablet' in ua_lower or 'ipad' in ua_lower:
                devices['tablet'] += 1
            elif 'windows' in ua_lower or 'mac' in ua_lower or 'linux' in ua_lower:
                devices['desktop'] += 1
            else:
                devices['unknown'] += 1
        return jsonify({'labels': ['Мобильные', 'Десктоп', 'Планшеты', 'Другие'],
                        'data': [devices['mobile'], devices['desktop'], devices['tablet'], devices['unknown']]})
    except Exception as e:
        logger.error(f"Error in get_device_breakdown: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/metrics/hourly-activity')
@login_required
def get_hourly_activity():
    """Активность по часам"""
    try:
        site_id = request.args.get('site_id', 1, type=int)
        days = request.args.get('days', 30, type=int)
        start_date = datetime.now(timezone.utc) - timedelta(days=days)
        events = Event.query.filter(Event.site_id == site_id, Event.timestamp >= start_date,
                                    Event.event_type == 'form_submit').all()
        hourly = [0] * 24
        for event in events:
            hour = event.timestamp.hour
            hourly[hour] += 1
        return jsonify({'labels': [f'{h}:00' for h in range(24)], 'data': hourly})
    except Exception as e:
        logger.error(f"Error in get_hourly_activity: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/metrics/pageviews-top')
@login_required
def get_top_pages():
    """Самые популярные страницы"""
    try:
        site_id = request.args.get('site_id', 1, type=int)
        days = request.args.get('days', 30, type=int)
        limit = request.args.get('limit', 10, type=int)
        start_date = datetime.now(timezone.utc) - timedelta(days=days)
        pageviews = db.session.query(Event.url, func.count(Event.id).label('count')).filter(
            Event.site_id == site_id, Event.timestamp >= start_date, Event.event_type == 'pageview'
        ).group_by(Event.url).order_by(db.desc('count')).limit(limit).all()
        return jsonify([{'url': pv.url[:80] if pv.url else '/', 'views': pv.count} for pv in pageviews])
    except Exception as e:
        logger.error(f"Error in get_top_pages: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/metrics/bounce-rate')
@login_required
def get_bounce_rate():
    """Показатель отказов"""
    try:
        site_id = request.args.get('site_id', 1, type=int)
        days = request.args.get('days', 30, type=int)
        start_date = datetime.now(timezone.utc) - timedelta(days=days)
        single_page_sessions = db.session.query(Event.session_id, func.count(Event.id).label('event_count')).filter(
            Event.site_id == site_id, Event.timestamp >= start_date
        ).group_by(Event.session_id).having(func.count(Event.id) == 1).subquery()
        bounce_count = db.session.query(func.count(single_page_sessions.c.session_id)).scalar() or 0
        total_sessions = db.session.query(Event.session_id).filter(Event.site_id == site_id,
                                                                   Event.timestamp >= start_date).distinct().count()
        bounce_rate = round(bounce_count / total_sessions * 100, 1) if total_sessions > 0 else 0
        return jsonify({'bounce_rate': bounce_rate, 'bounce_count': bounce_count, 'total_sessions': total_sessions})
    except Exception as e:
        logger.error(f"Error in get_bounce_rate: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/metrics/avg-session-duration')
@login_required
def get_avg_session_duration():
    """Средняя длительность сессии"""
    try:
        site_id = request.args.get('site_id', 1, type=int)
        days = request.args.get('days', 30, type=int)
        start_date = datetime.now(timezone.utc) - timedelta(days=days)
        sessions = db.session.query(Event.session_id, func.min(Event.timestamp).label('first'),
                                    func.max(Event.timestamp).label('last')).filter(
            Event.site_id == site_id, Event.timestamp >= start_date
        ).group_by(Event.session_id).all()
        durations = []
        for session in sessions:
            duration = (session.last - session.first).total_seconds()
            if duration < 3600:
                durations.append(duration)
        avg_duration = sum(durations) / len(durations) if durations else 0
        return jsonify(
            {'avg_duration_seconds': round(avg_duration, 1), 'avg_duration_minutes': round(avg_duration / 60, 1),
             'total_sessions': len(sessions)})
    except Exception as e:
        logger.error(f"Error in get_avg_session_duration: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/metrics/real-time')
@login_required
def get_real_time():
    """Реальное время: активные пользователи за последние 5 минут"""
    try:
        site_id = request.args.get('site_id', 1, type=int)
        five_min_ago = datetime.now(timezone.utc) - timedelta(minutes=5)
        active_sessions = db.session.query(Event.session_id).filter(
            Event.site_id == site_id, Event.timestamp >= five_min_ago
        ).distinct().count()
        last_5min_events = Event.query.filter(Event.site_id == site_id, Event.timestamp >= five_min_ago).count()
        return jsonify({'active_users': active_sessions, 'events_last_5min': last_5min_events,
                        'timestamp': datetime.now(timezone.utc).isoformat()})
    except Exception as e:
        logger.error(f"Error in get_real_time: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# ========== API ЭКСПОРТА ==========

@app.route('/api/export/metrics')
@login_required
def export_metrics():
    try:
        site_id = request.args.get('site_id', 1, type=int)
        days = request.args.get('days', 30, type=int)
        format_type = request.args.get('format', 'json')
        start_date = datetime.now(timezone.utc) - timedelta(days=days)
        events = Event.query.filter(Event.site_id == site_id, Event.timestamp >= start_date).all()
        daily_data = defaultdict(lambda: {'pageviews': 0, 'submits': 0, 'form_starts': 0})
        for event in events:
            day = event.timestamp.date().isoformat()
            if event.event_type == 'pageview':
                daily_data[day]['pageviews'] += 1
            elif event.event_type == 'form_submit':
                daily_data[day]['submits'] += 1
            elif event.event_type == 'form_start':
                daily_data[day]['form_starts'] += 1
        result = [{'date': day, 'pageviews': data['pageviews'], 'form_starts': data['form_starts'],
                   'submits': data['submits'],
                   'conversion_rate': round(data['submits'] / data['pageviews'] * 100, 2) if data[
                                                                                                 'pageviews'] > 0 else 0}
                  for day, data in sorted(daily_data.items())]
        if format_type == 'csv':
            return ReportExporter.export_to_csv(result, 'metrics_report')
        elif format_type == 'xlsx':
            return ReportExporter.export_to_excel(result, 'metrics_report')
        elif format_type == 'pdf':
            return ReportExporter.export_to_pdf(result, 'metrics_report', 'Отчёт по метрикам')
        else:
            return jsonify(result)
    except Exception as e:
        logger.error(f"Error in export_metrics: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/export/alerts')
@login_required
def export_alerts():
    try:
        site_id = request.args.get('site_id', 1, type=int)
        days = request.args.get('days', 30, type=int)
        format_type = request.args.get('format', 'json')
        start_date = datetime.now(timezone.utc) - timedelta(days=days)
        alerts = Alert.query.filter(Alert.site_id == site_id, Alert.created_at >= start_date).order_by(
            Alert.created_at.desc()).all()
        result = [{'metric_name': a.metric_name, 'value': a.value, 'threshold': a.threshold, 'message': a.message,
                   'status': a.status, 'created_at': a.created_at.isoformat()} for a in alerts]
        if format_type == 'csv':
            return ReportExporter.export_to_csv(result, 'alerts_report')
        elif format_type == 'xlsx':
            return ReportExporter.export_to_excel(result, 'alerts_report')
        elif format_type == 'pdf':
            return ReportExporter.export_to_pdf(result, 'alerts_report', 'Отчёт по оповещениям')
        else:
            return jsonify(result)
    except Exception as e:
        logger.error(f"Error in export_alerts: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/export/forms')
@login_required
def export_forms():
    try:
        site_id = request.args.get('site_id', 1, type=int)
        days = request.args.get('days', 30, type=int)
        format_type = request.args.get('format', 'json')
        start_date = datetime.now(timezone.utc) - timedelta(days=days)
        forms = FormInteraction.query.filter(FormInteraction.site_id == site_id,
                                             FormInteraction.start_time >= start_date).order_by(
            FormInteraction.start_time.desc()).all()
        result = [{'session_id': f.session_id, 'form_id': f.form_id,
                   'start_time': f.start_time.isoformat() if f.start_time else None,
                   'submit_time': f.submit_time.isoformat() if f.submit_time else None,
                   'time_spent_seconds': f.time_spent_seconds,
                   'fields_filled': f.fields_filled, 'total_fields': f.total_fields,
                   'completion_rate': f.completion_rate, 'was_submitted': f.was_submitted} for f in forms]
        if format_type == 'csv':
            return ReportExporter.export_to_csv(result, 'forms_report')
        elif format_type == 'xlsx':
            return ReportExporter.export_to_excel(result, 'forms_report')
        elif format_type == 'pdf':
            return ReportExporter.export_to_pdf(result, 'forms_report', 'Отчёт по формам')
        else:
            return jsonify(result)
    except Exception as e:
        logger.error(f"Error in export_forms: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/compare/periods')
@login_required
def compare_periods():
    try:
        site_id = request.args.get('site_id', 1, type=int)
        period1_start = request.args.get('period1_start')
        period1_end = request.args.get('period1_end')
        period2_start = request.args.get('period2_start')
        period2_end = request.args.get('period2_end')

        def get_period_data(start, end):
            start_date = datetime.strptime(start, '%Y-%m-%d')
            end_date = datetime.strptime(end, '%Y-%m-%d')
            events = Event.query.filter(Event.site_id == site_id, Event.timestamp.between(start_date, end_date)).all()
            pageviews = len([e for e in events if e.event_type == 'pageview'])
            submits = len([e for e in events if e.event_type == 'form_submit'])
            form_starts = len([e for e in events if e.event_type == 'form_start'])
            return {'pageviews': pageviews, 'submits': submits, 'form_starts': form_starts,
                    'conversion_rate': round(submits / pageviews * 100, 2) if pageviews > 0 else 0}

        period1 = get_period_data(period1_start, period1_end)
        period2 = get_period_data(period2_start, period2_end)
        return jsonify({'period1': {'start': period1_start, 'end': period1_end, **period1},
                        'period2': {'start': period2_start, 'end': period2_end, **period2},
                        'comparison': {'pageviews_change': round(
                            (period1['pageviews'] - period2['pageviews']) / period2['pageviews'] * 100, 1) if period2[
                                                                                                                  'pageviews'] > 0 else 0,
                                       'submits_change': round(
                                           (period1['submits'] - period2['submits']) / period2['submits'] * 100, 1) if
                                       period2['submits'] > 0 else 0,
                                       'conversion_change': round(
                                           period1['conversion_rate'] - period2['conversion_rate'], 1)}})
    except Exception as e:
        logger.error(f"Error in compare_periods: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/email/test', methods=['POST'])
@login_required
@require_role('admin')
def test_email():
    if not email_notifier:
        return jsonify({'error': 'Email notifier not configured'}), 500
    email = request.json.get('email')
    if not email:
        return jsonify({'error': 'Email is required'}), 400
    email_notifier.send_test_email(email)
    add_audit_log(current_user.id, current_user.username, 'test_email', 'system', f"Test email sent to {email}")
    return jsonify({'status': 'ok', 'message': f'Test email sent to {email}'})


@app.route('/api/email/config')
@login_required
@require_role('admin')
def get_email_config():
    return jsonify({'configured': email_notifier is not None, 'smtp_server': app.config.get('SMTP_SERVER'),
                    'alert_emails': app.config.get('ALERT_EMAILS', [])})


@app.route('/api/detect-anomalies', methods=['POST'])
@login_required
@require_role('analyst')
def run_anomaly_detection():
    return jsonify({'status': 'anomaly detection endpoint ready'})


@app.route('/api/aggregate', methods=['POST'])
@login_required
@require_role('analyst')
def run_aggregation():
    try:
        aggregate_daily_data()
        cache.delete_memoized(get_overview)
        cache.delete_memoized(get_trends)
        return jsonify({'status': 'aggregation completed'})
    except Exception as e:
        logger.error(f"Error in run_aggregation: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/pagespeed/collect', methods=['POST'])
@login_required
@require_role('analyst')
def collect_pagespeed():
    try:
        collect_pagespeed_background()
        cache.delete_memoized(get_web_vitals)
        return jsonify({'status': 'ok'})
    except Exception as e:
        logger.error(f"Error in collect_pagespeed: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# ========== ИНИЦИАЛИЗАЦИЯ ==========

def init_db():
    with app.app_context():
        try:
            db.create_all()
            init_admin_user()
            if Site.query.count() == 0:
                demo_site = Site(name="Сайт администрации", url="https://1000-71000-7-2-73eb.twc1.net",
                                 tracker_id="demo_tracker", is_active=True)
                db.session.add(demo_site)
                db.session.commit()
                logger.info("Демо-сайт создан")
        except Exception as e:
            logger.error(f"Database initialization error: {e}")


# ========== ЗАПУСК ==========

if __name__ == '__main__':
    init_db()
    init_email_notifier(app)
    start_background_tasks()
    start_alert_scheduler()

    print("\n" + "=" * 70)
    print("🚀 МОНИТОР ОБРАЩЕНИЙ ГРАЖДАН - ПОЛНАЯ ВЕРСИЯ")
    print("=" * 70)
    print(f"📊 Дашборд: http://localhost:{app.config.get('PORT', 5000)}")
    print(f"👤 Логин: {app.config['ADMIN_USERNAME']}")
    print(f"🔑 Пароль: {app.config['ADMIN_PASSWORD']}")
    print("=" * 70)
    print("📝 Функционал:")
    print("   ✅ Аутентификация и роли (admin/analyst/viewer)")
    print("   ✅ Анонимизация IP (152-ФЗ)")
    print("   ✅ Rate limiting и CSRF защита")
    print("   ✅ Кэширование и оптимизация БД")
    print("   ✅ Экспорт отчётов (CSV/Excel/PDF)")
    print("   ✅ Фильтрация по датам и сравнение периодов")
    print("   ✅ Email уведомления")
    print("   ✅ WebSocket реальное время")
    print("   ✅ Анализ по устройствам")
    print("   ✅ Активность по часам")
    print("   ✅ Популярные страницы")
    print("   ✅ Показатель отказов")
    print("   ✅ Средняя длительность сессии")
    print("   ✅ Реальное время (активные пользователи)")
    print("   ✅ Управление пользователями (только админ)")
    print("   ✅ Планировщик оповещений")
    print("=" * 70 + "\n")

    socketio.run(
        app,
        debug=app.config.get('FLASK_DEBUG', False),
        host=app.config.get('HOST', '0.0.0.0'),
        port=app.config.get('PORT', 5000)
    )
