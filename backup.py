import os
import shutil
import sqlite3
import gzip
import schedule
import time
from datetime import datetime
from threading import Thread


class DatabaseBackup:
    """Класс для автоматического резервного копирования базы данных"""

    def __init__(self, db_path, backup_dir="backups"):
        self.db_path = db_path
        self.backup_dir = backup_dir
        self.max_backups = 30  # Хранить последние 30 копий

        # Создаём директорию для бэкапов
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)

    def create_backup(self):
        """Создание резервной копии базы данных"""
        try:
            # Формируем имя файла бэкапа
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_filename = f"backup_{timestamp}.sqlite"
            backup_path = os.path.join(self.backup_dir, backup_filename)

            # Копируем файл базы данных
            if os.path.exists(self.db_path):
                shutil.copy2(self.db_path, backup_path)

                # Сжимаем бэкап
                compressed_path = backup_path + '.gz'
                with open(backup_path, 'rb') as f_in:
                    with gzip.open(compressed_path, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)

                # Удаляем несжатый файл
                os.remove(backup_path)

                # Очищаем старые бэкапы
                self._cleanup_old_backups()

                print(f"✅ Бэкап создан: {compressed_path}")
                return compressed_path
            else:
                print(f"❌ Файл БД не найден: {self.db_path}")
                return None
        except Exception as e:
            print(f"❌ Ошибка создания бэкапа: {e}")
            return None

    def _cleanup_old_backups(self):
        """Удаление старых резервных копий"""
        try:
            backups = [f for f in os.listdir(self.backup_dir) if f.endswith('.gz')]
            backups.sort()

            while len(backups) > self.max_backups:
                old_file = os.path.join(self.backup_dir, backups[0])
                os.remove(old_file)
                print(f"🗑️ Удалён старый бэкап: {backups[0]}")
                backups.pop(0)
        except Exception as e:
            print(f"Ошибка очистки бэкапов: {e}")

    def restore_backup(self, backup_filename):
        """Восстановление базы данных из бэкапа"""
        try:
            backup_path = os.path.join(self.backup_dir, backup_filename)

            if not os.path.exists(backup_path):
                return False, "Бэкап не найден"

            # Распаковываем
            if backup_filename.endswith('.gz'):
                extracted_path = backup_path.replace('.gz', '')
                with gzip.open(backup_path, 'rb') as f_in:
                    with open(extracted_path, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)
                backup_path = extracted_path

            # Останавливаем текущее соединение с БД
            # Восстанавливаем файл
            shutil.copy2(backup_path, self.db_path)

            # Удаляем временный файл если был
            if backup_filename.endswith('.gz') and os.path.exists(extracted_path):
                os.remove(extracted_path)

            return True, "База данных восстановлена"
        except Exception as e:
            return False, str(e)

    def list_backups(self):
        """Список доступных бэкапов"""
        backups = []
        for f in os.listdir(self.backup_dir):
            if f.endswith('.gz'):
                file_path = os.path.join(self.backup_dir, f)
                stat = os.stat(file_path)
                backups.append({
                    'filename': f,
                    'size': stat.st_size,
                    'created': datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                })
        return sorted(backups, key=lambda x: x['created'], reverse=True)


def start_backup_scheduler(db_path, interval_hours=24):
    """Запуск планировщика бэкапов"""
    backup = DatabaseBackup(db_path)

    # Создаём бэкап при запуске
    backup.create_backup()

    # Настраиваем расписание
    schedule.every(interval_hours).hours.do(backup.create_backup)

    def run_scheduler():
        while True:
            schedule.run_pending()
            time.sleep(60)

    thread = Thread(target=run_scheduler, daemon=True)
    thread.start()
    print(f"✅ Планировщик бэкапов запущен (интервал: {interval_hours} часов)")

    return backup