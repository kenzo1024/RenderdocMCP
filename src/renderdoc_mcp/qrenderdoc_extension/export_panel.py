"""Manual resource export panel for qrenderdoc."""

import os
import sys

import renderdoc as rd
from PySide2 import QtCore, QtWidgets

from .services import BridgeServices, BridgeSession, _ensure_project_src


_open_panels = []


def show_export_panel(ctx):
    panel = ResourceExportPanel(ctx)
    panel.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
    panel.destroyed.connect(lambda *_: _release_panel(panel))
    _open_panels.append(panel)
    panel.show()
    panel.raise_()
    panel.activateWindow()


def _release_panel(panel):
    if panel in _open_panels:
        _open_panels.remove(panel)


class ResourceExportPanel(QtWidgets.QDialog):
    """Small non-modal panel for manual asset export."""

    def __init__(self, ctx):
        super(ResourceExportPanel, self).__init__(None)
        _ensure_project_src()
        from renderdoc_mcp.resource_export import preset, schema

        self.ctx = ctx
        self.schema = schema
        self.preset = preset
        self.preset_dir = schema.default_preset_dir()
        self.headers = {schema.VSIN: [], schema.VSOUT: []}
        self.setWindowTitle("RenderDoc MCP Resource Export")
        self.setWindowModality(QtCore.Qt.NonModal)
        self.resize(620, 540)
        self._build_ui()
        self.refresh_presets()
        self.reset_mapping()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        form = QtWidgets.QFormLayout()
        self.event_id = QtWidgets.QLineEdit(str(self._guess_event_id() or ""))
        self.output_dir = QtWidgets.QLineEdit()
        self.prefix = QtWidgets.QLineEdit("asset")

        output_row = QtWidgets.QHBoxLayout()
        browse = QtWidgets.QPushButton("Browse")
        browse.clicked.connect(self.browse_output)
        output_row.addWidget(self.output_dir)
        output_row.addWidget(browse)

        form.addRow("Event ID", self.event_id)
        form.addRow("Output Dir", output_row)
        form.addRow("Prefix", self.prefix)
        layout.addLayout(form)

        preset_row = QtWidgets.QHBoxLayout()
        self.preset_name = QtWidgets.QLineEdit()
        self.preset_combo = QtWidgets.QComboBox()
        self.preset_combo.currentIndexChanged.connect(self.select_preset)
        load = QtWidgets.QPushButton("Load")
        save = QtWidgets.QPushButton("Save")
        load.clicked.connect(self.load_preset)
        save.clicked.connect(self.save_preset)
        preset_row.addWidget(QtWidgets.QLabel("Preset"))
        preset_row.addWidget(self.preset_combo)
        preset_row.addWidget(self.preset_name)
        preset_row.addWidget(load)
        preset_row.addWidget(save)
        layout.addLayout(preset_row)

        transform_row = QtWidgets.QHBoxLayout()
        self.face_winding = QtWidgets.QComboBox()
        self.face_winding.addItems(["Keep", "Reverse"])
        self.face_winding.setCurrentIndex(1)
        self.axis_x = self._axis_combo("+Y")
        self.axis_y = self._axis_combo("+Z")
        self.axis_z = self._axis_combo("+X")
        self.flip_uv_v = QtWidgets.QCheckBox("Flip UV V")
        self.flip_uv_v.setChecked(True)
        transform_row.addWidget(QtWidgets.QLabel("Face"))
        transform_row.addWidget(self.face_winding)
        transform_row.addWidget(QtWidgets.QLabel("Axis X"))
        transform_row.addWidget(self.axis_x)
        transform_row.addWidget(QtWidgets.QLabel("Y"))
        transform_row.addWidget(self.axis_y)
        transform_row.addWidget(QtWidgets.QLabel("Z"))
        transform_row.addWidget(self.axis_z)
        transform_row.addWidget(self.flip_uv_v)
        layout.addLayout(transform_row)

        self.mapping_table = QtWidgets.QTableWidget()
        self.mapping_table.setColumnCount(3)
        self.mapping_table.setHorizontalHeaderLabels(["FBX Column", "Source", "Header"])
        self.mapping_table.verticalHeader().setVisible(False)
        self.mapping_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.mapping_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.mapping_table.setColumnWidth(0, 150)
        self.mapping_table.setColumnWidth(1, 90)
        self.mapping_table.setColumnWidth(2, 300)
        layout.addWidget(self.mapping_table)

        button_row = QtWidgets.QHBoxLayout()
        refresh = QtWidgets.QPushButton("Refresh Headers")
        reset = QtWidgets.QPushButton("Reset")
        add = QtWidgets.QPushButton("Add")
        delete = QtWidgets.QPushButton("Delete")
        export = QtWidgets.QPushButton("Export")
        refresh.clicked.connect(self.refresh_headers)
        reset.clicked.connect(self.reset_mapping)
        add.clicked.connect(self.add_mapping)
        delete.clicked.connect(self.delete_mapping)
        export.clicked.connect(self.export_asset)
        button_row.addWidget(refresh)
        button_row.addWidget(reset)
        button_row.addWidget(add)
        button_row.addWidget(delete)
        button_row.addStretch(1)
        button_row.addWidget(export)
        layout.addLayout(button_row)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(100)
        layout.addWidget(self.log)

    def _axis_combo(self, value):
        combo = QtWidgets.QComboBox()
        combo.addItems(["+X", "-X", "+Y", "-Y", "+Z", "-Z"])
        combo.setCurrentText(value)
        combo.setFixedWidth(58)
        return combo

    def _guess_event_id(self):
        for name in ("CurEvent", "GetEventID", "CurrentEvent"):
            value = getattr(self.ctx, name, None)
            try:
                return value() if callable(value) else value
            except Exception:
                pass
        return ""

    def browse_output(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Output Dir", self.output_dir.text())
        if path:
            self.output_dir.setText(path)

    def refresh_presets(self):
        names = self.preset.list_presets(self.preset_dir)
        current = self.preset_combo.currentText()
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItems(names)
        if current:
            index = self.preset_combo.findText(current)
            if index >= 0:
                self.preset_combo.setCurrentIndex(index)
        self.preset_combo.blockSignals(False)
        self.select_preset()

    def select_preset(self):
        if self.preset_combo.currentText():
            self.preset_name.setText(self.preset_combo.currentText())

    def refresh_headers(self):
        try:
            event_id = self._event_id()
            headers = {"vsin": [], "vsout": []}

            def callback(controller):
                from renderdoc_mcp.resource_export.asset_export import decode_mesh_data

                session = BridgeSession(controller)
                action = session.get_action(event_id)
                if action is None:
                    raise ValueError("Event ID not found: %s" % event_id)
                mesh_data, exports = decode_mesh_data(session, action, event_id, 1)
                headers["vsin"] = exports["vsin"]["headers"]
                headers["vsout"] = exports["vsout"]["headers"]

            self.ctx.Replay().BlockInvoke(callback)
            self.headers = headers
            self._refresh_header_columns()
            self._log("Headers refreshed.")
        except Exception as exc:
            self._error(str(exc))

    def reset_mapping(self):
        config = self.schema.default_export_config()
        self.apply_config(config)

    def add_mapping(self):
        self._add_mapping_row("", self.schema.VSIN, "")

    def delete_mapping(self):
        row = self.mapping_table.currentRow()
        if row >= 0:
            self.mapping_table.removeRow(row)

    def load_preset(self):
        name = self.preset_combo.currentText() or self.preset_name.text()
        try:
            self.apply_config(self.preset.load_preset(name, self.preset_dir))
            self._log("Preset loaded: %s" % name)
        except Exception as exc:
            self._error(str(exc))

    def save_preset(self):
        name = self.preset_name.text()
        try:
            path = self.preset.save_preset(name, self.collect_config(), self.preset_dir)
            self.refresh_presets()
            self._log("Preset saved: %s" % path)
        except Exception as exc:
            self._error(str(exc))

    def export_asset(self):
        try:
            event_id = self._event_id()
            output_dir = self.output_dir.text().strip()
            if not output_dir:
                raise ValueError("Output Dir is empty.")

            services = BridgeServices(self.ctx)
            result = services.export_resource_asset({
                "event_id": event_id,
                "output_dir": output_dir,
                "prefix": self.prefix.text().strip() or "asset",
                "config": self.collect_config(),
                "include_textures": True,
                "include_render_targets": False,
            })
            if isinstance(result, dict) and result.get("error"):
                raise ValueError(result.get("error"))
            self._log("Export complete: %s" % result.get("bundle_dir", output_dir))
            QtWidgets.QMessageBox.information(self, "Export Complete", result.get("bundle_dir", output_dir))
        except Exception as exc:
            self._error(str(exc))

    def collect_config(self):
        config = {
            self.schema.ATTRIBUTE_MAPPINGS: self.collect_mappings(),
            self.schema.FACE_WINDING: self.schema.FACE_WINDING_REVERSE if self.face_winding.currentIndex() == 1 else self.schema.FACE_WINDING_KEEP,
            self.schema.AXIS_X: self.axis_x.currentText(),
            self.schema.AXIS_Y: self.axis_y.currentText(),
            self.schema.AXIS_Z: self.axis_z.currentText(),
            self.schema.FLIP_UV_V: self.flip_uv_v.isChecked(),
        }
        self.schema.apply_export_flags(config)
        return config

    def collect_mappings(self):
        rows = []
        for row_index in range(self.mapping_table.rowCount()):
            target = self.mapping_table.cellWidget(row_index, 0)
            source = self.mapping_table.cellWidget(row_index, 1)
            header = self.mapping_table.cellWidget(row_index, 2)
            rows.append({
                self.schema.TARGET_COLUMN: target.currentText() if target else "",
                self.schema.SOURCE_STAGE: source.currentData() if source else self.schema.VSIN,
                self.schema.SOURCE_COLUMN: header.currentText() if header else "",
            })
        return rows

    def apply_config(self, config):
        config = self.schema.normalize_config(config)
        self.face_winding.setCurrentIndex(1 if config.get(self.schema.FACE_WINDING) == self.schema.FACE_WINDING_REVERSE else 0)
        self.axis_x.setCurrentText(config.get(self.schema.AXIS_X, "+Y"))
        self.axis_y.setCurrentText(config.get(self.schema.AXIS_Y, "+Z"))
        self.axis_z.setCurrentText(config.get(self.schema.AXIS_Z, "+X"))
        self.flip_uv_v.setChecked(config.get(self.schema.FLIP_UV_V, True))
        self.mapping_table.setRowCount(0)
        for item in config.get(self.schema.ATTRIBUTE_MAPPINGS, []):
            self._add_mapping_row(
                item.get(self.schema.TARGET_COLUMN, ""),
                item.get(self.schema.SOURCE_STAGE, self.schema.VSIN),
                item.get(self.schema.SOURCE_COLUMN, ""),
            )

    def _add_mapping_row(self, target_column, source_stage, source_column):
        row_index = self.mapping_table.rowCount()
        self.mapping_table.insertRow(row_index)

        target = QtWidgets.QComboBox()
        target.addItems(self.schema.TARGET_COLUMNS)
        target.setEditable(True)
        target.setCurrentText(target_column)

        source = QtWidgets.QComboBox()
        source.addItem("VSIn", self.schema.VSIN)
        source.addItem("VSOut", self.schema.VSOUT)
        source.setCurrentIndex(1 if source_stage == self.schema.VSOUT else 0)

        header = QtWidgets.QComboBox()
        header.setEditable(True)
        self._fill_header_combo(header, source.currentData(), source_column)
        source.currentIndexChanged.connect(lambda *_: self._fill_header_combo(header, source.currentData(), header.currentText()))

        self.mapping_table.setCellWidget(row_index, 0, target)
        self.mapping_table.setCellWidget(row_index, 1, source)
        self.mapping_table.setCellWidget(row_index, 2, header)

    def _fill_header_combo(self, combo, source_stage, current):
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(self.headers.get(source_stage, []))
        if current and combo.findText(current) < 0:
            combo.addItem(current)
        if current:
            combo.setCurrentText(current)
        combo.blockSignals(False)

    def _refresh_header_columns(self):
        for row_index in range(self.mapping_table.rowCount()):
            source = self.mapping_table.cellWidget(row_index, 1)
            header = self.mapping_table.cellWidget(row_index, 2)
            if source and header:
                self._fill_header_combo(header, source.currentData(), header.currentText())

    def _event_id(self):
        text = self.event_id.text().strip()
        if not text:
            raise ValueError("Event ID is empty.")
        return int(text)

    def _log(self, message):
        self.log.appendPlainText(str(message))

    def _error(self, message):
        self._log("ERROR: %s" % message)
        QtWidgets.QMessageBox.critical(self, "Export Failed", str(message))
