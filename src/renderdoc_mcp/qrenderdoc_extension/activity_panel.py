"""Qt activity window for renderdoc-mcp bridge operations."""

from PySide2 import QtCore, QtGui, QtWidgets
import qrenderdoc as qrd


_open_panels = []


def show_activity_panel(ctx, store):
    for panel in list(_open_panels):
        ctx.RaiseDockWindow(panel)
        panel.refresh()
        return panel

    panel = ActivityPanel(store)
    panel.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
    panel.destroyed.connect(lambda *_: _release_panel(panel))
    _open_panels.append(panel)
    ctx.AddDockWindow(panel, qrd.DockReference.BottomWindowSide, None, 0.25)
    ctx.RaiseDockWindow(panel)
    return panel


def _release_panel(panel):
    if panel in _open_panels:
        _open_panels.remove(panel)


class ActivityPanel(QtWidgets.QWidget):
    """Dockable window that shows recent bridge activity."""

    def __init__(self, store):
        super(ActivityPanel, self).__init__()
        self.store = store
        self.setObjectName("renderdocMcpActivity")
        self.setWindowTitle("RenderDoc MCP Activity")
        self.setMinimumSize(720, 260)
        self._build_ui()
        self.store.add_listener(self._store_changed)
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(500)
        self.refresh()

    def closeEvent(self, event):
        self.store.remove_listener(self._store_changed)
        super(ActivityPanel, self).closeEvent(event)

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        toolbar = QtWidgets.QHBoxLayout()
        self.filter = QtWidgets.QComboBox()
        self.filter.addItems(["All", "Running", "Success", "Error"])
        self.filter.currentIndexChanged.connect(self.refresh)
        refresh = QtWidgets.QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        clear = QtWidgets.QPushButton("Clear")
        clear.clicked.connect(self.clear)
        copy_button = QtWidgets.QPushButton("Copy")
        copy_button.clicked.connect(self.copy_selected)
        toolbar.addWidget(QtWidgets.QLabel("Status"))
        toolbar.addWidget(self.filter)
        toolbar.addStretch(1)
        toolbar.addWidget(refresh)
        toolbar.addWidget(clear)
        toolbar.addWidget(copy_button)
        layout.addLayout(toolbar)

        self.table = QtWidgets.QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Time", "Status", "Operation", "RDC", "EID", "Duration", "Summary"]
        )
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 145)
        self.table.setColumnWidth(1, 78)
        self.table.setColumnWidth(2, 220)
        self.table.setColumnWidth(3, 250)
        self.table.setColumnWidth(4, 60)
        self.table.setColumnWidth(5, 80)
        layout.addWidget(self.table)

        self.details = QtWidgets.QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(115)
        layout.addWidget(self.details)
        self.table.itemSelectionChanged.connect(self._show_details)

    def refresh(self):
        selected_id = self._selected_id()
        wanted = self.filter.currentText().lower()
        entries = [
            item
            for item in self.store.snapshot()
            if wanted == "all" or item.get("status") == wanted
        ]
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(reversed(entries)):
            values = [
                entry.get("timestamp", ""),
                entry.get("status", ""),
                entry.get("operation", ""),
                entry.get("filepath") or "",
                str(entry.get("event_id") or ""),
                _duration(entry.get("duration_ms")),
                entry.get("message", ""),
            ]
            for column, value in enumerate(values):
                cell = QtWidgets.QTableWidgetItem(str(value))
                cell.setData(QtCore.Qt.UserRole, entry.get("id"))
                if column == 1:
                    cell.setForeground(_status_brush(entry.get("status")))
                self.table.setItem(row, column, cell)
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0) and self.table.item(row, 0).data(QtCore.Qt.UserRole) == selected_id:
                self.table.selectRow(row)
                break
        if self.table.rowCount() and not self.table.selectedItems():
            self.table.selectRow(0)

    def clear(self):
        self.store.clear()
        self.details.clear()

    def copy_selected(self):
        row = self.table.currentRow()
        if row < 0:
            return
        values = [self.table.item(row, column).text() for column in range(self.table.columnCount())]
        QtWidgets.QApplication.clipboard().setText("\t".join(values))

    def _selected_id(self):
        item = self.table.item(self.table.currentRow(), 0)
        return item.data(QtCore.Qt.UserRole) if item else None

    def _show_details(self):
        selected_id = self._selected_id()
        entry = next((item for item in self.store.snapshot() if item.get("id") == selected_id), None)
        if not entry:
            self.details.clear()
            return
        self.details.setPlainText(_format_details(entry))

    def _store_changed(self):
        self.refresh()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents)


def _duration(value):
    return "%sms" % value if value is not None else "-"


def _status_brush(status):
    colors = {"running": "#d6a84f", "success": "#61c28a", "error": "#e06c75"}
    return QtGui.QBrush(QtGui.QColor(colors.get(status, "#c8c8c8")))


def _format_details(entry):
    lines = [
        "Operation: %s" % entry.get("operation", ""),
        "Status: %s" % entry.get("status", ""),
    ]
    if entry.get("filepath"):
        lines.append("RDC: %s" % entry["filepath"])
    if entry.get("event_id") is not None:
        lines.append("EID: %s" % entry["event_id"])
    if entry.get("duration_ms") is not None:
        lines.append("Duration: %sms" % entry["duration_ms"])
    if entry.get("details"):
        lines.append("Details: %s" % entry["details"])
    return "\n".join(lines)
