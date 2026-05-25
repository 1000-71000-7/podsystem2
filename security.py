import hashlib
import re
from flask import request, session, current_app
from functools import wraps
from flask_login import current_user
from flask import abort


def anonymize_ip(ip_address):
    """
    Анонимизация IP-адреса для соответствия 152-ФЗ о персональных данных
    Используем SHA-256 с солью для невозможности обратного декодирования
    """
    if not current_app.config['ANONYMIZE_IP']:
        return ip_address

    salt = current_app.config['SECRET_KEY']
    # Добавляем соль и хешируем
    return hashlib.sha256(f"{ip_address}{salt}".encode()).hexdigest()[:32]


def sanitize_input(text):
    """Очистка пользовательского ввода от XSS"""
    if not text:
        return text
    # Простая очистка от опасных символов
    import html
    return html.escape(str(text))


def validate_url(url):
    """Проверка валидности URL"""
    if not url:
        return False
    pattern = re.compile(
        r'^https?://'  # http:// или https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # домен
        r'localhost|'  # localhost
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # IP
        r'(?::\d+)?'  # порт
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    return bool(pattern.match(url))


def require_role(required_role):
    """Декоратор для проверки роли пользователя"""

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if current_user.role == 'admin':
                return f(*args, **kwargs)
            if required_role == 'admin' and current_user.role != 'admin':
                abort(403)
            if required_role == 'analyst' and current_user.role not in ['admin', 'analyst']:
                abort(403)
            return f(*args, **kwargs)

        return decorated_function

    return decorator


def get_client_ip():
    """Получение реального IP клиента (с учётом прокси)"""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0]
    if request.headers.get('X-Real-IP'):
        return request.headers.get('X-Real-IP')
    return request.remote_addr