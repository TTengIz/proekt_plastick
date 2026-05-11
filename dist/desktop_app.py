import sys
import os
import time
import subprocess
import requests
import ctypes
from datetime import datetime
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QStackedWidget, QFrame,
    QMessageBox, QGridLayout, QComboBox, QDialog
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont

API_URL = "http://127.0.0.1:8000"
MUTEX_NAME = "ShiftApp_Mutex_SingleInstance"


class ShiftApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.token = None
        self.user_role = None
        self.username = None
        self.server_process = None
        self.server_started = False

        self.setWindowTitle("🏭 Учёт смен")
        self.setMinimumSize(800, 600)
        self.setStyleSheet("""
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #667eea, stop:1 #764ba2);
            }
            QWidget { font-family: 'Segoe UI', Arial, sans-serif; }
            QLabel { color: #333; }
            QLineEdit, QComboBox {
                padding: 12px; border: 2px solid #e0e0e0; border-radius: 10px;
                font-size: 14px; background: white;
            }
            QLineEdit:focus, QComboBox:focus { border: 2px solid #667eea; }
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #667eea, stop:1 #764ba2);
                color: white; border: none; border-radius: 10px; padding: 12px 24px;
                font-size: 14px; font-weight: bold;
            }
            QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #5568d3, stop:1 #65408b); }
            QFrame { background: white; border-radius: 20px; }
        """)

        # Пробуем запустить сервер, но не блокируем приложение
        QTimer.singleShot(100, self.try_start_server)

        self.init_ui()

    def try_start_server(self):
        """Пытаемся запустить сервер в фоне"""
        # Проверяем, запущен ли уже сервер
        try:
            requests.get(f"{API_URL}/docs", timeout=1)
            self.server_started = True
            return
        except:
            pass

        # Если уже пытались запустить - не делаем это снова
        if self.server_started:
            return

        self.server_started = True

        # Определяем путь к main.py
        if getattr(sys, 'frozen', False):
            app_path = os.path.dirname(sys.executable)
            main_path = os.path.join(app_path, 'main.py')
            python_exe = sys.executable
        else:
            app_path = os.path.dirname(os.path.abspath(__file__))
            main_path = os.path.join(app_path, 'main.py')
            python_exe = sys.executable

        if not os.path.exists(main_path):
            # Тихо игнорируем, покажем ошибку только при попытке входа
            return

        try:
            # Запускаем сервер как отдельный процесс
            self.server_process = subprocess.Popen(
                [python_exe, "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
                 "--port", "8000", "--log-level", "error"],
                cwd=app_path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )

            # Проверяем готовность через 3 секунды
            QTimer.singleShot(3000, self.check_server_ready)

        except Exception as e:
            print(f"Ошибка запуска сервера: {e}")

    def check_server_ready(self):
        """Проверяем готовность сервера"""
        try:
            requests.get(f"{API_URL}/docs", timeout=1)
        except:
            pass  # Сервер может загружаться дольше

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.container = QFrame()
        self.container.setMinimumSize(600, 500)
        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(40, 40, 40, 40)
        container_layout.setSpacing(20)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.create_login_page())
        self.stack.addWidget(self.create_register_page())
        self.stack.addWidget(self.create_dashboard_page())
        container_layout.addWidget(self.stack)
        main_layout.addWidget(self.container)

    def create_login_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(20)
        layout.addWidget(self._title_label(" Учёт смен"))
        layout.addWidget(QLabel("Логин"))
        self.login_input = QLineEdit()
        self.login_input.setPlaceholderText("Введите логин")
        layout.addWidget(self.login_input)
        layout.addWidget(QLabel("Пароль"))
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Введите пароль")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.password_input)
        self.login_btn = QPushButton("Войти")
        self.login_btn.setMinimumHeight(50)
        self.login_btn.clicked.connect(self.login)
        layout.addWidget(self.login_btn)
        to_reg = QPushButton("📝 Нет аккаунта? Зарегистрироваться")
        to_reg.setStyleSheet("background:transparent;color:#667eea;border:2px solid #667eea;")
        to_reg.clicked.connect(lambda: self.stack.setCurrentIndex(1))
        layout.addWidget(to_reg)
        layout.addStretch()
        return page

    def create_register_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(20)
        layout.addWidget(self._title_label("📝 Регистрация"))
        layout.addWidget(QLabel("Логин"))
        self.reg_login = QLineEdit()
        self.reg_login.setPlaceholderText("Придумайте логин")
        layout.addWidget(self.reg_login)
        layout.addWidget(QLabel("Пароль"))
        self.reg_pass = QLineEdit()
        self.reg_pass.setPlaceholderText("Придумайте пароль")
        self.reg_pass.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.reg_pass)
        layout.addWidget(QLabel("Роль"))
        self.reg_role = QComboBox()
        self.reg_role.addItems(["Сотрудник", "Администратор"])
        self.reg_role.currentTextChanged.connect(self._toggle_secret)
        layout.addWidget(self.reg_role)

        self.secret_widget = QWidget()
        sl = QVBoxLayout(self.secret_widget)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(QLabel("Секретный ключ админа"))
        self.reg_secret = QLineEdit()
        self.reg_secret.setPlaceholderText("SUPER_SECRET_ADMIN_KEY_2026")
        sl.addWidget(self.reg_secret)
        layout.addWidget(self.secret_widget)
        self.secret_widget.hide()

        reg_btn = QPushButton("Создать аккаунт")
        reg_btn.setMinimumHeight(50)
        reg_btn.setStyleSheet("background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #11998e,stop:1 #38ef7d);")
        reg_btn.clicked.connect(self.register)
        layout.addWidget(reg_btn)
        back_btn = QPushButton("← Назад ко входу")
        back_btn.setStyleSheet("background:#6c757d;")
        back_btn.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        layout.addWidget(back_btn)
        layout.addStretch()
        return page

    def create_dashboard_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(20)
        self.welcome = QLabel("👤 Личный кабинет")
        self.welcome.setFont(QFont('Segoe UI', 24, QFont.Weight.Bold))
        self.welcome.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.welcome)

        stats = QGridLayout()
        stats.setSpacing(15)
        self.l_shifts = QLabel("0")
        self.l_shifts.setFont(QFont('Segoe UI', 32, QFont.Weight.Bold))
        self.l_shifts.setStyleSheet("color:#667eea;")
        self.l_shifts.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.t_shifts = QLabel("Всего смен")
        self.t_shifts.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.t_shifts.setStyleSheet("color:#666;font-size:14px;")
        self.l_hours = QLabel("0")
        self.l_hours.setFont(QFont('Segoe UI', 32, QFont.Weight.Bold))
        self.l_hours.setStyleSheet("color:#667eea;")
        self.l_hours.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.t_hours = QLabel("Отработано часов")
        self.t_hours.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.t_hours.setStyleSheet("color:#666;font-size:14px;")
        stats.addWidget(self.l_shifts, 0, 0)
        stats.addWidget(self.t_shifts, 1, 0)
        stats.addWidget(self.l_hours, 0, 1)
        stats.addWidget(self.t_hours, 1, 1)
        layout.addLayout(stats)

        self.open_btn = QPushButton("🕐 Начать смену")
        self.open_btn.setMinimumHeight(50)
        self.open_btn.setStyleSheet("background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #11998e,stop:1 #38ef7d);")
        self.open_btn.clicked.connect(self.open_shift)
        layout.addWidget(self.open_btn)
        self.close_btn = QPushButton("⏹️ Закончить смену")
        self.close_btn.setMinimumHeight(50)
        self.close_btn.setStyleSheet("background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #eb3349,stop:1 #f45c43);")
        self.close_btn.clicked.connect(self.close_shift)
        self.close_btn.hide()
        layout.addWidget(self.close_btn)

        self.hist_btn = QPushButton("📋 История смен")
        self.hist_btn.setMinimumHeight(45)
        self.hist_btn.clicked.connect(self.show_history)
        layout.addWidget(self.hist_btn)
        self.admin_btn = QPushButton("📊 Панель администратора")
        self.admin_btn.setMinimumHeight(45)
        self.admin_btn.clicked.connect(self.show_admin_panel)
        self.admin_btn.hide()
        layout.addWidget(self.admin_btn)

        out_btn = QPushButton("🚪 Выйти")
        out_btn.setMinimumHeight(45)
        out_btn.setStyleSheet("background:#6c757d;")
        out_btn.clicked.connect(self.logout)
        layout.addWidget(out_btn)
        layout.addStretch()
        return page

    def _title_label(self, text):
        lbl = QLabel(text)
        lbl.setFont(QFont('Segoe UI', 28, QFont.Weight.Bold))
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return lbl

    def _toggle_secret(self, text):
        self.secret_widget.setVisible(text == "Администратор")

    def login(self):
        # Проверяем сервер перед входом
        try:
            requests.get(f"{API_URL}/docs", timeout=2)
        except:
            QMessageBox.warning(self, "Предупреждение",
                                "Сервер ещё загружается. Подождите несколько секунд и попробуйте снова.")
            return

        u, p = self.login_input.text().strip(), self.password_input.text()
        if not u or not p:
            return QMessageBox.warning(self, "Ошибка", "Введите логин и пароль")
        try:
            r = requests.post(f"{API_URL}/auth/login", data={"username": u, "password": p}, timeout=5)
            if r.status_code == 200:
                d = r.json()
                self.token = d["access_token"]
                self.user_role = d["role"]
                self.username = u
                self.load_dashboard()
                self.stack.setCurrentIndex(2)
            else:
                QMessageBox.critical(self, "Ошибка", r.json().get("detail", "Ошибка входа"))
        except requests.exceptions.ConnectionError:
            QMessageBox.critical(self, "Ошибка",
                                 "Не удалось подключиться к серверу.\n\n"
                                 "Убедитесь, что файлы main.py, models.py и др. находятся рядом с приложением.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    def register(self):
        login, pwd = self.reg_login.text().strip(), self.reg_pass.text()
        if not login or not pwd:
            return QMessageBox.warning(self, "Ошибка", "Заполните логин и пароль")
        role = "admin" if self.reg_role.currentText() == "Администратор" else "user"
        payload = {"login": login, "password": pwd, "role": role}
        if role == "admin":
            payload["admin_secret"] = self.reg_secret.text().strip()
        try:
            r = requests.post(f"{API_URL}/auth/register", json=payload, timeout=5)
            if r.status_code == 201:
                QMessageBox.information(self, "Успех", "Аккаунт создан! Войдите.")
                self.stack.setCurrentIndex(0)
                [w.clear() for w in (self.reg_login, self.reg_pass, self.reg_secret)]
                self.reg_role.setCurrentIndex(0)
            else:
                QMessageBox.critical(self, "Ошибка", r.json().get("detail", "Ошибка"))
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    def load_dashboard(self):
        self.welcome.setText(f"👤 {self.username}")
        try:
            r = requests.get(f"{API_URL}/shifts/history?limit=1000",
                             headers={"Authorization": f"Bearer {self.token}"}, timeout=5)
            if r.status_code == 200:
                shifts = r.json()
                self.l_shifts.setText(str(len(shifts)))
                self.l_hours.setText(f"{sum(s.get('duration_hours', 0) or 0 for s in shifts):.1f}")
                has_open = any(not s.get("end_time") for s in shifts)
                self.open_btn.setVisible(not has_open)
                self.close_btn.setVisible(has_open)
                self.admin_btn.setVisible(self.user_role == "admin")
        except:
            pass

    def open_shift(self):
        try:
            r = requests.post(f"{API_URL}/shifts/open",
                              headers={"Authorization": f"Bearer {self.token}"}, timeout=5)
            if r.status_code == 200:
                t = datetime.fromisoformat(r.json()["start_time"].replace("Z", "+00:00"))
                QMessageBox.information(self, "Смена открыта", f"Время: {t.strftime('%H:%M')}")
                self.load_dashboard()
            else:
                QMessageBox.warning(self, "Ошибка", r.json().get("detail", "Ошибка"))
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    def close_shift(self):
        try:
            r = requests.post(f"{API_URL}/shifts/close",
                              headers={"Authorization": f"Bearer {self.token}"}, timeout=5)
            if r.status_code == 200:
                QMessageBox.information(self, "Смена закрыта",
                                        f"Отработано: {r.json().get('duration_hours', 0)} ч.")
                self.load_dashboard()
            else:
                QMessageBox.warning(self, "Ошибка", r.json().get("detail", "Ошибка"))
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    def show_history(self):
        try:
            r = requests.get(f"{API_URL}/shifts/history",
                             headers={"Authorization": f"Bearer {self.token}"}, timeout=5)
            if r.status_code == 200:
                shifts = r.json()
                if not shifts:
                    return QMessageBox.information(self, "История", "Пусто")
                msg = "📋 Последние смены:\n"
                for i, s in enumerate(shifts[:15], 1):
                    st = datetime.fromisoformat(s["start_time"].replace("Z", "+00:00"))
                    line = f"{i}. {st.strftime('%d.%m %H:%M')}"
                    if s.get("end_time"):
                        et = datetime.fromisoformat(s["end_time"].replace("Z", "+00:00"))
                        line += f" - {et.strftime('%H:%M')} ({s['duration_hours']}ч)"
                    if s.get("is_late"):
                        line += f" ️ +{s['late_minutes']}мин"
                    msg += line + "\n"
                QMessageBox.information(self, "История", msg)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    def show_admin_panel(self):
        try:
            r = requests.get(f"{API_URL}/admin/dashboard",
                             headers={"Authorization": f"Bearer {self.token}"}, timeout=5)
            if r.status_code == 200:
                d = r.json()
                txt = f"📊 Статистика:\n👥 Сотрудников: {d['total_users']}\n" \
                      f"🟢 На смене: {d['currently_working']}\n⚠️ Опоздали: {d['late_today']}"
                if QMessageBox.question(self, "Админ-панель", txt + "\n\nСкачать Excel?",
                                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) \
                        == QMessageBox.StandardButton.Yes:
                    self.download_report()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    def download_report(self):
        def get_date(title, default):
            dlg = QDialog(self)
            dlg.setWindowTitle("Ввод даты")
            lay = QVBoxLayout(dlg)
            lay.addWidget(QLabel(title))
            inp = QLineEdit(default)
            lay.addWidget(inp)
            btns = QHBoxLayout()
            ok = QPushButton("OK")
            no = QPushButton("Отмена")
            ok.clicked.connect(dlg.accept)
            no.clicked.connect(dlg.reject)
            btns.addWidget(ok)
            btns.addWidget(no)
            lay.addLayout(btns)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                return inp.text(), True
            return None, False

        s, ok1 = get_date("Дата начала (YYYY-MM-DD):", "2026-05-01")
        e, ok2 = get_date("Дата конца (YYYY-MM-DD):", "2026-05-31")
        if ok1 and ok2:
            import webbrowser
            webbrowser.open(f"{API_URL}/admin/reports/shifts/excel?start_date={s}&end_date={e}")

    def logout(self):
        self.token = self.user_role = self.username = None
        self.login_input.clear()
        self.password_input.clear()
        self.stack.setCurrentIndex(0)

    def closeEvent(self, event):
        """Закрываем сервер при выходе"""
        if self.server_process:
            try:
                self.server_process.terminate()
                self.server_process.wait(timeout=3)
            except:
                pass
        event.accept()


if __name__ == "__main__":
    # Проверяем, не запущено ли уже приложение
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        QMessageBox.critical(None, "Ошибка", "Приложение уже запущено!")
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = ShiftApp()
    window.show()
    sys.exit(app.exec())