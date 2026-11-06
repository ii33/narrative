"""Observe double clicks only over registered read-only block display items."""
from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtQuick import QQuickItem, QQuickWindow
from shiboken6 import getCppPointer, isValid


class ReadOnlyBlockTextEvents(QObject):
    doubleClicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = {}
        self._windows = {}
        self._prune_pending = False

    def observe(self, item: QObject) -> None:
        if not isinstance(item, QQuickItem):
            return
        window = item.window()
        if window is None:
            return
        key = getCppPointer(item)[0]
        if key not in self._items:
            self._items[key] = item
        if not self._prune_pending:
            self._prune_pending = True
            QTimer.singleShot(0, self._prune)
        window_key = getCppPointer(window)[0]
        if window_key not in self._windows:
            self._windows[window_key] = window
            window.installEventFilter(self)


    def _prune(self):
        # Release invalid wrappers after Qt has finished destroying their items.
        self._prune_pending = False
        self._items = {key: item for key, item in self._items.items() if isValid(item)}

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (not isinstance(watched, QQuickWindow) or not isinstance(event, QMouseEvent)
                or event.type() != QEvent.Type.MouseButtonDblClick
                or event.button() != Qt.MouseButton.LeftButton):
            return False
        focus = watched.activeFocusItem()
        while focus is not None:
            if "Popup" in focus.metaObject().className():
                return False
            focus = focus.parentItem()
        point = event.position()
        for item in tuple(self._items.values()):
            if not isValid(item) or item.window() != watched or not item.contains(item.mapFromScene(point)):
                continue
            ancestor = item
            visible = True
            while ancestor is not None:
                if not ancestor.isVisible() or not ancestor.isEnabled() or (ancestor.clip() and not ancestor.contains(ancestor.mapFromScene(point))):
                    visible = False
                    break
                ancestor = ancestor.parentItem()
            if (visible and item.objectName() == "scriptBlockDisplayText"
                    and item.property("readOnly") is True):
                self.doubleClicked.emit(str(item.property("blockId") or ""))
                break
        return False
