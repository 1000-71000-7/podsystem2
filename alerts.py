import requests
import schedule
import time
from datetime import datetime, timedelta, timezone
from app import app, db
from models import Site, Event, Alert, PageSpeedMetric


def check_metrics_and_alert():
    """Проверяет метрики и отправляет оповещения"""
    with app.app_context():
        for site in Site.query.all():
            # Проверяем просмотры за последний час
            hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
            recent_views = Event.query.filter(
                Event.site_id == site.id,
                Event.timestamp >= hour_ago,
                Event.event_type == 'pageview'
            ).count()

            # Если просмотров нет, а сайт должен работать - оповещаем
            if recent_views == 0 and site.is_active:
                send_telegram_alert(
                    f"⚠️ ВНИМАНИЕ! Сайт '{site.name}'\n"
                    f"За последний час не зафиксировано ни одного просмотра.\n"
                    f"Возможны проблемы с доступностью сайта."
                )
                # Также отправляем email оповещение
                send_email_alert(site.name, "Нет активности", "За последний час не было просмотров")

            # Проверяем производительность
            last_metric = PageSpeedMetric.query.filter_by(
                site_id=site.id
            ).order_by(PageSpeedMetric.timestamp.desc()).first()

            if last_metric and last_metric.lcp:
                if last_metric.lcp > 4.0:
                    send_telegram_alert(
                        f"⚠️ Медленная загрузка сайта '{site.name}'\n"
                        f"LCP = {last_metric.lcp:.2f}с (норма < 2.5с)\n"
                        f"Это влияет на доступность формы обращений"
                    )
                    send_email_alert(
                        site.name,
                        "Медленная загрузка сайта",
                        f"LCP = {last_metric.lcp:.2f}с (норма < 2.5с)"
                    )


def send_telegram_alert(message):
    """Отправляет сообщение в Telegram"""
    token = app.config.get('TELEGRAM_BOT_TOKEN')
    chat_id = app.config.get('TELEGRAM_CHAT_ID')

    if not token or not chat_id:
        print(f"Telegram не настроен. Сообщение: {message}")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        response = requests.post(url, json={
            'chat_id': chat_id,
            'text': message,
            'parse_mode': 'HTML'
        }, timeout=5)
        if response.status_code == 200:
            print(f"✅ Telegram оповещение отправлено: {message[:50]}...")
        else:
            print(f"❌ Telegram ошибка: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"❌ Ошибка отправки в Telegram: {e}")


def send_email_alert(site_name, subject, message):
    """Отправляет email оповещение"""
    email_notifier = get_email_notifier()

    if not email_notifier:
        print("Email notifier не настроен")
        return

    recipients = app.config.get('ALERT_EMAILS', [])
    if not recipients:
        print("Нет настроенных email получателей")
        return

    email_subject = f"⚠️ Оповещение: {site_name} - {subject}"
    body_html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"></head>
    <body>
        <h2>⚠️ Оповещение системы мониторинга</h2>
        <p><strong>Сайт:</strong> {site_name}</p>
        <p><strong>Проблема:</strong> {subject}</p>
        <p><strong>Детали:</strong> {message}</p>
        <p><strong>Время:</strong> {datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M:%S')} UTC</p>
        <hr>
        <p>Это автоматическое сообщение от подсистемы мониторинга обращений граждан.</p>
    </body>
    </html>
    """

    for recipient in recipients:
        if recipient and '@' in recipient:
            email_notifier.send_email_async(recipient, email_subject, body_html)
            print(f"✅ Email отправлен на {recipient}")


def get_email_notifier():
    """Получает экземпляр email notifier"""
    try:
        from app import email_notifier
        return email_notifier
    except ImportError:
        return None


def run_scheduler():
    """Запускает планировщик задач"""
    print("🚀 Планировщик оповещений запущен")

    # Каждые 15 минут проверяем метрики
    schedule.every(15).minutes.do(check_metrics_and_alert)

    print("   ✅ check_metrics_and_alert: каждые 15 минут")

    # Проверяем, есть ли функции для сбора PageSpeed и агрегации
    try:
        from app import collect_pagespeed_background, aggregate_daily_data_func
        # Раз в час собираем PageSpeed метрики
        schedule.every().hour.do(collect_pagespeed_background)
        # Раз в сутки агрегируем данные (в 00:05)
        schedule.every().day.at("00:05").do(aggregate_daily_data_func)
        print("   ✅ collect_pagespeed: каждый час")
        print("   ✅ aggregate_daily_data: каждый день в 00:05")
    except ImportError:
        print("   ⚠️ Функции сбора метрик не найдены, будут использованы только проверки")

    while True:
        try:
            schedule.run_pending()
            time.sleep(60)
        except Exception as e:
            print(f"Ошибка в планировщике: {e}")
            time.sleep(60)


def aggregate_daily_data_func():
    """Обёртка для агрегации данных"""
    try:
        from app import aggregate_daily_data
        with app.app_context():
            aggregate_daily_data()
            print(f"✅ Агрегация данных выполнена: {datetime.now(timezone.utc)}")
    except Exception as e:
        print(f"❌ Ошибка агрегации: {e}")


# Для совместимости со старым кодом
def collect_all_pagespeed():
    """Собирает PageSpeed метрики для всех сайтов"""
    try:
        from app import collect_pagespeed_background
        collect_pagespeed_background()
    except Exception as e:
        print(f"❌ Ошибка сбора PageSpeed: {e}")


def aggregate_daily_data():
    """Агрегирует данные за день"""
    aggregate_daily_data_func()


# Запуск планировщика (если запущен напрямую)
if __name__ == '__main__':
    run_scheduler()