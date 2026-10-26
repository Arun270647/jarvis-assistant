"""The window's web view on Windows (and Linux): Qt WebEngine, with the page's QWebChannel bridge. The Mac build uses
the system's WebKit instead (src/gui/webkit_view.py); both offer make_view() with the same arguments."""
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView


class WebPage(QWebEnginePage):
    """Routes page console messages to the log so UI errors are diagnosable."""

    def javaScriptConsoleMessage(self, level, message, line, source):
        tag = {0: "INFO", 1: "WARN", 2: "ERROR"}.get(int(level.value) if hasattr(level, "value") else int(level), "INFO")
        print(f"[UI {tag}] {Path(source).name}:{line} {message}")


def make_view(parent, bridge, page: Path, transparent: bool = False) -> QWebEngineView:
    view = QWebEngineView(parent)
    web_page = WebPage(view)
    view.setPage(web_page)
    s = web_page.settings()
    s.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
    s.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, False)
    view.setContextMenuPolicy(Qt.NoContextMenu)
    web_page.setBackgroundColor(Qt.transparent if transparent else QColor("#1d2d3d"))
    channel = QWebChannel(web_page)
    channel.registerObject("jarvis", bridge)
    web_page.setWebChannel(channel)
    view.setUrl(QUrl.fromLocalFile(str(page)))
    return view
