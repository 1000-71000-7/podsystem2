import numpy as np
from datetime import datetime, timedelta
from app import app, db
from database import Site, DailyAggregate, AnomalyDetection
from alerts import send_telegram_alert


def detect_anomalies():
    """Обнаруживает аномалии в метриках"""
    with app.app_context():
        for site in Site.query.all():
            end_date = datetime.utcnow().date()
            start_date = end_date - timedelta(days=30)

            daily_data = DailyAggregate.query.filter(
                DailyAggregate.site_id == site.id,
                DailyAggregate.date >= start_date,
                DailyAggregate.date <= end_date
            ).order_by(DailyAggregate.date).all()

            if len(daily_data) < 14:
                continue

            # Анализируем просмотры
            views = [d.pageviews for d in daily_data]
            check_metric(
                site_id=site.id,
                metric_name='pageviews',
                values=views,
                current_value=views[-1] if views else 0,
                expected_mean=np.mean(views[:-7]),
                expected_std=np.std(views[:-7])
            )

            # Анализируем отправки
            submits = [d.form_submits for d in daily_data]
            check_metric(
                site_id=site.id,
                metric_name='form_submits',
                values=submits,
                current_value=submits[-1] if submits else 0,
                expected_mean=np.mean(submits[:-7]),
                expected_std=np.std(submits[:-7])
            )


def check_metric(site_id, metric_name, values, current_value, expected_mean, expected_std):
    """Проверка конкретной метрики"""
    if expected_std == 0:
        return

    deviation = abs(current_value - expected_mean) / expected_std

    if deviation >= 3:
        severity = 'high'
        message = f"🔴 КРИТИЧЕСКАЯ АНОМАЛИЯ!\nМетрика: {metric_name}\nТекущее: {current_value}\nОжидаемое: {expected_mean:.0f}\nОтклонение: {deviation:.1f}σ"
        send_telegram_alert(message)
    elif deviation >= 2:
        severity = 'medium'
        message = f"⚠️ Аномалия\nМетрика: {metric_name}\nТекущее: {current_value}\nОжидаемое: {expected_mean:.0f}\nОтклонение: {deviation:.1f}σ"
        send_telegram_alert(message)
    elif deviation >= 1.5:
        severity = 'low'
    else:
        return

    existing = AnomalyDetection.query.filter(
        AnomalyDetection.site_id == site_id,
        AnomalyDetection.metric_name == metric_name,
        AnomalyDetection.is_resolved == False
    ).first()

    if not existing:
        anomaly = AnomalyDetection(
            site_id=site_id,
            metric_name=metric_name,
            expected_value=expected_mean,
            actual_value=current_value,
            deviation_sigma=deviation,
            severity=severity
        )
        db.session.add(anomaly)
        db.session.commit()


if __name__ == '__main__':
    detect_anomalies()