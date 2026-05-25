import functools
import time
import logging
from flask import jsonify, request, current_app
import requests

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ========== ДЕКОРАТОРЫ ДЛЯ RETRY ==========

def retry_on_failure(max_attempts=3, delay=1, backoff=2):
    """
    Декоратор для повторных попыток при ошибках
    max_attempts: максимальное количество попыток
    delay: начальная задержка в секундах
    backoff: множитель задержки (экспоненциальная задержка)
    """

    def decorator(func):
        @functools.wraps(func)
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


# ========== RETRY ДЛЯ API ЗАПРОСОВ (без tenacity) ==========

def make_request_with_retry(url, method='GET', max_attempts=3, delay=2, backoff=2, **kwargs):
    """Выполнение HTTP запроса с автоматическими повторами (без tenacity)"""
    kwargs.setdefault('timeout', 30)
    last_exception = None
    current_delay = delay

    for attempt in range(max_attempts):
        try:
            if method.upper() == 'GET':
                response = requests.get(url, **kwargs)
            elif method.upper() == 'POST':
                response = requests.post(url, **kwargs)
            elif method.upper() == 'PUT':
                response = requests.put(url, **kwargs)
            else:
                response = requests.request(method, url, **kwargs)

            response.raise_for_status()
            return response
        except (ConnectionError, TimeoutError, requests.RequestException) as e:
            last_exception = e
            logger.warning(f"Попытка {attempt + 1}/{max_attempts} для запроса {url}: {e}")

            if attempt < max_attempts - 1:
                time.sleep(current_delay)
                current_delay *= backoff
            else:
                logger.error(f"Запрос {url} не выполнился после {max_attempts} попыток")
                raise last_exception
        except Exception as e:
            # Не повторяем для других ошибок
            logger.error(f"Ошибка при запросе {url}: {e}")
            raise

    raise last_exception if last_exception else Exception("Unknown error")


# ========== ОБРАБОТЧИКИ ОШИБОК ДЛЯ FLASK ==========

def register_error_handlers(app):
    """Регистрация глобальных обработчиков ошибок"""

    @app.errorhandler(400)
    def bad_request(error):
        logger.error(f"Bad request: {error}")
        return jsonify({
            'error': 'Bad request',
            'message': str(error.description if hasattr(error, 'description') else error),
            'status_code': 400
        }), 400

    @app.errorhandler(401)
    def unauthorized(error):
        logger.warning(f"Unauthorized access attempt: {error}")
        return jsonify({
            'error': 'Unauthorized',
            'message': 'Требуется авторизация',
            'status_code': 401
        }), 401

    @app.errorhandler(403)
    def forbidden(error):
        logger.warning(f"Forbidden access: {error}")
        return jsonify({
            'error': 'Forbidden',
            'message': 'Недостаточно прав для выполнения операции',
            'status_code': 403
        }), 403

    @app.errorhandler(404)
    def not_found(error):
        logger.warning(f"Not found: {error}")
        return jsonify({
            'error': 'Not found',
            'message': 'Ресурс не найден',
            'status_code': 404
        }), 404

    @app.errorhandler(429)
    def too_many_requests(error):
        logger.warning(f"Rate limit exceeded: {error}")
        return jsonify({
            'error': 'Too many requests',
            'message': 'Превышен лимит запросов. Попробуйте позже.',
            'status_code': 429
        }), 429

    @app.errorhandler(500)
    def internal_server_error(error):
        logger.error(f"Internal server error: {error}")
        return jsonify({
            'error': 'Internal server error',
            'message': 'Внутренняя ошибка сервера',
            'status_code': 500
        }), 500

    @app.errorhandler(Exception)
    def handle_all_exceptions(error):
        logger.error(f"Unhandled exception: {error}")
        return jsonify({
            'error': 'Internal server error',
            'message': 'Произошла непредвиденная ошибка',
            'status_code': 500
        }), 500


# ========== ЛОГГЕР ДЛЯ API ЗАПРОСОВ ==========

def log_api_request(func):
    """Декоратор для логирования API запросов"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()

        # Логируем запрос
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