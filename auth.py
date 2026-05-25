from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app, session
from flask_login import login_user, logout_user, login_required, current_user
from models import db, User, AuditLog
from datetime import datetime, timezone
import hashlib
import secrets
import time

auth_bp = Blueprint('auth', __name__)


def add_audit_log(user_id, username, action, resource, details=None):
    """Добавление записи в лог аудита"""
    try:
        log = AuditLog(
            user_id=user_id,
            username=username,
            action=action,
            resource=resource,
            ip_address=request.remote_addr if request else None,
            user_agent=request.headers.get('User-Agent', '')[:500] if request else None,
            details=details
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        print(f"Failed to add audit log: {e}")


def init_admin_user():
    """Создание администратора по умолчанию"""
    with current_app.app_context():
        admin = User.query.filter_by(username=current_app.config['ADMIN_USERNAME']).first()
        if not admin:
            admin = User(
                username=current_app.config['ADMIN_USERNAME'],
                role='admin',
                is_active=True
            )
            admin.set_password(current_app.config['ADMIN_PASSWORD'])
            db.session.add(admin)
            db.session.commit()
            print(f"✅ Администратор создан: {admin.username}")


def generate_csrf_token():
    """Генерация CSRF токена для форм"""
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)
    return session['csrf_token']


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember = request.form.get('remember') == 'on'

        # Защита от brute force (задержка)
        time.sleep(0.5)

        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password) and user.is_active:
            login_user(user, remember=remember)
            user.last_login = datetime.now(timezone.utc)
            db.session.commit()

            # Логируем вход
            add_audit_log(user.id, user.username, 'login', 'system',
                          f"Вход в систему с IP: {request.remote_addr}")

            next_page = request.args.get('next')
            if next_page and next_page.startswith('/'):
                return redirect(next_page)
            return redirect(url_for('dashboard'))
        else:
            flash('Неверное имя пользователя или пароль', 'error')

    return render_template('login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    username = current_user.username
    add_audit_log(current_user.id, username, 'logout', 'system', "Выход из системы")
    logout_user()
    flash('Вы вышли из системы', 'success')
    return redirect(url_for('login'))


@auth_bp.route('/profile')
@login_required
def profile():
    return render_template('profile.html', user=current_user)


@auth_bp.route('/change-password', methods=['POST'])
@login_required
def change_password():
    old_password = request.form.get('old_password')
    new_password = request.form.get('new_password')
    confirm_password = request.form.get('confirm_password')

    if not current_user.check_password(old_password):
        flash('Неверный текущий пароль', 'error')
        return redirect(url_for('auth.profile'))

    if new_password != confirm_password:
        flash('Пароли не совпадают', 'error')
        return redirect(url_for('auth.profile'))

    if len(new_password) < 6:
        flash('Пароль должен быть не менее 6 символов', 'error')
        return redirect(url_for('auth.profile'))

    current_user.set_password(new_password)
    db.session.commit()

    add_audit_log(current_user.id, current_user.username, 'change_password', 'system', "Смена пароля")
    flash('Пароль успешно изменён', 'success')
    return redirect(url_for('auth.profile'))