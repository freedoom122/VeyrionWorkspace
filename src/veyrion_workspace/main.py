"""Veyrion Workspace — application entry point.

Responsibilities:
  * High-DPI / scaling setup before any Qt object exists.
  * Single-instance guard (second launches forward to the first).
  * Crash-safe bootstrap: logging, settings, database, sessions.
  * First-run onboarding, then the main window.
  * Global exception handling that keeps the app alive where possible.
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from veyrion_workspace import APP_NAME, APP_SLUG, ORG_NAME, __version__


def _setup_high_dpi() -> None:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)


def _bootstrap_services():
    """Initialize logging, settings, and database; returns (settings, db,
    tasks, journal)."""
    from veyrion_workspace.app import paths
    from veyrion_workspace.app.logging_setup import prune_old_logs, setup_logging
    from veyrion_workspace.services.settings import Settings
    from veyrion_workspace.services.tasks import TaskManager
    from veyrion_workspace.services.recovery import SessionJournal
    from veyrion_workspace.storage.database import Database

    paths.ensure_all()
    setup_logging(verbose=bool(os.environ.get("VEYRION_VERBOSE")))
    prune_old_logs()

    settings = Settings()
    if settings.restored_defaults:
        pass  # caller surfaces a toast once the window exists

    db = Database()
    db.integrity_check()
    tasks = TaskManager()
    journal = SessionJournal(paths.sessions_dir())
    return settings, db, tasks, journal


class _SingleInstanceGuard:
    """Qt LocalServer-based guard (no extra dependencies, cross-platform)."""

    def __init__(self) -> None:
        from PySide6.QtNetwork import QLocalServer, QLocalSocket
        self._server = None
        self._socket = None
        self.server_name = f"{APP_SLUG.lower()}-{__import__('getpass').getuser()}"
        self.is_primary = True
        self._LocalServer = QLocalServer
        self._LocalSocket = QLocalSocket

    def try_acquire(self) -> bool:
        """Returns True when this process is the primary instance."""
        probe = self._LocalSocket()
        probe.connectToServer(self.server_name)
        if probe.waitForConnected(200):
            # Another instance is running; forward our args and exit.
            self._socket = probe
            self.is_primary = False
            try:
                self._socket.write(
                    " ".join(f'"{a}"' for a in sys.argv[1:]).encode("utf-8"))
                self._socket.flush()
                self._socket.waitForBytesWritten(300)
            except Exception:
                pass
            return False
        # No server — become primary.
        self._LocalServer.removeServer(self.server_name)
        self._server = self._LocalServer()
        self._server.setSocketOptions(
            self._LocalServer.SocketOption.UserAccessOption)
        if not self._server.listen(self.server_name):
            # Race lost; another instance started first.
            self.is_primary = False
            return False
        self._server.newConnection.connect(self._forward)
        return True

    def _forward(self) -> None:
        try:
            conn = self._server.nextPendingConnection()
            if conn is not None:
                payload = bytes(conn.readAll()).decode("utf-8")
                conn.disconnectFromServer()
                self._on_forwarded(payload)
        except Exception:
            pass

    def _on_forwarded(self, payload: str) -> None:
        """Re-emitted on the main window via a queued call."""
        from PySide6.QtCore import QMetaObject, Qt as Qt2, Q_ARG, QTimer
        window = QApplication.instance().activeWindow()
        if window is not None and hasattr(window, "open_path"):
            for token in payload.split():
                path = token.strip("\"'")
                if path:
                    QTimer.singleShot(0, lambda p=path: window.open_path(p))


def _install_exception_hook() -> None:
    def hook(exc_type, exc_value, exc_tb):
        try:
            from veyrion_workspace.app.logging_setup import logger  # noqa
            import logging
            logging.getLogger("veyrion").error(
                "unhandled exception: %s\n%s",
                exc_value, "".join(traceback.format_tb(exc_tb)))
        except Exception:
            traceback.print_exception(exc_type, exc_value, exc_tb)
        # Keep the app running where possible; Qt dialogs recover better
        # than dying silently.
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        try:
            app = QApplication.instance()
            if app is not None:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.critical(
                    None, APP_NAME,
                    f"Something went wrong:\n\n{exc_value}\n\n"
                    f"The error was logged. You can continue working.")
        except Exception:
            pass
    sys.excepthook = hook


def run(argv: list | None = None) -> int:
    _setup_high_dpi()
    _install_exception_hook()

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(ORG_NAME)
    app.setQuitOnLastWindowClosed(True)

    guard = _SingleInstanceGuard()
    if not guard.try_acquire():
        return 0  # forwarded to the running instance

    from veyrion_workspace.app import paths
    from veyrion_workspace.app.logging_setup import setup_logging
    from veyrion_workspace.ui.icons import make_window_icon
    app.setWindowIcon(make_window_icon())

    settings, db, tasks, journal = _bootstrap_services()

    # Smoke-test mode: VEYRION_SMOKE=1 quits cleanly after a short run,
    # used by CI and by build validation to prove the app boots and exits.
    smoke_seconds = os.environ.get("VEYRION_SMOKE", "")
    if smoke_seconds:
        from PySide6.QtCore import QTimer as _QT
        _QT.singleShot(int(smoke_seconds) * 1000, app.quit)
        settings.set("general", "first_run_complete", True)

    from veyrion_workspace.ui.main_window import MainWindow
    window = MainWindow(settings, db, tasks, journal)
    window.show()

    if settings.restored_defaults:
        from veyrion_workspace.ui.widgets import show_toast
        show_toast(window,
                   "Your settings file was damaged — defaults were restored "
                   "and a backup was kept.", "warning")

    # First-run onboarding.
    if not settings.get("general", "first_run_complete", False):
        from veyrion_workspace.ui.onboarding import OnboardingDialog
        if OnboardingDialog(window, settings).exec():
            from veyrion_workspace.ui.theme import build_qss, get_palette
            app.setStyleSheet(build_qss(
                get_palette(settings.get("appearance", "theme", "light"),
                            settings.get("appearance", "accent", ""))))
            folders = settings.get("general", "watch_folders", [])
            for folder in folders:
                if Path(folder).exists():
                    window.library_panel.import_folder(Path(folder))
            window.library_panel.reload()

    # Crash recovery — restore the previous session when appropriate.
    try:
        window.restore_session()
    except Exception:
        import logging
        logging.getLogger("veyrion").exception("session restore failed")

    # Command-line files (double-click associations / drag onto exe).
    for arg in sys.argv[1:]:
        if arg.lower().startswith("veyrion-smoke-test"):
            continue
        window.open_path(arg)

    # (The crash-journal heartbeat lives on the MainWindow itself, parented
    # to it, so no orphan timer is created here.)

    # Clean temp files from previous runs.
    try:
        from veyrion_workspace.utils.safeio import cleanup_temps
        cleanup_temps(max_age_hours=24)
    except Exception:
        pass

    exit_code = app.exec()
    tasks.shutdown(wait=False)
    try:
        db.close()
    except Exception:
        pass
    return exit_code


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()