import os
import secrets
from dotenv import load_dotenv

# Загружаем переменные из .env
load_dotenv()


class Config:
    # Основные настройки
    SECRET_KEY = os.environ.get('SECRET_KEY', secrets.token_hex(32))
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///monitor.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Безопасность
    ANONYMIZE_IP = os.environ.get('ANONYMIZE_IP', 'True').lower() == 'true'
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'False').lower() == 'true'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

    # Rate limiting
    RATELIMIT_ENABLED = os.environ.get('RATELIMIT_ENABLED', 'True').lower() == 'true'
    RATELIMIT_DEFAULT = os.environ.get('RATELIMIT_DEFAULT', '100 per hour')
    RATELIMIT_STORAGE_URL = os.environ.get('RATELIMIT_STORAGE_URL', 'memory://')

    # CSRF защита
    WTF_CSRF_ENABLED = os.environ.get('WTF_CSRF_ENABLED', 'True').lower() == 'true'
    WTF_CSRF_SECRET_KEY = os.environ.get('WTF_CSRF_SECRET_KEY', secrets.token_hex(32))

    # Кэширование
    CACHE_TYPE = os.environ.get('CACHE_TYPE', 'SimpleCache')  # Для разработки: SimpleCache, для production: RedisCache
    CACHE_REDIS_HOST = os.environ.get('REDIS_HOST', 'localhost')
    CACHE_REDIS_PORT = int(os.environ.get('REDIS_PORT', 6379))
    CACHE_REDIS_DB = int(os.environ.get('REDIS_DB', 0))
    CACHE_REDIS_URL = os.environ.get('REDIS_URL', f"redis://{CACHE_REDIS_HOST}:{CACHE_REDIS_PORT}/{CACHE_REDIS_DB}")
    CACHE_DEFAULT_TIMEOUT = int(os.environ.get('CACHE_TIMEOUT', 300))  # 5 минут

    # Администратор по умолчанию
    ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'Admin123!')

    # API ключи
    PAGESPEED_API_KEY = os.environ.get('PAGESPEED_API_KEY', '')

    # Оповещения
    TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
    ADMIN_EMAILS = [e.strip() for e in os.environ.get('ADMIN_EMAILS', '').split(',') if e.strip()]

    # Режим отладки
    FLASK_DEBUG = os.environ.get('FLASK_DEBUG', 'True').lower() == 'true'

    # Хост и порт
    HOST = os.environ.get('HOST', '0.0.0.0')
    PORT = int(os.environ.get('PORT', 5000))

    # Настройки фоновых задач
    BACKGROUND_TASKS_ENABLED = os.environ.get('BACKGROUND_TASKS_ENABLED', 'True').lower() == 'true'
    PAGESPEED_COLLECT_INTERVAL = int(os.environ.get('PAGESPEED_COLLECT_INTERVAL', 3600))  # 1 час
    AGGREGATE_INTERVAL = int(os.environ.get('AGGREGATE_INTERVAL', 86400))  # 24 часа

    # Настройки бэкапов
    BACKUP_ENABLED = os.environ.get('BACKUP_ENABLED', 'True').lower() == 'true'
    BACKUP_INTERVAL_HOURS = int(os.environ.get('BACKUP_INTERVAL_HOURS', 24))
    BACKUP_MAX_FILES = int(os.environ.get('BACKUP_MAX_FILES', 30))
    BACKUP_DIR = os.environ.get('BACKUP_DIR', 'backups')

    # Email настройки
    SMTP_SERVER = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
    SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
    SMTP_USERNAME = os.environ.get('SMTP_USERNAME', '')
    SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')
    SMTP_USE_TLS = os.environ.get('SMTP_USE_TLS', 'True').lower() == 'true'
    ALERT_EMAILS = [e.strip() for e in os.environ.get('ALERT_EMAILS', '').split(',') if e.strip()]

    # WebSocket настройки
    SOCKETIO_ASYNC_MODE = os.environ.get('SOCKETIO_ASYNC_MODE', 'eventlet')