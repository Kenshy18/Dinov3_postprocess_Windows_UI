from __future__ import annotations

import json
import os
import shlex
import sys
import time
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets

from .path_utils import VIDEO_EXTS, clean_run_part, normalize_windows_path, windows_path_to_wsl, wsl_path_to_unc
from .process_utils import hidden_windows_process_command
from .progress import format_duration, parse_progress_line
from .progress_view import ProgressDashboard, phase_key, phase_label, progress_float
from .settings import AppSettings
from .widgets import ClosingComboBox
from .wsl_bridge import WslBridge, WslRuntime


DEFAULT_RAW_REMOVE_SHORT_TRACKS_MAX_FRAMES = 10
DEFAULT_OVERLAY_ENCODER = "nvenc"


def timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def as_command_text(command: list[str]) -> str:
    return " ".join(f'"{part}"' if " " in str(part) else str(part) for part in command)


class PipelineUiWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = AppSettings.load()
        if not self.settings.windows_output_dir:
            self.settings.windows_output_dir = str(Path.home() / "Videos" / "Dinov3PostprocessRuns")
        self.bridge = WslBridge(self.settings.wsl_distro, self.settings.wsl_repo_path)
        self.runtime: WslRuntime | None = None

        self.setWindowTitle("SOD推論システム - Windows Frontend")
        self.resize(960, 800)
        self.setMinimumSize(880, 700)

        self.queue_paths: list[Path] = []
        self.run_queue: list[Path] = []
        self.current_index = -1
        self.current_run_name = ""
        self.current_summary_path: Path | None = None
        self.process: QtCore.QProcess | None = None
        self.process_start_time = 0.0
        self.active_log_buffer = ""
        self.stopping = False
        self.workflow_running = False
        self.queue_start_time = 0.0
        self.progress_phase_weights: dict[str, float] = {}
        self.progress_phase_offsets: dict[str, float] = {}
        self.active_progress_phase_key = ""
        self.last_overall_percent = 0.0
        self.last_built_run_name = ""
        self.last_built_staging_output_root_wsl = ""
        self.last_built_staging_run_dir_wsl = ""
        self.last_built_final_run_dir_wsl = ""

        self.elapsed_timer = QtCore.QTimer(self)
        self.elapsed_timer.setInterval(1000)
        self.elapsed_timer.timeout.connect(self.update_elapsed)

        self.build_ui()
        self.apply_style()
        self.refresh_wsl_distros()
        self.discover_runtime(silent=True)
        self.load_runtime(silent=True)
        self.update_queue_state()
        self.update_running_state(False)

    def build_ui(self) -> None:
        central = QtWidgets.QWidget()
        central.setObjectName("centralRoot")
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)
        self.setCentralWidget(central)

        root.addWidget(self.build_connection_panel())
        root.addWidget(self.build_run_settings())

        body = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        body.setObjectName("bodySplitter")
        body.setChildrenCollapsible(False)
        body.setHandleWidth(8)
        root.addWidget(body, 1)

        upper = QtWidgets.QWidget()
        upper.setMinimumHeight(220)
        upper_layout = QtWidgets.QHBoxLayout(upper)
        upper_layout.setContentsMargins(0, 0, 0, 0)
        upper_layout.setSpacing(10)
        upper_layout.addWidget(self.build_queue_panel(), 7)
        upper_layout.addWidget(self.build_status_panel(), 11)
        body.addWidget(upper)

        lower = QtWidgets.QWidget()
        lower.setMinimumHeight(200)
        lower_layout = QtWidgets.QHBoxLayout(lower)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.setSpacing(10)
        lower_layout.addWidget(self.build_log_panel(), 8)
        lower_layout.addWidget(self.build_postprocess_panel(), 10)
        body.addWidget(lower)
        body.setStretchFactor(0, 5)
        body.setStretchFactor(1, 4)
        body.setSizes([400, 320])

    def build_connection_panel(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("WSL接続")
        grid = QtWidgets.QGridLayout(box)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.wsl_combo = ClosingComboBox()
        self.wsl_combo.setEditable(True)
        self.wsl_combo.setEditText(self.settings.wsl_distro)
        self.wsl_combo.setMinimumWidth(160)
        self.refresh_wsl_button = QtWidgets.QPushButton("WSL一覧更新")
        self.refresh_wsl_button.clicked.connect(self.refresh_wsl_distros)
        self.discover_runtime_button = QtWidgets.QPushButton("自動探索")
        self.discover_runtime_button.clicked.connect(lambda: self.discover_runtime(silent=False))
        self.wsl_repo_edit = QtWidgets.QLineEdit(self.settings.wsl_repo_path)
        self.test_connection_button = QtWidgets.QPushButton("接続確認")
        self.test_connection_button.setObjectName("secondaryButton")
        self.test_connection_button.clicked.connect(lambda: self.load_runtime(silent=False))
        self.runtime_info_label = QtWidgets.QLabel("未確認")
        self.runtime_info_label.setObjectName("runtimeInfo")
        self.runtime_info_label.setWordWrap(True)
        self.runtime_info_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        grid.addWidget(self._form_label("Distro"), 0, 0)
        grid.addWidget(self.wsl_combo, 0, 1)
        grid.addWidget(self.refresh_wsl_button, 0, 2)
        grid.addWidget(self.discover_runtime_button, 0, 3)
        grid.addWidget(self._form_label("WSL repo"), 1, 0)
        grid.addWidget(self.wsl_repo_edit, 1, 1)
        grid.addWidget(self.test_connection_button, 1, 2, 1, 2)
        grid.addWidget(self._form_label("Runtime"), 2, 0, QtCore.Qt.AlignTop)
        grid.addWidget(self.runtime_info_label, 2, 1, 1, 3)
        grid.setColumnStretch(1, 1)
        return box

    def _form_label(self, text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setObjectName("formLabel")
        return label

    def build_run_settings(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("実行設定")
        root = QtWidgets.QVBoxLayout(box)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(10)

        form = QtWidgets.QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        self.output_edit = QtWidgets.QLineEdit(self.settings.windows_output_dir)
        self.output_browse_button = QtWidgets.QPushButton("参照…")
        self.output_browse_button.clicked.connect(self.browse_output_dir)
        self.detector_combo = ClosingComboBox()
        self.detector_combo.addItem("DINOv3", "dinov3")
        self.detector_combo.addItem("EVA02", "eva02")
        self.detector_combo.addItem("Co-DINO", "codino")
        self.detector_combo.setCurrentIndex(1)
        self.detector_combo.setMinimumWidth(140)

        form.addWidget(self._form_label("Backend"), 0, 0)
        form.addWidget(self.detector_combo, 0, 1)
        form.addWidget(self._form_label("結果保存先"), 0, 2)
        form.addWidget(self.output_edit, 0, 3)
        form.addWidget(self.output_browse_button, 0, 4)
        form.setColumnStretch(3, 1)
        root.addLayout(form)

        overlay_row = QtWidgets.QHBoxLayout()
        overlay_row.setSpacing(12)
        overlay_label = QtWidgets.QLabel("オーバーレイ")
        overlay_label.setObjectName("sectionLabel")
        self.detailed_overlay_check = QtWidgets.QCheckBox("詳細")
        self.detector_overlay_check = QtWidgets.QCheckBox("AI生成カバー")
        self.simple_overlay_check = QtWidgets.QCheckBox("簡易")
        self.detailed_overlay_check.setChecked(True)
        overlay_row.addWidget(overlay_label)
        overlay_row.addWidget(self.detailed_overlay_check)
        overlay_row.addWidget(self.detector_overlay_check)
        overlay_row.addWidget(self.simple_overlay_check)
        overlay_row.addStretch(1)
        root.addLayout(overlay_row)

        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(8)
        self.check_artifacts_button = QtWidgets.QPushButton("チェックポイント確認")
        self.check_artifacts_button.setObjectName("secondaryButton")
        self.check_artifacts_button.clicked.connect(self.check_artifacts)
        self.advanced_button = QtWidgets.QToolButton()
        self.advanced_button.setObjectName("advancedToggle")
        self.advanced_button.setText("詳細設定を開く")
        self.advanced_button.setCheckable(True)
        self.advanced_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.advanced_button.toggled.connect(self.toggle_advanced)
        self.start_button = QtWidgets.QPushButton("推論開始")
        self.start_button.setObjectName("startButton")
        self.start_button.setMinimumWidth(110)
        self.start_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.start_button.clicked.connect(self.start_queue)
        self.stop_button = QtWidgets.QPushButton("停止")
        self.stop_button.setObjectName("stopButton")
        self.stop_button.setMinimumWidth(80)
        self.stop_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.stop_button.clicked.connect(self.stop_process)
        actions.addWidget(self.check_artifacts_button)
        actions.addWidget(self.advanced_button)
        actions.addStretch(1)
        actions.addWidget(self.start_button)
        actions.addWidget(self.stop_button)
        root.addLayout(actions)

        self.advanced_box = self.build_advanced_box()
        self.advanced_box.setVisible(False)
        root.addWidget(self.advanced_box)
        return box

    def build_advanced_box(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("詳細設定")
        box.setObjectName("advancedBox")
        outer = QtWidgets.QVBoxLayout(box)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)

        self.run_prefix_edit = QtWidgets.QLineEdit(self.settings.run_prefix)
        self.force_check = QtWidgets.QCheckBox("既存結果を上書き")
        self.force_check.setChecked(True)
        self.recursive_check = QtWidgets.QCheckBox("フォルダ入力を再帰検索")
        self.raw_cut_detect_check = QtWidgets.QCheckBox("カット検出")
        self.raw_cut_detect_check.setChecked(True)
        self.max_frames_spin = QtWidgets.QSpinBox()
        self.max_frames_spin.setRange(0, 100_000_000)
        self.max_frames_spin.setSpecialValueText("既定")
        self.max_frames_spin.setMinimumWidth(96)
        self.batch_size_spin = QtWidgets.QSpinBox()
        self.batch_size_spin.setRange(0, 4096)
        self.batch_size_spin.setSpecialValueText("既定")
        self.batch_size_spin.setMinimumWidth(96)
        self.warmup_spin = QtWidgets.QSpinBox()
        self.warmup_spin.setRange(-1, 100_000)
        self.warmup_spin.setValue(-1)
        self.warmup_spin.setSpecialValueText("既定")
        self.warmup_spin.setMinimumWidth(96)
        self.score_enable = QtWidgets.QCheckBox("score-thresh指定")
        self.score_spin = QtWidgets.QDoubleSpinBox()
        self.score_spin.setRange(0.0, 1.0)
        self.score_spin.setSingleStep(0.01)
        self.score_spin.setDecimals(3)
        self.score_spin.setValue(0.300)
        self.score_spin.setEnabled(False)
        self.score_spin.setMinimumWidth(96)
        self.score_enable.toggled.connect(self.score_spin.setEnabled)

        prefix_row = QtWidgets.QHBoxLayout()
        prefix_row.setSpacing(8)
        prefix_row.addWidget(self._form_label("Run名Prefix"))
        prefix_row.addWidget(self.run_prefix_edit, 1)
        outer.addLayout(prefix_row)

        numeric_row = QtWidgets.QHBoxLayout()
        numeric_row.setSpacing(8)
        for label_text, widget in (
            ("最大フレーム", self.max_frames_spin),
            ("batch-size", self.batch_size_spin),
            ("warmup", self.warmup_spin),
        ):
            numeric_row.addWidget(self._form_label(label_text))
            numeric_row.addWidget(widget)
            numeric_row.addSpacing(4)
        numeric_row.addStretch(1)
        outer.addLayout(numeric_row)

        toggles_row = QtWidgets.QHBoxLayout()
        toggles_row.setSpacing(16)
        toggles_row.addWidget(self.force_check)
        toggles_row.addWidget(self.recursive_check)
        toggles_row.addWidget(self.raw_cut_detect_check)
        toggles_row.addStretch(1)
        outer.addLayout(toggles_row)

        score_row = QtWidgets.QHBoxLayout()
        score_row.setSpacing(8)
        score_row.addWidget(self.score_enable)
        score_row.addWidget(self.score_spin)
        score_row.addStretch(1)
        outer.addLayout(score_row)
        return box

    def build_queue_panel(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("入力動画キュー")
        layout = QtWidgets.QVBoxLayout(box)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        self.queue_stack = QtWidgets.QStackedWidget()
        self.empty_queue_label = QtWidgets.QLabel("動画がありません\n\n下のボタンから追加してください")
        self.empty_queue_label.setObjectName("emptyState")
        self.empty_queue_label.setAlignment(QtCore.Qt.AlignCenter)
        self.queue_list = QtWidgets.QListWidget()
        self.queue_list.setObjectName("queueList")
        self.queue_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.queue_list.setAlternatingRowColors(True)
        self.queue_list.setUniformItemSizes(True)
        self.queue_stack.addWidget(self.empty_queue_label)
        self.queue_stack.addWidget(self.queue_list)
        layout.addWidget(self.queue_stack, 1)
        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(6)
        self.add_video_button = QtWidgets.QPushButton("動画追加")
        self.add_video_button.clicked.connect(self.add_videos)
        self.add_folder_button = QtWidgets.QPushButton("フォルダ追加")
        self.add_folder_button.clicked.connect(self.add_folder)
        self.remove_button = QtWidgets.QPushButton("選択削除")
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button = QtWidgets.QPushButton("全削除")
        self.clear_button.setObjectName("dangerGhostButton")
        self.clear_button.clicked.connect(self.clear_queue)
        for button in (self.add_video_button, self.add_folder_button, self.remove_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self.clear_button)
        layout.addLayout(buttons)
        return box

    def build_status_panel(self) -> QtWidgets.QGroupBox:
        self.progress_dashboard = ProgressDashboard()
        self.progress_dashboard.bind_legacy_attributes(self)
        return self.progress_dashboard

    def build_log_panel(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("実行ログ")
        layout = QtWidgets.QVBoxLayout(box)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        self.log_edit = QtWidgets.QPlainTextEdit()
        self.log_edit.setObjectName("logEdit")
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumBlockCount(5000)
        self.log_edit.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.log_edit.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        layout.addWidget(self.log_edit)
        return box

    def build_postprocess_panel(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("自動後処理設定")
        layout = QtWidgets.QVBoxLayout(box)
        layout.setContentsMargins(10, 12, 10, 10)
        layout.setSpacing(8)
        self.postprocess_check = QtWidgets.QCheckBox("推論後に自動で後処理を実行")
        self.postprocess_check.setChecked(True)
        self.postprocess_check.toggled.connect(self.update_postprocess_enabled)
        self.class_tabs = QtWidgets.QTabWidget()
        self.class_tabs.setObjectName("classTabs")
        self.class_tabs.setDocumentMode(True)
        self.class_tabs.setElideMode(QtCore.Qt.ElideRight)
        self.class_tabs.setUsesScrollButtons(False)
        self.class_tabs.tabBar().setExpanding(False)
        self.class_shape_combos: dict[str, ClosingComboBox] = {}
        self.class_keyframe_spins: dict[str, QtWidgets.QSpinBox] = {}
        self.class_recall_spins: dict[str, QtWidgets.QDoubleSpinBox] = {}
        self.class_confidence_spins: dict[str, QtWidgets.QDoubleSpinBox] = {}
        for name in ("女性器", "男性器", "結合部分"):
            page = QtWidgets.QWidget()
            grid = QtWidgets.QGridLayout(page)
            grid.setContentsMargins(10, 12, 10, 10)
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(8)
            shape = ClosingComboBox()
            shape.addItem("楕円近似", "ellipse")
            shape.addItem("ポリゴン", "polygon")
            shape.setCurrentIndex(1 if name == "男性器" else 0)
            shape.setMinimumWidth(110)
            shape.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            keyframe = QtWidgets.QSpinBox()
            keyframe.setRange(1, 300)
            keyframe.setValue(3)
            keyframe.setMinimumWidth(80)
            keyframe.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            recall = QtWidgets.QDoubleSpinBox()
            recall.setRange(0.001, 1.0)
            recall.setDecimals(3)
            recall.setSingleStep(0.005)
            recall.setValue(0.960)
            recall.setMinimumWidth(80)
            recall.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            confidence = QtWidgets.QDoubleSpinBox()
            confidence.setRange(0.0, 1.0)
            confidence.setDecimals(3)
            confidence.setSingleStep(0.005)
            confidence.setValue(0.350)
            confidence.setMinimumWidth(80)
            confidence.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            self.class_shape_combos[name] = shape
            self.class_keyframe_spins[name] = keyframe
            self.class_recall_spins[name] = recall
            self.class_confidence_spins[name] = confidence
            grid.addWidget(self._form_label("マスクタイプ"), 0, 0)
            grid.addWidget(shape, 0, 1)
            grid.addWidget(self._form_label("キーフレーム間隔"), 0, 2)
            grid.addWidget(keyframe, 0, 3)
            grid.addWidget(self._form_label("recall閾値"), 1, 0)
            grid.addWidget(recall, 1, 1)
            grid.addWidget(self._form_label("confidence閾値"), 1, 2)
            grid.addWidget(confidence, 1, 3)
            grid.setColumnStretch(1, 1)
            grid.setColumnStretch(3, 1)
            grid.setRowStretch(2, 1)
            self.class_tabs.addTab(page, name)
        layout.addWidget(self.postprocess_check)
        layout.addWidget(self.class_tabs, 1)
        self.update_postprocess_enabled(True)
        return box

    def _ensure_icon_assets(self) -> dict[str, str]:
        from .settings import app_data_dir

        assets_dir = app_data_dir() / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        check_path = assets_dir / "check_white.png"
        if not check_path.exists():
            image = QtGui.QImage(32, 32, QtGui.QImage.Format_ARGB32)
            image.fill(QtCore.Qt.transparent)
            painter = QtGui.QPainter(image)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            pen = QtGui.QPen(QtGui.QColor("#ffffff"))
            pen.setWidthF(4.0)
            pen.setCapStyle(QtCore.Qt.RoundCap)
            pen.setJoinStyle(QtCore.Qt.RoundJoin)
            painter.setPen(pen)
            path = QtGui.QPainterPath()
            path.moveTo(7.0, 17.0)
            path.lineTo(13.5, 23.5)
            path.lineTo(25.0, 10.0)
            painter.drawPath(path)
            painter.end()
            image.save(str(check_path), "PNG")
        return {"check": str(check_path).replace("\\", "/")}

    def apply_style(self) -> None:
        icons = self._ensure_icon_assets()
        self.setStyleSheet(
            ("""
            /* ====== Base ====== */
            QWidget {
                font-family: "Segoe UI", "Yu Gothic UI", "Meiryo UI", sans-serif;
                font-size: 12px;
                color: #1d2939;
            }
            QWidget#centralRoot { background: #f4f6f9; }
            QToolTip {
                background: #1d2939;
                color: #f9fafb;
                border: 0;
                padding: 4px 8px;
                border-radius: 4px;
            }

            /* ====== Group boxes (cards) ====== */
            QGroupBox {
                background: #ffffff;
                border: 1px solid #e4e7ec;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 2px;
                font-weight: 600;
                color: #1d2939;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 12px;
                padding: 0 6px;
                color: #344054;
            }
            QGroupBox#advancedBox {
                background: #fafbfc;
                border: 1px solid #eaecf0;
            }
            QScrollArea#dashboardScroll {
                background: transparent;
                border: 0;
            }
            QScrollArea#dashboardScroll > QWidget > QWidget {
                background: transparent;
            }

            /* ====== Labels ====== */
            QLabel { background: transparent; color: #344054; }
            QLabel#formLabel { color: #475467; font-weight: 500; }
            QLabel#sectionLabel { color: #667085; font-weight: 600; font-size: 11px; padding-right: 4px; }
            QLabel#emptyState { color: #98a2b3; font-style: italic; }
            QLabel#runtimeInfo {
                color: #475467;
                background: #f9fafb;
                border: 1px solid #eaecf0;
                border-radius: 6px;
                padding: 6px 10px;
            }

            /* ====== Buttons ====== */
            QPushButton {
                background: #ffffff;
                color: #344054;
                border: 1px solid #d0d5dd;
                border-radius: 6px;
                padding: 5px 14px;
                min-height: 26px;
                min-width: 64px;
            }
            QPushButton:hover { background: #f9fafb; border-color: #98a2b3; color: #1d2939; }
            QPushButton:pressed { background: #f2f4f7; }
            QPushButton:disabled { background: #f9fafb; color: #b5bcc4; border-color: #eaecf0; }

            QPushButton#startButton {
                background: #2563eb;
                color: #ffffff;
                border: 1px solid #2563eb;
                font-weight: 600;
                padding: 6px 18px;
            }
            QPushButton#startButton:hover { background: #1d4ed8; border-color: #1d4ed8; }
            QPushButton#startButton:pressed { background: #1e40af; border-color: #1e40af; }
            QPushButton#startButton:disabled { background: #cdd5e0; color: #ffffff; border-color: #cdd5e0; }

            QPushButton#stopButton {
                background: #ffffff;
                color: #b42318;
                border: 1px solid #fda29b;
                font-weight: 600;
                padding: 6px 16px;
            }
            QPushButton#stopButton:hover { background: #fef3f2; border-color: #f97066; color: #912018; }
            QPushButton#stopButton:pressed { background: #fee4e2; }
            QPushButton#stopButton:disabled { background: #fdf3f2; color: #e3b5b0; border-color: #fbe4e2; }

            QPushButton#secondaryButton {
                background: #eff4ff;
                color: #1d4ed8;
                border: 1px solid #d6e4ff;
            }
            QPushButton#secondaryButton:hover { background: #dde7ff; border-color: #b2ccff; }
            QPushButton#secondaryButton:pressed { background: #c5d6ff; }
            QPushButton#secondaryButton:disabled { background: #f4f6fa; color: #b5bcc4; border-color: #eaecf0; }

            QPushButton#dangerGhostButton {
                background: transparent;
                color: #b42318;
                border: 1px solid #eaecf0;
            }
            QPushButton#dangerGhostButton:hover { background: #fef3f2; border-color: #fda29b; }
            QPushButton#dangerGhostButton:disabled { color: #d6bbbb; border-color: #eaecf0; }

            QToolButton#advancedToggle {
                background: transparent;
                color: #475467;
                border: 1px solid #e4e7ec;
                border-radius: 6px;
                padding: 5px 12px;
                font-weight: 500;
            }
            QToolButton#advancedToggle:hover { background: #f2f4f7; color: #1d2939; }
            QToolButton#advancedToggle:checked {
                background: #eff4ff;
                color: #1d4ed8;
                border-color: #b2ccff;
            }

            /* ====== Inputs ====== */
            QLineEdit, QPlainTextEdit, QTextEdit {
                background: #ffffff;
                color: #1d2939;
                border: 1px solid #d0d5dd;
                border-radius: 6px;
                padding: 5px 8px;
                selection-background-color: #b2ccff;
                selection-color: #1d2939;
            }
            QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover { border-color: #98a2b3; }
            QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus { border-color: #2563eb; }
            QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled {
                background: #f9fafb; color: #98a2b3; border-color: #eaecf0;
            }
            QLineEdit:read-only, QPlainTextEdit:read-only, QTextEdit:read-only {
                background: #fafbfc;
            }

            QComboBox {
                background: #ffffff;
                color: #1d2939;
                border: 1px solid #d0d5dd;
                border-radius: 6px;
                padding: 4px 10px;
                min-height: 24px;
            }
            QComboBox:hover { border-color: #98a2b3; }
            QComboBox:focus { border-color: #2563eb; }
            QComboBox:on { border-color: #2563eb; }
            QComboBox:disabled { background: #f9fafb; color: #98a2b3; border-color: #eaecf0; }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 22px;
                border: 0;
                background: transparent;
            }
            QComboBox::down-arrow {
                image: none;
                width: 0; height: 0;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #667085;
                margin-right: 8px;
            }
            QComboBox::down-arrow:disabled {
                border-top-color: #cbd5e1;
            }
            QComboBox QAbstractItemView {
                background: #ffffff;
                color: #1d2939;
                border: 1px solid #d0d5dd;
                selection-background-color: #eff4ff;
                selection-color: #1d4ed8;
                outline: 0;
                padding: 2px;
            }

            QSpinBox, QDoubleSpinBox {
                background: #ffffff;
                color: #1d2939;
                border: 1px solid #d0d5dd;
                border-radius: 6px;
                padding: 4px 6px;
                min-height: 24px;
            }
            QSpinBox:hover, QDoubleSpinBox:hover { border-color: #98a2b3; }
            QSpinBox:focus, QDoubleSpinBox:focus { border-color: #2563eb; }
            QSpinBox:disabled, QDoubleSpinBox:disabled { background: #f9fafb; color: #98a2b3; border-color: #eaecf0; }
            QSpinBox::up-button, QDoubleSpinBox::up-button,
            QSpinBox::down-button, QDoubleSpinBox::down-button {
                subcontrol-origin: border;
                width: 16px;
                border: 0;
                background: transparent;
            }
            QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-position: top right; }
            QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-position: bottom right; }
            QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
            QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover { background: #f2f4f7; }
            QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
                image: none; width: 0; height: 0;
                border-left: 3px solid transparent;
                border-right: 3px solid transparent;
                border-bottom: 4px solid #667085;
            }
            QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
                image: none; width: 0; height: 0;
                border-left: 3px solid transparent;
                border-right: 3px solid transparent;
                border-top: 4px solid #667085;
            }
            QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled { border-bottom-color: #cbd5e1; }
            QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled { border-top-color: #cbd5e1; }

            /* ====== Checkboxes ====== */
            QCheckBox {
                color: #344054;
                spacing: 8px;
                padding: 3px 2px;
            }
            QCheckBox:disabled { color: #b5bcc4; }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                background: #ffffff;
            }
            QCheckBox::indicator:hover { border-color: #2563eb; }
            QCheckBox::indicator:focus { border-color: #2563eb; }
            QCheckBox::indicator:checked {
                background: #2563eb;
                border: 1px solid #2563eb;
                image: url("__CHECK_ICON__");
            }
            QCheckBox::indicator:checked:hover { background: #1d4ed8; border-color: #1d4ed8; }
            QCheckBox::indicator:indeterminate {
                background: #b2ccff;
                border: 1px solid #2563eb;
            }
            QCheckBox::indicator:disabled { background: #f2f4f7; border-color: #e4e7ec; }
            QCheckBox::indicator:checked:disabled { background: #cbd5e1; border-color: #cbd5e1; }

            /* ====== Radio buttons ====== */
            QRadioButton { color: #344054; spacing: 8px; padding: 3px 2px; }
            QRadioButton:disabled { color: #b5bcc4; }
            QRadioButton::indicator {
                width: 16px; height: 16px;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                background: #ffffff;
            }
            QRadioButton::indicator:hover { border-color: #2563eb; }
            QRadioButton::indicator:checked {
                background: #2563eb;
                border: 4px solid #ffffff;
                outline: 1px solid #2563eb;
            }
            QRadioButton::indicator:disabled { background: #f2f4f7; border-color: #e4e7ec; }

            /* ====== Tabs ====== */
            QTabWidget::pane {
                background: #ffffff;
                border: 1px solid #e4e7ec;
                border-radius: 8px;
                top: -1px;
            }
            QTabWidget#classTabs::pane {
                background: #fafbfc;
            }
            QTabBar { qproperty-drawBase: 0; background: transparent; }
            QTabBar::tab {
                background: #f2f4f7;
                color: #667085;
                padding: 6px 18px;
                margin-right: 4px;
                margin-top: 2px;
                border: 1px solid transparent;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                min-width: 70px;
                font-weight: 500;
            }
            QTabBar::tab:hover { background: #e9ecf2; color: #344054; }
            QTabBar::tab:selected {
                background: #ffffff;
                color: #1d4ed8;
                border: 1px solid #e4e7ec;
                border-bottom-color: #ffffff;
                font-weight: 600;
                margin-top: 0px;
            }
            QTabWidget#classTabs QTabBar::tab:selected { border-bottom-color: #fafbfc; }
            QTabBar::tab:disabled { color: #b5bcc4; background: #f9fafb; }
            QTabBar::tear { width: 0; height: 0; }

            /* ====== List widgets ====== */
            QListWidget {
                background: #ffffff;
                border: 1px solid #d0d5dd;
                border-radius: 6px;
                padding: 4px;
                outline: 0;
                alternate-background-color: #fafbfc;
            }
            QListWidget::item {
                padding: 6px 8px;
                border-radius: 4px;
                color: #1d2939;
            }
            QListWidget::item:hover { background: #f2f4f7; }
            QListWidget::item:selected {
                background: #eff4ff;
                color: #1d4ed8;
            }
            QListWidget#queueList::item { padding: 6px 10px; }

            /* ====== Progress bars ====== */
            QProgressBar {
                background: #eef0f4;
                border: 1px solid #e4e7ec;
                border-radius: 6px;
                text-align: center;
                color: #344054;
                font-weight: 500;
                min-height: 18px;
            }
            QProgressBar::chunk {
                background: #2563eb;
                border-radius: 5px;
                margin: 1px;
            }
            QProgressBar#phaseProgress::chunk { background: #38bdf8; }

            /* ====== Splitters ====== */
            QSplitter#bodySplitter::handle {
                background: transparent;
            }
            QSplitter#bodySplitter::handle:vertical {
                height: 8px;
                margin: 1px 24px;
                background: #e4e7ec;
                border-radius: 3px;
            }
            QSplitter#bodySplitter::handle:vertical:hover { background: #98a2b3; }
            QSplitter#bodySplitter::handle:horizontal {
                width: 8px;
                margin: 24px 1px;
                background: #e4e7ec;
                border-radius: 3px;
            }
            QSplitter#bodySplitter::handle:horizontal:hover { background: #98a2b3; }

            /* ====== Scrollbars ====== */
            QScrollBar:vertical {
                background: transparent;
                width: 10px;
                margin: 2px 0;
            }
            QScrollBar::handle:vertical {
                background: #cbd5e1;
                border-radius: 4px;
                min-height: 28px;
                margin: 0 2px;
            }
            QScrollBar::handle:vertical:hover { background: #98a2b3; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                background: transparent; height: 0; border: 0;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }

            QScrollBar:horizontal {
                background: transparent;
                height: 10px;
                margin: 0 2px;
            }
            QScrollBar::handle:horizontal {
                background: #cbd5e1;
                border-radius: 4px;
                min-width: 28px;
                margin: 2px 0;
            }
            QScrollBar::handle:horizontal:hover { background: #98a2b3; }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                background: transparent; width: 0; border: 0;
            }
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }

            /* ====== Progress dashboard ====== */
            QLabel#statusBanner {
                background: #f2f4f7;
                color: #475467;
                border: 1px solid #e4e7ec;
                border-radius: 6px;
                padding: 8px 12px;
                font-weight: 600;
                font-size: 13px;
            }
            QLabel#statusBanner[state="idle"] {
                background: #f2f4f7;
                color: #475467;
                border-color: #e4e7ec;
            }
            QLabel#statusBanner[state="running"] {
                background: #eff4ff;
                color: #1d4ed8;
                border-color: #b2ccff;
            }
            QLabel#statusBanner[state="done"] {
                background: #ecfdf5;
                color: #047857;
                border-color: #a7f3d0;
            }
            QLabel#statusBanner[state="error"] {
                background: #fef3f2;
                color: #b42318;
                border-color: #fda29b;
            }
            QLabel#statusBanner[state="stopped"] {
                background: #fffaeb;
                color: #92400e;
                border-color: #fde68a;
            }
            QFrame#metricCard {
                background: #f9fafb;
                border: 1px solid #eaecf0;
                border-radius: 6px;
            }
            QLabel#metricKey {
                color: #667085;
                font-size: 10px;
                font-weight: 500;
            }
            QLabel#metricValue {
                color: #1d2939;
                font-weight: 600;
                font-size: 13px;
            }
            QLabel#statusKey {
                color: #667085;
                font-size: 11px;
                padding: 1px 4px 1px 0;
            }
            QLabel#statusValue {
                color: #1d2939;
                padding: 1px 4px;
            }
            QPlainTextEdit#logEdit {
                font-family: "Cascadia Mono", Consolas, "Courier New", monospace;
                font-size: 11px;
                color: #344054;
                background: #fafbfc;
                border: 1px solid #e4e7ec;
            }
            QPlainTextEdit#summaryText {
                background: #f9fafb;
                color: #475467;
                font-family: "Cascadia Mono", Consolas, "Courier New", monospace;
                font-size: 11px;
                border: 1px solid #eaecf0;
            }
            """).replace("__CHECK_ICON__", icons["check"])
        )

    def refresh_wsl_distros(self) -> None:
        current = self.wsl_combo.currentText() if self.wsl_combo.count() else self.settings.wsl_distro
        distros = [current or "Ubuntu"]
        try:
            distros = WslBridge.list_distros() or distros
        except Exception:
            pass
        self.wsl_combo.clear()
        self.wsl_combo.addItems(distros)
        index = self.wsl_combo.findText(current)
        if index >= 0:
            self.wsl_combo.setCurrentIndex(index)
        else:
            self.wsl_combo.setEditText(current)

    def discover_runtime(self, *, silent: bool) -> bool:
        if silent and self.wsl_repo_edit.text().strip():
            try:
                self.current_bridge().load_runtime()
                return False
            except Exception:
                pass
        self.refresh_wsl_distros()
        distros: list[str] = []
        current = self.wsl_combo.currentText().strip() or self.settings.wsl_distro or "Ubuntu"
        if current:
            distros.append(current)
        for index in range(self.wsl_combo.count()):
            distro = self.wsl_combo.itemText(index).strip()
            if distro and distro not in distros:
                distros.append(distro)
        for distro in distros or ["Ubuntu"]:
            try:
                candidates = WslBridge.discover_repo_paths(distro)
            except Exception:
                candidates = []
            if not candidates:
                continue
            self.wsl_combo.setEditText(distro)
            self.wsl_repo_edit.setText(candidates[0])
            self.runtime_info_label.setText(f"自動探索: {distro} {candidates[0]}")
            if not silent:
                QtWidgets.QMessageBox.information(self, "WSL runtime found", f"{distro}\n{candidates[0]}")
            return True
        if not silent:
            self.runtime_info_label.setText("自動探索: Dinov3_postprocess が見つかりません")
            QtWidgets.QMessageBox.warning(
                self,
                "WSL runtime not found",
                "WSL 内で Dinov3_postprocess が見つかりませんでした。Distro と WSL repo を手動で指定してください。",
            )
        return False

    def current_bridge(self) -> WslBridge:
        return WslBridge(self.wsl_combo.currentText().strip() or "Ubuntu", self.wsl_repo_edit.text().strip())

    def load_runtime(self, *, silent: bool) -> bool:
        self.bridge = self.current_bridge()
        try:
            self.runtime = self.bridge.load_runtime()
        except Exception as exc:
            self.runtime = None
            self.runtime_info_label.setText(f"接続失敗: {exc}")
            if not silent:
                QtWidgets.QMessageBox.warning(self, "WSL接続失敗", str(exc))
            return False
        summary = self.bridge.setup_summary()
        if not silent:
            checks = self.bridge.validate_runtime(self.runtime)
            ok = all(item_ok for _name, item_ok, _detail in checks)
            lines = [summary, *[f"{'OK' if item_ok else 'NG'} {name}: {detail}" for name, item_ok, detail in checks]]
            self.runtime_info_label.setText("\n".join(lines))
            if not ok:
                QtWidgets.QMessageBox.warning(self, "WSL接続確認", "一部の接続確認に失敗しました。Runtime 表示の NG 項目を確認してください。")
                return False
        self.settings.wsl_distro = self.bridge.distro
        self.settings.wsl_repo_path = self.bridge.repo_path
        self.settings.windows_output_dir = self.output_edit.text() if hasattr(self, "output_edit") else self.settings.windows_output_dir
        self.settings.run_prefix = self.run_prefix_edit.text() if hasattr(self, "run_prefix_edit") else self.settings.run_prefix
        self.settings.save()
        if silent:
            self.runtime_info_label.setText(summary)
        return True

    def browse_output_dir(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "結果保存先", self.output_edit.text())
        if path:
            self.output_edit.setText(path)

    def add_videos(self) -> None:
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "動画を追加",
            str(Path.home()),
            "Video files (*.mp4 *.avi *.mov *.mkv *.webm *.m4v);;All files (*)",
        )
        self.add_paths([Path(file) for file in files])

    def add_folder(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "フォルダを追加", str(Path.home()))
        if folder:
            self.add_paths([Path(folder)])

    def add_paths(self, paths: list[Path]) -> None:
        existing = {str(path) for path in self.queue_paths}
        for path in paths:
            resolved = normalize_windows_path(path)
            if str(resolved) in existing:
                continue
            try:
                exists = resolved.exists()
                is_file = resolved.is_file()
                suffix = resolved.suffix.lower()
            except OSError as exc:
                self.append_log(f"[skip] cannot access: {resolved} ({exc})")
                continue
            if not exists:
                self.append_log(f"[skip] not found: {resolved}")
                continue
            if is_file and suffix not in VIDEO_EXTS:
                self.append_log(f"[skip] unsupported file: {resolved}")
                continue
            self.queue_paths.append(resolved)
            existing.add(str(resolved))
        self.update_queue_state()

    def remove_selected(self) -> None:
        for row in sorted((index.row() for index in self.queue_list.selectedIndexes()), reverse=True):
            if 0 <= row < len(self.queue_paths):
                del self.queue_paths[row]
        self.update_queue_state()

    def clear_queue(self) -> None:
        if self.process_is_running():
            return
        self.queue_paths.clear()
        self.update_queue_state()

    def update_queue_state(self) -> None:
        self.queue_list.clear()
        for path in self.queue_paths:
            item = QtWidgets.QListWidgetItem(str(path))
            item.setToolTip(str(path))
            self.queue_list.addItem(item)
        self.queue_stack.setCurrentWidget(self.queue_list if self.queue_paths else self.empty_queue_label)
        self.start_button.setEnabled(bool(self.queue_paths) and not self.process_is_running())
        self.remove_button.setEnabled(bool(self.queue_paths) and not self.process_is_running())
        self.clear_button.setEnabled(bool(self.queue_paths) and not self.process_is_running())

    def check_artifacts(self) -> None:
        if self.process_is_running():
            return
        if self.runtime is None and not self.load_runtime(silent=False):
            return
        assert self.runtime is not None
        command = self.bridge.check_artifacts_command(self.runtime)
        self.append_log("[artifact-check] " + as_command_text(command))
        self.start_process(command, label="チェックポイント確認", tool_only=True)

    def start_queue(self) -> None:
        if self.process_is_running() or not self.queue_paths:
            return
        if self.runtime is None and not self.load_runtime(silent=False):
            return
        expanded_inputs = self.expand_queue_paths()
        if not expanded_inputs:
            self.status_banner.setText("エラー")
            self.status_value_label.setText("no videos")
            self.summary_text.setPlainText("status: error\nqueue: no supported videos")
            return
        output_root = normalize_windows_path(self.output_edit.text())
        try:
            output_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.report_local_error("出力先を作成できません", f"{output_root}\n{exc}")
            return
        self.settings.windows_output_dir = str(output_root)
        self.settings.run_prefix = self.run_prefix_edit.text()
        self.settings.save()
        self.run_queue = expanded_inputs
        self.current_index = -1
        self.current_summary_path = None
        self.stopping = False
        self.workflow_running = True
        self.queue_start_time = time.perf_counter()
        self.active_progress_phase_key = ""
        self.last_overall_percent = 0.0
        self.configure_progress_plan()
        self.log_edit.clear()
        self.total_progress.setRange(0, 1000)
        self.total_progress.setValue(0)
        self.status_banner.setText("実行中")
        self.status_value_label.setText("running")
        self.summary_text.setPlainText("status: running")
        self.start_next_item()

    def configure_progress_plan(self) -> None:
        phases: list[tuple[str, float]] = [("normalize_input", 0.02), ("inference", 0.50), ("raw_sqlite", 0.05)]
        if self.postprocess_check.isChecked():
            phases.append(("postprocess", 0.25))
        if self.detector_overlay_check.isChecked():
            phases.append(("raw_overlay", 0.08))
        if self.detailed_overlay_check.isChecked():
            phases.append(("detailed_overlay", 0.07))
        if self.simple_overlay_check.isChecked():
            phases.append(("simple_overlay", 0.03))
        total = sum(weight for _, weight in phases) or 1.0
        offset = 0.0
        self.progress_phase_weights = {}
        self.progress_phase_offsets = {}
        for key, weight in phases:
            normalized = weight / total
            self.progress_phase_offsets[key] = offset
            self.progress_phase_weights[key] = normalized
            offset += normalized

    def expand_queue_paths(self) -> list[Path]:
        expanded: list[Path] = []
        seen: set[str] = set()
        for path in self.queue_paths:
            try:
                if path.is_file():
                    candidates = [path]
                elif path.is_dir():
                    iterator = path.rglob("*") if self.recursive_check.isChecked() else path.iterdir()
                    candidates = sorted(candidate for candidate in iterator if candidate.is_file() and candidate.suffix.lower() in VIDEO_EXTS)
                else:
                    continue
            except OSError as exc:
                self.append_log(f"[skip] cannot enumerate: {path} ({exc})")
                continue
            for candidate in candidates:
                key = str(candidate)
                if key not in seen:
                    seen.add(key)
                    expanded.append(candidate)
        return expanded

    def start_next_item(self) -> None:
        self.current_index += 1
        if self.current_index >= len(self.run_queue):
            self.finish_queue(success=True)
            return
        path = self.run_queue[self.current_index]
        self.active_progress_phase_key = ""
        try:
            command = self.build_command(path, self.current_index)
        except (OSError, RuntimeError, ValueError) as exc:
            self.append_log(f"[error] failed to prepare job: {exc}")
            self.report_local_error("ジョブを開始できません", str(exc), finish_workflow=True)
            return
        self.current_run_name = self.extract_run_name(command)
        output_root = normalize_windows_path(self.output_edit.text())
        self.current_summary_path = output_root / self.current_run_name / "summary.json"
        self.current_video_value.setText(path.name)
        self.output_value.setText(str(self.current_summary_path.parent))
        self.command_value.setText(as_command_text(command))
        self.phase_value.setText("起動中")
        self.status_value_label.setText("running")
        self.progress_dashboard.set_overall_percent(self.current_index / max(1, len(self.run_queue)) * 100.0)
        self.count_value.setText(
            f"Inference {self.current_index + 1} / {len(self.run_queue)} | "
            f"Postprocess {self.current_index + 1 if self.postprocess_check.isChecked() else 0} / {len(self.run_queue)}"
        )
        self.remaining_value.setText(str(len(self.run_queue) - self.current_index - 1))
        self.append_log("")
        self.append_log(f"[queue] {self.current_index + 1}/{len(self.run_queue)} {path}")
        self.start_process(command, label=path.name, tool_only=False)

    def profile_recs(self) -> dict:
        if self.runtime is None:
            return {}
        return self.runtime.profile_recommendations()

    def profile_int(self, section: str, key: str) -> int | None:
        try:
            value = self.profile_recs().get(section, {}).get(key)
            return int(value) if value is not None else None
        except Exception:
            return None

    def profile_value(self, section: str, key: str) -> str | None:
        try:
            value = self.profile_recs().get(section, {}).get(key)
            return str(value) if value else None
        except Exception:
            return None

    def staging_output_root_wsl(self, run_name: str) -> str:
        assert self.runtime is not None
        base = self.runtime.gui_runtime_env.get("WINDOWS_FRONTEND_STAGING_ROOT")
        if not base:
            base = f"{self.bridge.repo_path.rstrip('/')}/.runtime/windows_frontend_staging"
        return f"{base.rstrip('/')}/{run_name}"

    def quote_wsl(self, value: str | Path) -> str:
        return shlex.quote(str(value))

    def final_copy_cleanup_script(
        self,
        *,
        inner: list[str],
        staging_root: str,
        run_name: str,
        final_root: str,
        final_run_windows: Path,
    ) -> str:
        staging_run_dir = f"{staging_root.rstrip('/')}/{run_name}"
        final_run_dir = f"{final_root.rstrip('/')}/{run_name}"
        final_run_windows_text = str(final_run_windows).replace("\\", "/")
        self.last_built_staging_output_root_wsl = staging_root
        self.last_built_staging_run_dir_wsl = staging_run_dir
        self.last_built_final_run_dir_wsl = final_run_dir
        rewrite_code = (
            "from pathlib import Path, PureWindowsPath\n"
            "import os\n"
            "root = Path(os.environ['CODX_FINAL_RUN'])\n"
            "final_win = (os.environ.get('CODX_FINAL_WIN') or os.environ['CODX_FINAL_RUN']).replace(chr(92), '/')\n"
            "parent_win = str(PureWindowsPath(final_win).parent).replace(chr(92), '/')\n"
            "pairs = [\n"
            "    (os.environ['CODX_STAGING_RUN'], final_win),\n"
            "    (os.environ['CODX_STAGING_ROOT'], parent_win),\n"
            "    (os.environ['CODX_FINAL_RUN'], final_win),\n"
            "]\n"
            "suffixes = {'.json', '.jsonl', '.txt', '.log', '.md', '.csv'}\n"
            "for path in root.rglob('*'):\n"
            "    if not path.is_file() or path.suffix.lower() not in suffixes:\n"
            "        continue\n"
            "    try:\n"
            "        text = path.read_text(encoding='utf-8')\n"
            "    except (OSError, UnicodeDecodeError):\n"
            "        continue\n"
            "    updated = text\n"
            "    for old, new in pairs:\n"
            "        updated = updated.replace(old, new)\n"
            "    if updated != text:\n"
            "        path.write_text(updated, encoding='utf-8')\n"
        )

        steps = [
            f"mkdir -p {self.quote_wsl(staging_root)} {self.quote_wsl(final_root)}",
        ]
        if self.force_check.isChecked():
            steps.append(f"rm -rf {self.quote_wsl(final_run_dir)}")
        else:
            steps.append(
                f"if [ -e {self.quote_wsl(final_run_dir)} ]; then "
                f"echo {self.quote_wsl('[error] final output already exists: ' + final_run_dir)}; exit 2; fi"
            )
        steps.extend(
            [
                self.bridge.quote_command(inner),
                f"test -d {self.quote_wsl(staging_run_dir)}",
                f"mkdir -p {self.quote_wsl(final_root)}",
                f"cp -a {self.quote_wsl(staging_run_dir)} {self.quote_wsl(final_root)}/",
                "export "
                f"CODX_STAGING_ROOT={self.quote_wsl(staging_root)} "
                f"CODX_STAGING_RUN={self.quote_wsl(staging_run_dir)} "
                f"CODX_FINAL_RUN={self.quote_wsl(final_run_dir)} "
                f"CODX_FINAL_WIN={self.quote_wsl(final_run_windows_text)}",
                f"{self.quote_wsl(self.runtime.python if self.runtime else 'python3')} -c {self.quote_wsl(rewrite_code)}",
                f"rm -rf {self.quote_wsl(staging_root)}",
                f"echo {self.quote_wsl('[windows-output] ' + final_run_windows_text)}",
            ]
        )
        return "; ".join(steps)

    def build_command(self, input_path: Path, index: int) -> list[str]:
        assert self.runtime is not None
        detector = str(self.detector_combo.currentData())
        final_output_root = normalize_windows_path(self.output_edit.text())
        prefix = clean_run_part(self.run_prefix_edit.text() or "ui_run")
        run_name = f"{prefix}_{timestamp()}_{index + 1:02d}_{clean_run_part(input_path.stem)}"
        self.last_built_run_name = run_name
        postprocess = self.postprocess_check.isChecked()
        detailed_overlay = postprocess and self.detailed_overlay_check.isChecked()
        simple_overlay = postprocess and self.simple_overlay_check.isChecked()
        if detailed_overlay and simple_overlay:
            overlay_mode = "both"
        elif detailed_overlay:
            overlay_mode = "detailed"
        elif simple_overlay:
            overlay_mode = "simple"
        else:
            overlay_mode = "none"

        input_wsl = windows_path_to_wsl(input_path)
        final_output_wsl = windows_path_to_wsl(final_output_root)
        staging_output_wsl = self.staging_output_root_wsl(run_name)
        staging_output_unc = Path(wsl_path_to_unc(self.bridge.distro, staging_output_wsl))
        pipeline_command = [
            self.runtime.python,
            "scripts/run_integrated_pipeline.py",
            "--input",
            input_wsl,
            "--output-root",
            staging_output_wsl,
            "--run-name",
            run_name,
            "--detector",
            detector,
            "--postprocess" if postprocess else "--no-postprocess",
            "--progress-interval-sec",
            "5",
        ]
        if postprocess:
            policy_path = self.write_policy_file(staging_output_unc, run_name)
            pipeline_command.extend(
                [
                    "--intervals",
                    ",".join(str(value) for value in self.class_intervals()),
                    "--default-shape-mode",
                    "ellipse",
                    "--class-policy-json",
                    windows_path_to_wsl(policy_path),
                    "--no-render-overlays",
                    "--overlay-encoder",
                    DEFAULT_OVERLAY_ENCODER,
                    "--raw-remove-short-tracks-max-frames",
                    str(DEFAULT_RAW_REMOVE_SHORT_TRACKS_MAX_FRAMES),
                    "--raw-cut-detect" if self.raw_cut_detect_check.isChecked() else "--no-raw-cut-detect",
                ]
            )
        if self.force_check.isChecked():
            pipeline_command.append("--force")
        if self.max_frames_spin.value() > 0:
            pipeline_command.extend(["--max-frames", str(self.max_frames_spin.value())])
        self.add_detector_options(pipeline_command, detector)
        if self.score_enable.isChecked():
            flag = "--eva02-score-thresh" if detector == "eva02" else "--codino-score-thresh" if detector == "codino" else "--score-thresh"
            pipeline_command.extend([flag, f"{self.score_spin.value():.3f}"])
        if postprocess:
            fallback_recall = max(self.class_recall_values()) if self.class_recall_values() else 0.960
            pipeline_command.extend(
                [
                    "--postprocess-extra-args",
                    "--embed-original-masks" if detailed_overlay else "--no-embed-original-masks",
                    "--raw-det-score-min",
                    f"{self.global_confidence_floor():.3f}",
                    "--dense-recall-target",
                    f"{fallback_recall:.3f}",
                    "--polygon-recall-min",
                    f"{fallback_recall:.3f}",
                    "--progress-interval-sec",
                    "5",
                ]
            )

        inner = [
            self.runtime.python,
            "apps/qt_ui/run_ui_job.py",
            "--input",
            input_wsl,
            "--output-root",
            staging_output_wsl,
            "--run-name",
            run_name,
            "--overlay-mode",
            overlay_mode,
            "--raw-overlay" if self.detector_overlay_check.isChecked() else "--no-raw-overlay",
            "--encoder",
            DEFAULT_OVERLAY_ENCODER,
        ]
        if self.force_check.isChecked():
            inner.append("--force")
        inner.append("--")
        inner.extend(pipeline_command)
        script = self.final_copy_cleanup_script(
            inner=inner,
            staging_root=staging_output_wsl,
            run_name=run_name,
            final_root=final_output_wsl,
            final_run_windows=final_output_root / run_name,
        )
        return self.bridge.job_script_command(script)

    def add_detector_options(self, command: list[str], detector: str) -> None:
        if detector == "eva02":
            batch = self.batch_size_spin.value() if self.batch_size_spin.value() > 0 else self.profile_int("eva02", "batch_size")
            warmup = self.warmup_spin.value() if self.warmup_spin.value() >= 0 else self.profile_int("eva02", "warmup_frames")
            classifier_batch = self.profile_int("eva02", "classifier_batch_size")
            if batch:
                command.extend(["--eva02-batch-size", str(batch)])
            if warmup is not None:
                command.extend(["--eva02-warmup-frames", str(warmup)])
            if classifier_batch:
                command.extend(["--eva02-classifier-batch-size", str(classifier_batch)])
        elif detector == "codino":
            batch = self.batch_size_spin.value() if self.batch_size_spin.value() > 0 else self.profile_int("codino", "batch_size")
            warmup = self.warmup_spin.value() if self.warmup_spin.value() >= 0 else self.profile_int("codino", "warmup_frames")
            if batch:
                command.extend(["--codino-batch-size", str(batch)])
            if warmup is not None:
                command.extend(["--codino-warmup-frames", str(warmup)])
            for key, flag in (
                ("trt_backbone_engine", "--codino-trt-backbone-engine"),
                ("trt_feature_engine", "--codino-trt-feature-engine"),
                ("trt_query_encoder_engine", "--codino-trt-query-encoder-engine"),
                ("trt_decoder_engine", "--codino-trt-decoder-engine"),
                ("trt_mask_head_engine", "--codino-trt-mask-head-engine"),
            ):
                value = self.profile_value("codino", key)
                if value:
                    command.extend([flag, value])
        else:
            batch = self.batch_size_spin.value() if self.batch_size_spin.value() > 0 else self.profile_int("dinov3", "batch_size")
            warmup = self.warmup_spin.value() if self.warmup_spin.value() >= 0 else self.profile_int("dinov3", "warmup_frames")
            engine = self.profile_value("dinov3", "trt_backbone_engine")
            if batch:
                command.extend(["--batch-size", str(batch)])
            if warmup is not None:
                command.extend(["--warmup-frames", str(warmup)])
            if engine:
                command.extend(["--trt-backbone-engine", engine])

    def class_policy_entries(self) -> dict[str, dict[str, object]]:
        entries: dict[str, dict[str, object]] = {}
        for name in ("女性器", "男性器", "結合部分"):
            recall = float(self.class_recall_spins[name].value())
            confidence = float(self.class_confidence_spins[name].value())
            entries[name] = {
                "shape_mode": str(self.class_shape_combos[name].currentData()),
                "target_interval": int(self.class_keyframe_spins[name].value()),
                "dense_recall_target": recall,
                "polygon_recall_min": recall,
                "target_recall": recall,
                "raw_det_score_min": confidence,
                "confidence_min": confidence,
            }
        entries["結合"] = dict(entries["結合部分"])
        return entries

    def class_intervals(self) -> list[int]:
        return sorted({int(spin.value()) for spin in self.class_keyframe_spins.values()}) or [3]

    def class_recall_values(self) -> list[float]:
        return [float(spin.value()) for spin in self.class_recall_spins.values()]

    def global_confidence_floor(self) -> float:
        values = [float(spin.value()) for spin in self.class_confidence_spins.values()]
        return min(values) if values else 0.350

    def write_policy_file(self, output_root: Path, run_name: str) -> Path:
        fallback_recall = max(self.class_recall_values()) if self.class_recall_values() else 0.960
        entry = {
            "shape_mode": "ellipse",
            "target_interval": self.class_intervals()[0],
            "dense_recall_target": fallback_recall,
            "polygon_recall_min": fallback_recall,
            "target_recall": fallback_recall,
            "raw_det_score_min": self.global_confidence_floor(),
            "confidence_min": self.global_confidence_floor(),
        }
        policy = {"default": entry, "classes": self.class_policy_entries()}
        config_dir = output_root / run_name / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        path = config_dir / "class_policy.ui.json"
        path.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def extract_run_name(self, command: list[str]) -> str:
        if self.last_built_run_name:
            return self.last_built_run_name
        try:
            idx = command.index("--run-name")
            return command[idx + 1]
        except (ValueError, IndexError):
            for part in command:
                if part.startswith("ui_run_"):
                    return part
            return f"ui_run_{timestamp()}"

    def start_process(self, command: list[str], *, label: str, tool_only: bool) -> None:
        self.process = QtCore.QProcess(self)
        self.process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        env = QtCore.QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        self.process.setProcessEnvironment(env)
        self.process.readyReadStandardOutput.connect(self.read_process_output)
        self.process.finished.connect(lambda code, status: self.process_finished(code, status, tool_only))
        self.process.errorOccurred.connect(self.process_error)
        self.active_log_buffer = ""
        self.process_start_time = time.perf_counter()
        self.elapsed_timer.start()
        self.update_running_state(True)
        self.process_value.setText("Running")
        self.progress_dashboard.set_phase_indeterminate()
        self.append_log("[runtime] " + (self.bridge.setup_summary() if self.runtime else "not connected"))
        self.append_log("[cmd] " + as_command_text(command))
        launch_command = hidden_windows_process_command(command)
        self.process.start(launch_command[0], launch_command[1:])
        if not self.process.waitForStarted(3000):
            detail = self.process.errorString() if self.process is not None else ""
            self.append_log(f"[error] failed to start: {label}" + (f" ({detail})" if detail else ""))
            self.handle_start_failure(tool_only=tool_only)

    def handle_start_failure(self, *, tool_only: bool) -> None:
        self.elapsed_timer.stop()
        self.progress_dashboard.set_phase_done(False)
        self.process_value.setText("NotRunning")
        self.status_banner.setText("エラー")
        self.status_value_label.setText("failed to start")
        self.summary_text.setPlainText("status: error\nprocess: failed to start")
        self.process = None
        self.update_running_state(False)
        if self.workflow_running and not tool_only:
            self.finish_queue(success=False, exit_code=-1)

    def read_process_output(self) -> None:
        if self.process is None:
            return
        data = bytes(self.process.readAllStandardOutput()).decode(errors="replace")
        if not data:
            return
        self.active_log_buffer += data
        while "\n" in self.active_log_buffer:
            line, self.active_log_buffer = self.active_log_buffer.split("\n", 1)
            self.handle_process_line(line.rstrip())
        cursor = self.log_edit.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        self.log_edit.setTextCursor(cursor)

    def handle_process_line(self, line: str) -> None:
        self.append_log(line)
        progress_fields = parse_progress_line(line)
        if progress_fields:
            self.apply_progress_fields(progress_fields)
            self.heartbeat_value.setText(time.strftime("%Y-%m-%d %H:%M:%S"))
            return
        if line.startswith("[run]"):
            self.update_phase_from_text(line.removeprefix("[run]").strip(), fraction=0.0)
        elif line.startswith("[done]"):
            text = line.removeprefix("[done]").strip()
            self.update_phase_from_text(text, fraction=1.0 if self.is_direct_progress_phase(text) else None)
        elif line.startswith("[summary]") or line.startswith("[index]"):
            self.summary_text.setPlainText("status: completed\n" + line)
        elif line.startswith("[phase-start]"):
            self.update_phase_from_text(line.removeprefix("[phase-start]").strip(), fraction=0.0)
        elif line.startswith("[phase-progress]"):
            text = line.removeprefix("[phase-progress]").strip()
            self.update_phase_from_text(text, fraction=None)
            self.summary_text.setPlainText("status: running\n" + text)
        elif line.startswith("[phase-done]"):
            text = line.removeprefix("[phase-done]").strip()
            self.update_phase_from_text(text, fraction=1.0 if self.is_direct_progress_phase(text) else None)
        elif line.startswith("[DONE]"):
            self.summary_text.setPlainText("status: running\n" + line)
        elif line.startswith("[error]") or "failed" in line.lower():
            self.status_value_label.setText("error")
            self.status_banner.setText("エラー")
        self.heartbeat_value.setText(time.strftime("%Y-%m-%d %H:%M:%S"))

    def progress_key_for_text(self, text: str | None) -> str:
        key = phase_key(text)
        if key in self.progress_phase_weights:
            return key
        if key == "command" and text:
            lowered = text.lower()
            if "postprocess" in lowered:
                return "postprocess"
            if "overlay" in lowered:
                return phase_key(lowered)
            if "infer" in lowered or "detect" in lowered:
                return "inference"
        if key != "unknown" and phase_label(key) != key:
            return key
        return self.active_progress_phase_key or next(iter(self.progress_phase_weights or {"unknown": 1.0}))

    def is_direct_progress_phase(self, text: str | None) -> bool:
        key = phase_key(text)
        return key in self.progress_phase_weights or (key != "unknown" and phase_label(key) != key)

    def progress_fraction_from_fields(self, fields: dict[str, str]) -> float | None:
        for key in ("percent", "phase_percent"):
            value = progress_float(fields, key)
            if value is not None:
                return max(0.0, min(100.0, value)) / 100.0
        return None

    def overall_percent_for_phase(self, key: str, fraction: float | None) -> float | None:
        if not self.workflow_running or not self.run_queue or self.current_index < 0:
            return None
        fraction = 0.0 if fraction is None else max(0.0, min(1.0, fraction))
        offset = self.progress_phase_offsets.get(key, 0.0)
        weight = self.progress_phase_weights.get(key, 0.0)
        item_fraction = max(0.0, min(1.0, offset + weight * fraction))
        overall = (self.current_index + item_fraction) / max(1, len(self.run_queue)) * 100.0
        if overall < self.last_overall_percent:
            return self.last_overall_percent
        self.last_overall_percent = overall
        return max(0.0, min(100.0, overall))

    def overall_eta(self, overall_percent: float | None) -> str | None:
        if overall_percent is None or overall_percent <= 0 or overall_percent >= 100 or self.queue_start_time <= 0:
            return None
        elapsed = time.perf_counter() - self.queue_start_time
        remaining = elapsed * (100.0 - overall_percent) / overall_percent
        return format_duration(remaining)

    def update_phase_from_text(self, text: str, *, fraction: float | None = None) -> None:
        key = self.progress_key_for_text(text)
        self.active_progress_phase_key = key
        overall = self.overall_percent_for_phase(key, fraction)
        fields = {"phase": key, "stage": phase_label(key), "detail": text.strip()}
        if fraction is not None:
            fields["percent"] = f"{max(0.0, min(1.0, fraction)) * 100.0:.3f}"
        if overall is not None:
            fields["overall"] = f"{overall:.3f}"
            eta = self.overall_eta(overall)
            if eta:
                fields["overall_eta"] = eta
        if self.run_queue and self.current_index >= 0:
            fields["item"] = f"{self.current_index + 1}/{len(self.run_queue)}"
        self.progress_dashboard.apply_progress_fields(fields)

    def apply_progress_fields(self, fields: dict[str, str]) -> None:
        display = dict(fields)
        key = self.progress_key_for_text(display.get("phase") or display.get("stage"))
        self.active_progress_phase_key = key
        display.setdefault("stage", phase_label(key))
        fraction = self.progress_fraction_from_fields(display)
        overall = self.overall_percent_for_phase(key, fraction)
        if overall is not None:
            display["overall"] = f"{overall:.3f}"
            eta = self.overall_eta(overall)
            if eta:
                display["overall_eta"] = eta
        if self.run_queue and self.current_index >= 0:
            display.setdefault("item", f"{self.current_index + 1}/{len(self.run_queue)}")
        self.progress_dashboard.apply_progress_fields(display)

    def process_finished(self, exit_code: int, exit_status: QtCore.QProcess.ExitStatus, tool_only: bool) -> None:
        if self.active_log_buffer:
            self.handle_process_line(self.active_log_buffer.rstrip())
            self.active_log_buffer = ""
        self.elapsed_timer.stop()
        self.progress_dashboard.set_phase_done(True)
        self.process_value.setText("NotRunning")
        self.process = None
        self.update_running_state(False)
        success = exit_status == QtCore.QProcess.NormalExit and exit_code == 0 and not self.stopping
        if tool_only:
            self.status_banner.setText("待機中" if success else "エラー")
            self.status_value_label.setText("completed" if success else f"exit {exit_code}")
            self.summary_text.setPlainText(f"status: {'completed' if success else 'error'}\nreturncode: {exit_code}")
            return
        if success:
            item_done = (self.current_index + 1) / max(1, len(self.run_queue)) * 100.0
            self.last_overall_percent = max(self.last_overall_percent, item_done)
            self.progress_dashboard.set_overall_percent(item_done)
            self.status_value_label.setText("completed")
            self.start_next_item()
            return
        self.finish_queue(success=False, exit_code=exit_code)

    def process_error(self, error: QtCore.QProcess.ProcessError) -> None:
        detail = self.process.errorString() if self.process is not None else ""
        self.append_log(f"[process-error] {error}" + (f" {detail}" if detail else ""))
        self.status_value_label.setText("error")
        self.status_banner.setText("エラー")

    def report_local_error(self, title: str, detail: str, *, finish_workflow: bool = False) -> None:
        self.append_log(f"[error] {title}: {detail}")
        if finish_workflow and self.workflow_running:
            self.finish_queue(success=False, exit_code=-1)
        self.status_banner.setText("エラー")
        self.status_value_label.setText(title)
        self.summary_text.setPlainText(f"status: error\n{title}\n{detail}")

    def finish_queue(self, *, success: bool, exit_code: int = 0) -> None:
        self.elapsed_timer.stop()
        self.progress_dashboard.set_phase_done(success)
        self.update_running_state(False)
        self.process_value.setText("NotRunning")
        if success:
            self.status_banner.setText("完了")
            self.phase_value.setText("完了")
            self.progress_dashboard.set_overall_percent(100.0)
            self.status_value_label.setText("completed")
            self.summary_text.setPlainText("status: completed\nqueue: done")
        else:
            self.status_banner.setText("停止" if self.stopping else "エラー")
            self.status_value_label.setText("stopped" if self.stopping else f"exit {exit_code}")
            self.summary_text.setPlainText(f"status: {'stopped' if self.stopping else 'error'}\nreturncode: {exit_code}")
        self.run_queue = []
        self.current_index = -1
        self.workflow_running = False
        self.stopping = False
        self.update_queue_state()

    def stop_process(self) -> None:
        if self.process is None:
            return
        self.stopping = True
        self.status_banner.setText("停止中")
        self.status_value_label.setText("terminating")
        self.process.terminate()
        QtCore.QTimer.singleShot(5000, self.kill_if_running)

    def kill_if_running(self) -> None:
        if self.process is not None and self.process.state() != QtCore.QProcess.NotRunning:
            self.process.kill()

    def process_is_running(self) -> bool:
        return self.process is not None and self.process.state() != QtCore.QProcess.NotRunning

    def update_running_state(self, running: bool) -> None:
        self.start_button.setEnabled(bool(self.queue_paths) and not running)
        self.stop_button.setEnabled(running)
        self.check_artifacts_button.setEnabled(not running)
        self.add_video_button.setEnabled(not running)
        self.add_folder_button.setEnabled(not running)
        self.remove_button.setEnabled(bool(self.queue_paths) and not running)
        self.clear_button.setEnabled(bool(self.queue_paths) and not running)
        self.test_connection_button.setEnabled(not running)
        self.discover_runtime_button.setEnabled(not running)

    def update_postprocess_enabled(self, enabled: bool) -> None:
        widgets = [
            self.class_tabs,
            self.detailed_overlay_check,
            self.simple_overlay_check,
            *self.class_shape_combos.values(),
            *self.class_keyframe_spins.values(),
            *self.class_recall_spins.values(),
            *self.class_confidence_spins.values(),
        ]
        for widget in widgets:
            widget.setEnabled(enabled)

    def toggle_advanced(self, visible: bool) -> None:
        self.advanced_box.setVisible(visible)
        self.advanced_button.setText("詳細を閉じる" if visible else "詳細を開く")

    def append_log(self, text: str) -> None:
        self.log_edit.appendPlainText(text)

    def update_elapsed(self) -> None:
        if self.process_start_time <= 0:
            self.elapsed_value.setText("-")
            return
        elapsed = int(time.perf_counter() - self.process_start_time)
        self.elapsed_value.setText(f"{elapsed // 3600:02d}:{elapsed % 3600 // 60:02d}:{elapsed % 60:02d}")

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self.process_is_running():
            self.stop_process()
        event.accept()


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("DINOv3 Postprocess Windows Frontend")
    window = PipelineUiWindow()
    window.show()
    return app.exec_()
