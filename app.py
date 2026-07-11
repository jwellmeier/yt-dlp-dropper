import os
import sys
import threading
import uuid

import requests
import socketio
from PySide6.QtCore import Qt, QObject, QRect, QSize, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAction,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

BACKEND_URL = os.environ.get("YTDLP_BACKEND_URL", "http://127.0.0.1:5000")
SOCKET_URL = BACKEND_URL


class DownloadTask:
    def __init__(self, url: str, task_id: str = None):
        self.id = task_id or str(uuid.uuid4())
        self.url = url
        self.status = "queued"
        self.progress = 0.0
        self.message = "Queued"
        self.filename = None
        self.error = None

    def update(self, payload: dict):
        self.status = payload.get("status", self.status)
        self.progress = payload.get("progress", self.progress) or self.progress
        self.message = payload.get("message", self.message)
        self.filename = payload.get("filename", self.filename)
        self.error = payload.get("error", self.error)


class DownloadCardWidget(QFrame):
    cancel_requested = Signal(str)
    retry_requested = Signal(str)

    def __init__(self, task: DownloadTask):
        super().__init__()
        self.task = task
        self.current_section = "active"
        self.setFrameShape(QFrame.StyledPanel)
        self.setFrameShadow(QFrame.Raised)
        self.setStyleSheet("QFrame { background: #f8f9fa; border: 1px solid #d0d0d0; border-radius: 6px; }")

        self.title_label = QLabel(task.url)
        self.title_label.setWordWrap(True)
        self.status_label = QLabel(task.message)
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(int(self.task.progress))
        self.cancel_button = QPushButton("Cancel")
        self.retry_button = QPushButton("Retry")
        self.retry_button.setVisible(False)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.cancel_button)
        button_layout.addWidget(self.retry_button)
        button_layout.addStretch()

        layout = QVBoxLayout(self)
        layout.addWidget(self.title_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress_bar)
        layout.addLayout(button_layout)

        self.cancel_button.clicked.connect(lambda: self.cancel_requested.emit(self.task.id))
        self.retry_button.clicked.connect(lambda: self.retry_requested.emit(self.task.id))
        self.update_status(task)

    def _progress_text(self):
        return f"{self.task.progress:.1f}%" if self.task.progress is not None else "Pending"

    def update_status(self, task: DownloadTask):
        self.task = task
        self.title_label.setText(task.filename or task.url)
        self.status_label.setText(task.message or task.status.capitalize())
        self.progress_bar.setValue(int(task.progress or 0))
        self.update_buttons()

    def update_buttons(self):
        is_active = self.task.status in ("queued", "downloading")
        self.cancel_button.setEnabled(is_active)
        self.cancel_button.setVisible(is_active)
        self.retry_button.setVisible(self.task.status in ("error", "cancelled"))


class DropZone(QLabel):
    dropped = Signal(str)

    def __init__(self):
        super().__init__("Drag a URL or file here to queue a download")
        self.setAlignment(Qt.AlignCenter)
        self.setAcceptDrops(True)
        self.setAutoFillBackground(True)
        self.setStyleSheet(
            "QLabel { border: 2px dashed #6c757d; background: #ffffff; padding: 30px; font-size: 14px; color: #333333; }"
        )

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = []
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    urls.append(url.toLocalFile())
                else:
                    urls.append(url.toString())
        elif event.mimeData().hasText():
            text = event.mimeData().text().strip()
            urls.extend([line.strip() for line in text.splitlines() if line.strip()])

        for item in urls:
            self.dropped.emit(item)


class BackendClient(QObject):
    task_event = Signal(str, dict)
    request_failed = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.sio = socketio.Client(logger=False, reconnection=True)
        self.sio.on("connect", self.on_connect)
        self.sio.on("disconnect", self.on_disconnect)
        self.sio.on("download_event", self.on_download_event)
        self.connected = False

    def on_connect(self):
        self.connected = True
        print("Connected to backend socket")

    def on_disconnect(self):
        self.connected = False
        print("Disconnected from backend socket")

    def on_download_event(self, data):
        task_id = data.get("task_id")
        if task_id:
            self.task_event.emit(task_id, data)

    def start(self):
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()

    def _run(self):
        try:
            self.sio.connect(SOCKET_URL)
            self.sio.wait()
        except Exception as exc:
            print(f"SocketIO connection failed: {exc}")

    def submit_task(self, task: DownloadTask):
        def worker():
            try:
                response = requests.post(
                    f"{BACKEND_URL}/download",
                    json={"url": task.url, "task_id": task.id},
                    timeout=20,
                )
                if response.status_code >= 400:
                    self.request_failed.emit(task.id, response.text)
            except Exception as exc:
                self.request_failed.emit(task.id, str(exc))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def cancel_task(self, task_id: str):
        def worker():
            try:
                response = requests.post(f"{BACKEND_URL}/cancel/{task_id}", timeout=15)
                if response.status_code >= 400:
                    self.request_failed.emit(task_id, response.text)
            except Exception as exc:
                self.request_failed.emit(task_id, str(exc))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()


class ProgressWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("yt-dlp Dropper")
        self.resize(580, 600)
        self.tasks = {}
        self.widgets = {}

        self.backend = BackendClient()
        self.backend.task_event.connect(self.handle_task_event)
        self.backend.request_failed.connect(self.handle_request_failed)
        self.backend.start()

        self._build_ui()
        self._build_tray()

    def _build_ui(self):
        central_widget = QWidget()
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(14)

        self.drop_zone = DropZone()
        self.drop_zone.dropped.connect(self.enqueue_url)
        main_layout.addWidget(self.drop_zone)

        self.active_area = self._create_section("Active Downloads")
        self.completed_area = self._create_section("Completed / Failed")
        main_layout.addWidget(self.active_area)
        main_layout.addWidget(self.completed_area)

        self.setCentralWidget(central_widget)

    def _create_section(self, title: str) -> QWidget:
        section_widget = QWidget()
        section_layout = QVBoxLayout(section_widget)
        section_layout.setSpacing(8)
        section_layout.addWidget(QLabel(f"<b>{title}</b>"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(container)
        section_layout.addWidget(scroll)
        if title.startswith("Active"):
            self.active_container = container
            self.active_layout = container_layout
        else:
            self.completed_container = container
            self.completed_layout = container_layout
        return section_widget

    def _build_tray(self):
        self.tray_icon = QSystemTrayIcon(self._create_icon(), self)
        self.tray_icon.setToolTip("yt-dlp Dropper")

        tray_menu = QMenu()
        open_action = QAction("Open")
        quit_action = QAction("Quit")
        open_action.triggered.connect(self.show_normal)
        quit_action.triggered.connect(self.close_application)
        tray_menu.addAction(open_action)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()

    def _create_icon(self) -> QIcon:
        pixmap = QPixmap(QSize(24, 24))
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor("#0069d9"))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QRect(2, 2, 20, 20))
        painter.end()
        return QIcon(pixmap)

    def enqueue_url(self, url: str):
        task = DownloadTask(url)
        self.tasks[task.id] = task
        self.add_task_widget(task)
        self.backend.submit_task(task)
        self.update_task(task.id, {"status": "queued", "message": "Queued"})

    def add_task_widget(self, task: DownloadTask):
        widget = DownloadCardWidget(task)
        widget.cancel_requested.connect(self.cancel_task)
        widget.retry_requested.connect(self.retry_task)
        self.widgets[task.id] = widget
        self.active_layout.addWidget(widget)

    def handle_task_event(self, task_id: str, payload: dict):
        if task_id not in self.tasks:
            self.tasks[task_id] = DownloadTask(payload.get("url", ""), task_id=task_id)
            self.add_task_widget(self.tasks[task_id])

        self.update_task(task_id, payload)

    def handle_request_failed(self, task_id: str, error: str):
        self.update_task(task_id, {"status": "error", "message": "Backend request failed", "error": error})

    def update_task(self, task_id: str, payload: dict):
        task = self.tasks.get(task_id)
        widget = self.widgets.get(task_id)
        if not task or not widget:
            return

        task.update(payload)
        widget.update_status(task)
        self._sync_widget_location(widget, task)

    def _sync_widget_location(self, widget: DownloadCardWidget, task: DownloadTask):
        if task.status in ("completed", "error", "cancelled") and widget.current_section != "finished":
            widget.setParent(None)
            self.completed_layout.addWidget(widget)
            widget.current_section = "finished"
        elif task.status in ("queued", "downloading") and widget.current_section != "active":
            widget.setParent(None)
            self.active_layout.addWidget(widget)
            widget.current_section = "active"

    def cancel_task(self, task_id: str):
        task = self.tasks.get(task_id)
        if not task:
            return
        task.status = "cancelled"
        task.message = "Cancelling..."
        self.update_task(task_id, {"status": "cancelled", "message": "Cancelling..."})
        self.backend.cancel_task(task_id)

    def retry_task(self, task_id: str):
        task = self.tasks.get(task_id)
        if not task:
            return
        new_task = DownloadTask(task.url)
        self.tasks[new_task.id] = new_task
        self.add_task_widget(new_task)
        self.backend.submit_task(new_task)
        self.update_task(new_task.id, {"status": "queued", "message": "Retrying"})

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.show_normal()
            self.activateWindow()

    def close_application(self):
        self.tray_icon.hide()
        QApplication.quit()

    def show_normal(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        self.hide()
        event.ignore()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ProgressWindow()
    window.show()
    sys.exit(app.exec())
