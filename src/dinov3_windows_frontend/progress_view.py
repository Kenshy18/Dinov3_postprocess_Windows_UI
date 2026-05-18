from __future__ import annotations

import time

from PyQt5 import QtCore, QtWidgets


PHASE_LABELS = {
    "normalize_input": "入力正規化",
    "inference": "推論",
    "detector": "推論",
    "raw_sqlite": "推論結果SQLite作成",
    "postprocess": "後処理",
    "raw_overlay": "後処理前オーバーレイ",
    "detailed_overlay": "後処理後オーバーレイ（詳細）",
    "simple_overlay": "後処理後オーバーレイ（簡易）",
    "command": "コマンド",
    "unknown": "処理中",
}


class StatusBanner(QtWidgets.QLabel):
    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self.setObjectName("statusBanner")
        self.setProperty("state", self._detect_state(text))
        self.setAlignment(QtCore.Qt.AlignCenter)

    @staticmethod
    def _detect_state(text: str) -> str:
        value = (text or "").strip()
        if not value:
            return "idle"
        if "エラー" in value or "失敗" in value:
            return "error"
        if "停止" in value:
            return "stopped"
        if "完了" in value:
            return "done"
        if "実行中" in value or "起動中" in value:
            return "running"
        if "待機" in value:
            return "idle"
        return "running"

    def setText(self, text: str) -> None:  # type: ignore[override]
        state = self._detect_state(text)
        if self.property("state") != state:
            self.setProperty("state", state)
            style = self.style()
            if style is not None:
                style.unpolish(self)
                style.polish(self)
        super().setText(text)


def progress_float(fields: dict[str, str], key: str) -> float | None:
    value = fields.get(key)
    if value in (None, "", "-"):
        return None
    try:
        return float(value.rstrip("%"))
    except ValueError:
        return None


def phase_key(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return "unknown"
    normalized = text.lower().replace("-", "_").replace(" ", "_")
    if "normalize" in normalized:
        return "normalize_input"
    if "detailed_overlay" in normalized or ("overlay" in normalized and "detailed" in normalized):
        return "detailed_overlay"
    if "simple_overlay" in normalized or ("overlay" in normalized and "simple" in normalized):
        return "simple_overlay"
    if "raw_overlay" in normalized or ("overlay" in normalized and "raw" in normalized):
        return "raw_overlay"
    if "raw_sqlite" in normalized or ("sqlite" in normalized and "raw" in normalized):
        return "raw_sqlite"
    if "postprocess" in normalized or "atosyori" in normalized:
        return "postprocess"
    if any(token in normalized for token in ("inference", "infer", "detect", "detector", "codino", "dinov3", "eva02")):
        return "inference"
    if "command" in normalized:
        return "command"
    return normalized


def phase_label(value: str | None) -> str:
    key = phase_key(value)
    return PHASE_LABELS.get(key, (value or "-").strip() or "-")


def progress_percent(fields: dict[str, str], *keys: str) -> float | None:
    for key in keys:
        value = progress_float(fields, key)
        if value is not None:
            return max(0.0, min(100.0, value))
    return None


class ProgressDashboard(QtWidgets.QGroupBox):
    EXPORTS = (
        "status_banner",
        "total_progress",
        "phase_progress",
        "current_video_value",
        "speed_value",
        "detail_info_value",
        "elapsed_value",
        "process_value",
        "stage_value",
        "frame_value",
        "phase_value",
        "phase_elapsed_value",
        "status_value_label",
        "count_value",
        "heartbeat_value",
        "remaining_value",
        "overall_remaining_value",
        "output_value",
        "command_value",
        "summary_text",
    )

    def __init__(self) -> None:
        super().__init__("実行状況")
        self._build()

    def _build(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(10, 12, 10, 10)
        outer.setSpacing(8)

        self.status_banner = StatusBanner("待機中")
        outer.addWidget(self.status_banner)

        scroll = QtWidgets.QScrollArea()
        scroll.setObjectName("dashboardScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll, 1)

        container = QtWidgets.QWidget()
        container.setObjectName("dashboardContent")
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        scroll.setWidget(container)

        progress_grid = QtWidgets.QGridLayout()
        progress_grid.setHorizontalSpacing(10)
        progress_grid.setVerticalSpacing(4)
        self.total_progress = QtWidgets.QProgressBar()
        self.total_progress.setObjectName("totalProgress")
        self.phase_progress = QtWidgets.QProgressBar()
        self.phase_progress.setObjectName("phaseProgress")
        self.total_progress.setRange(0, 1)
        self.phase_progress.setRange(0, 1)
        progress_grid.addWidget(self._key_label("全体"), 0, 0)
        progress_grid.addWidget(self.total_progress, 0, 1)
        progress_grid.addWidget(self._key_label("フェーズ"), 1, 0)
        progress_grid.addWidget(self.phase_progress, 1, 1)
        progress_grid.setColumnStretch(1, 1)
        layout.addLayout(progress_grid)

        metrics = QtWidgets.QGridLayout()
        metrics.setHorizontalSpacing(6)
        metrics.setVerticalSpacing(6)
        self.phase_value = self.metric_value("待機")
        self.frame_value = self.metric_value("-")
        self.speed_value = self.metric_value("-")
        self.overall_remaining_value = self.metric_value("-")
        for col, (label, widget) in enumerate(
            (("フェーズ", self.phase_value), ("フレーム", self.frame_value), ("FPS", self.speed_value), ("残り", self.overall_remaining_value))
        ):
            card = QtWidgets.QFrame()
            card.setObjectName("metricCard")
            card_layout = QtWidgets.QVBoxLayout(card)
            card_layout.setContentsMargins(8, 5, 8, 5)
            card_layout.setSpacing(0)
            key = QtWidgets.QLabel(label)
            key.setObjectName("metricKey")
            card_layout.addWidget(key)
            card_layout.addWidget(widget)
            metrics.addWidget(card, 0, col)
            metrics.setColumnStretch(col, 1)
        layout.addLayout(metrics)

        form = QtWidgets.QGridLayout()
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(2)
        self.current_video_value = self.status_value("-")
        self.detail_info_value = self.status_value("-")
        self.elapsed_value = self.status_value("-")
        self.process_value = self.status_value("NotRunning")
        self.stage_value = self.status_value("-")
        self.phase_elapsed_value = self.status_value("-")
        self.status_value_label = self.status_value("-")
        self.count_value = self.status_value("Inference 0 / 0 | Postprocess 0 / 0")
        self.heartbeat_value = self.status_value("-")
        self.remaining_value = self.status_value("-")
        self.output_value = self.status_value("-")
        self.command_value = self.status_value("-")
        rows = [
            ("動画", self.current_video_value, "経過", self.elapsed_value),
            ("詳細", self.detail_info_value, "状態", self.status_value_label),
            ("プロセス", self.process_value, "動画数", self.count_value),
            ("ステージ", self.stage_value, "更新", self.heartbeat_value),
            ("フェーズ経過", self.phase_elapsed_value, "残本数", self.remaining_value),
        ]
        for row, (left_label, left_widget, right_label, right_widget) in enumerate(rows):
            form.addWidget(self.status_key(left_label), row, 0)
            form.addWidget(left_widget, row, 1)
            form.addWidget(self.status_key(right_label), row, 2)
            form.addWidget(right_widget, row, 3)
        last_row = len(rows)
        form.addWidget(self.status_key("実行Dir"), last_row, 0)
        form.addWidget(self.output_value, last_row, 1, 1, 3)
        form.setColumnStretch(0, 0)
        form.setColumnStretch(1, 5)
        form.setColumnStretch(2, 0)
        form.setColumnStretch(3, 4)
        layout.addLayout(form)

        self.summary_text = QtWidgets.QPlainTextEdit()
        self.summary_text.setObjectName("summaryText")
        self.summary_text.setReadOnly(True)
        self.summary_text.setMinimumHeight(38)
        self.summary_text.setMaximumHeight(52)
        self.summary_text.setPlainText("status: idle")
        self.summary_text.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        layout.addWidget(self.summary_text)
        layout.addStretch(0)

    def _key_label(self, text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setObjectName("statusKey")
        label.setMinimumWidth(56)
        return label

    def status_key(self, text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setObjectName("statusKey")
        return label

    def status_value(self, text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setObjectName("statusValue")
        label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        label.setWordWrap(False)
        label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        return label

    def metric_value(self, text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setObjectName("metricValue")
        label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        return label

    def bind_legacy_attributes(self, owner: object) -> None:
        for name in self.EXPORTS:
            setattr(owner, name, getattr(self, name))

    def set_phase_indeterminate(self) -> None:
        self.phase_progress.setRange(0, 0)

    def set_phase_done(self, success: bool = True) -> None:
        self.phase_progress.setRange(0, 1)
        self.phase_progress.setValue(1 if success else 0)

    def set_overall_percent(self, percent: float | None) -> None:
        if percent is None:
            return
        self.total_progress.setRange(0, 1000)
        self.total_progress.setValue(max(0, min(1000, int(round(percent * 10)))))

    def apply_progress_fields(self, fields: dict[str, str]) -> None:
        phase = fields.get("stage") or phase_label(fields.get("phase"))
        self.phase_value.setText(phase)
        self.stage_value.setText(phase)
        percent = progress_percent(fields, "percent", "phase_percent")
        if percent is not None:
            self.phase_progress.setRange(0, 1000)
            self.phase_progress.setValue(max(0, min(1000, int(round(percent * 10)))))
        else:
            self.set_phase_indeterminate()
        self.set_overall_percent(progress_percent(fields, "overall", "overall_percent"))

        current = fields.get("current")
        total = fields.get("total")
        unit = fields.get("unit", "")
        if current and total:
            self.frame_value.setText(f"{current}/{total} {unit}".strip())
        elif current:
            self.frame_value.setText(f"{current} {unit}".strip())
        fps = fields.get("fps")
        compute_fps = fields.get("compute_fps")
        if fps and compute_fps:
            self.speed_value.setText(f"{fps} / {compute_fps}")
        elif fps:
            self.speed_value.setText(fps)
        elif compute_fps:
            self.speed_value.setText(f"compute {compute_fps}")
        if fields.get("elapsed"):
            self.phase_elapsed_value.setText(fields["elapsed"])
        if fields.get("overall_eta"):
            self.overall_remaining_value.setText(fields["overall_eta"])
        elif fields.get("eta"):
            self.overall_remaining_value.setText(f"フェーズ {fields['eta']}")
        detail = self.status_text(fields, phase)
        self.status_banner.setText(f"実行中: {phase}")
        self.summary_text.setPlainText("status: running\n" + detail)
        self.detail_info_value.setText(detail)
        self.heartbeat_value.setText(time.strftime("%Y-%m-%d %H:%M:%S"))

    def status_text(self, fields: dict[str, str], phase: str) -> str:
        pieces = [phase]
        if fields.get("item"):
            pieces.append(f"動画={fields['item']}")
        if fields.get("current") and fields.get("total"):
            pieces.append(f"{fields['current']}/{fields['total']} {fields.get('unit', '')}".strip())
        if fields.get("fps"):
            pieces.append(f"fps={fields['fps']}")
        if fields.get("compute_fps"):
            pieces.append(f"compute={fields['compute_fps']}")
        if fields.get("overall_eta"):
            pieces.append(f"残り={fields['overall_eta']}")
        elif fields.get("eta"):
            pieces.append(f"フェーズ残り={fields['eta']}")
        return " | ".join(pieces)

