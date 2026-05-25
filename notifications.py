import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from threading import Thread
import logging
from flask import current_app

logger = logging.getLogger(__name__)


class EmailNotifier:
    """Класс для отправки email уведомлений"""

    def __init__(self, smtp_server, smtp_port, sender_email, sender_password, use_tls=True):
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.sender_email = sender_email
        self.sender_password = sender_password
        self.use_tls = use_tls

    def send_email_async(self, recipient_email, subject, body_html, body_text=None):
        """Асинхронная отправка email"""
        thread = Thread(target=self._send_email, args=(recipient_email, subject, body_html, body_text))
        thread.daemon = True
        thread.start()

    def _send_email(self, recipient_email, subject, body_html, body_text=None):
        """Отправка email"""
        try:
            message = MIMEMultipart("alternative")
            message["Subject"] = subject
            message["From"] = self.sender_email
            message["To"] = recipient_email

            # Текстовая версия
            if body_text:
                part_text = MIMEText(body_text, "plain")
                message.attach(part_text)
            else:
                # Простая текстовая версия из HTML
                import re
                clean_text = re.sub(r'<[^>]+>', '', body_html)
                part_text = MIMEText(clean_text, "plain")
                message.attach(part_text)

            # HTML версия
            part_html = MIMEText(body_html, "html")
            message.attach(part_html)

            # Отправка
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

    def send_alert(self, recipient_email, site_name, metric_name, current_value, threshold, severity):
        """Отправка оповещения о критическом событии"""
        subject = f"⚠️ {severity.upper()} оповещение: {site_name} - {metric_name}"

        body_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; }}
                .alert {{ padding: 15px; border-radius: 8px; margin: 10px 0; }}
                .critical {{ background: #fce8e6; border-left: 4px solid #c5221f; }}
                .warning {{ background: #fef7e0; border-left: 4px solid #e37400; }}
                .info {{ background: #e8f0fe; border-left: 4px solid #1a73e8; }}
                .value {{ font-size: 24px; font-weight: bold; }}
                .metric {{ font-size: 18px; }}
                .footer {{ margin-top: 20px; font-size: 12px; color: #5f6368; }}
            </style>
        </head>
        <body>
            <h2>📊 Монитор обращений граждан</h2>
            <div class="alert {severity}">
                <p class="metric"><strong>{metric_name}</strong></p>
                <p class="value">Текущее значение: {current_value}</p>
                <p>Пороговое значение: {threshold}</p>
                <p>Сайт: {site_name}</p>
            </div>
            <p>Пожалуйста, проверьте дашборд для получения подробной информации.</p>
            <hr>
            <div class="footer">
                <p>Это автоматическое сообщение. Пожалуйста, не отвечайте на него.</p>
            </div>
        </body>
        </html>
        """

        self.send_email_async(recipient_email, subject, body_html)

    def send_daily_report(self, recipient_email, site_name, metrics_data):
        """Отправка ежедневного отчёта"""
        subject = f"📊 Ежедневный отчёт: {site_name}"

        body_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; }}
                table {{ border-collapse: collapse; width: 100%; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                th {{ background-color: #1a73e8; color: white; }}
                .good {{ color: #34a853; }}
                .warning {{ color: #fbbc04; }}
                .critical {{ color: #ea4335; }}
            </style>
        </head>
        <body>
            <h2>📊 Ежедневный отчёт</h2>
            <p><strong>Сайт:</strong> {site_name}</p>
            <p><strong>Дата:</strong> {metrics_data.get('date', 'N/A')}</p>

            <h3>Ключевые метрики</h3>
            <table>
                <tr><th>Метрика</th><th>Значение</th><th>Статус</th></tr>
                <tr><td>Просмотры</td><td>{metrics_data.get('pageviews', 0)}</td><td>-</td></tr>
                <tr><td>Отправки форм</td><td>{metrics_data.get('submits', 0)}</td><td>-</td></tr>
                <tr><td>Конверсия</td><td>{metrics_data.get('conversion_rate', 0)}%</td><td>-</td></tr>
                <tr><td>LCP</td><td>{metrics_data.get('lcp', 'N/A')} мс</td>
                    <td class="{metrics_data.get('lcp_rating', 'good')}">{metrics_data.get('lcp_rating', 'good')}</td></tr>
            </table>

            <p style="margin-top: 20px;">
                <a href="{metrics_data.get('dashboard_url', '#')}" style="background: #1a73e8; color: white; padding: 10px 20px; text-decoration: none; border-radius: 8px;">
                    Перейти к дашборду
                </a>
            </p>

            <div class="footer">
                <p>Это автоматическое сообщение. Пожалуйста, не отвечайте на него.</p>
            </div>
        </body>
        </html>
        """

        self.send_email_async(recipient_email, subject, body_html)

    def send_test_email(self, recipient_email):
        """Отправка тестового email"""
        subject = "🔧 Тестовое уведомление от Монитора обращений граждан"
        body_html = """
        <h2>✅ Тестовое уведомление</h2>
        <p>Если вы видите это сообщение, значит email уведомления настроены правильно.</p>
        <p>Вы будете получать оповещения о:</p>
        <ul>
            <li>Критических аномалиях на сайте</li>
            <li>Проблемах с производительностью</li>
            <li>Ежедневных отчётах</li>
        </ul>
        """

        self.send_email_async(recipient_email, subject, body_html)


# Глобальный экземпляр (будет инициализирован в app.py)
email_notifier = None


def init_email_notifier(app):
    """Инициализация email уведомлений из конфигурации"""
    global email_notifier

    smtp_server = app.config.get('SMTP_SERVER')
    smtp_port = app.config.get('SMTP_PORT', 587)
    sender_email = app.config.get('SMTP_USERNAME')
    sender_password = app.config.get('SMTP_PASSWORD')

    if smtp_server and sender_email and sender_password:
        email_notifier = EmailNotifier(
            smtp_server=smtp_server,
            smtp_port=smtp_port,
            sender_email=sender_email,
            sender_password=sender_password,
            use_tls=app.config.get('SMTP_USE_TLS', True)
        )
        logger.info("Email notifier initialized")
        return True
    else:
        logger.warning("Email notifier not configured")
        return False


def send_alert_email(recipients, site_name, metric_name, current_value, threshold, severity):
    """Отправка оповещения на email"""
    if email_notifier and recipients:
        for recipient in recipients:
            if recipient and '@' in recipient:
                email_notifier.send_alert(recipient, site_name, metric_name, current_value, threshold, severity)