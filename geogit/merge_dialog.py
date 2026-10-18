"""Window for settling merge conflicts: whole files, or GeoPackages feature by feature."""

import os
import shutil
import tempfile

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QBrush, QColor
from qgis.PyQt.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
                                 QLabel, QMessageBox, QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem,
                                 QVBoxLayout, QWidget)

from . import git_backend as gb
from . import vector_diff, vector_merge
from .i18n import t

PATH_ROLE = Qt.ItemDataRole.UserRole


def group_conflicts(paths):
    """{dataset main file: [conflicted member files]} so a shapefile is settled as one."""
    groups = {}
    for p in paths:
        main = gb.vector_main_file(p) if os.path.splitext(p)[1].lower() in gb.SHP_SIDECARS else p
        groups.setdefault(main or p, []).append(p)
    return groups


def _summary(f):
    if f is None:
        return t("mg_deleted")
    parts = [f"{k}={v}" for k, v in list(f.attrs.items())[:4]]
    return ", ".join(parts)


class ConflictDialog(QDialog):
    def __init__(self, dock, parent=None):
        super().__init__(parent or dock)
        self.dock = dock
        self.repo = dock.repo
        self.setWindowTitle(t("mg_title"))
        self.resize(760, 520)
        self._work = tempfile.mkdtemp(prefix="geogit-merge-")
        self._sides = {}          # path -> (base, ours, theirs) exported files
        self._pending = {}        # path -> [FeatureConflict]

        lay = QVBoxLayout(self)
        self.info = QLabel()
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        split = QSplitter(Qt.Orientation.Vertical)

        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        self.files = QTreeWidget()
        self.files.setRootIsDecorated(False)
        self.files.setColumnCount(3)
        self.files.setHeaderLabels([t("col_path"), t("col_kind"), t("col_state")])
        self.files.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.files.currentItemChanged.connect(self._selected)
        tl.addWidget(self.files)
        row = QHBoxLayout()
        self.btn_ours = QPushButton(t("mg_keep_ours"))
        self.btn_ours.clicked.connect(lambda: self._take_side("ours"))
        self.btn_theirs = QPushButton(t("mg_take_theirs"))
        self.btn_theirs.clicked.connect(lambda: self._take_side("theirs"))
        self.btn_features = QPushButton(QgsApplication.getThemeIcon("/mActionMergeFeatures.svg"), t("mg_feature_merge"))
        self.btn_features.clicked.connect(self._feature_merge)
        self.btn_map = QPushButton(QgsApplication.getThemeIcon("/mActionShowAllLayers.svg"), t("mg_show_map"))
        self.btn_map.clicked.connect(self._show_on_map)
        for b in (self.btn_ours, self.btn_theirs, self.btn_features, self.btn_map):
            row.addWidget(b)
        row.addStretch(1)
        tl.addLayout(row)
        split.addWidget(top)

        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        self.feat_label = QLabel()
        self.feat_label.setWordWrap(True)
        bl.addWidget(self.feat_label)
        self.features = QTreeWidget()
        self.features.setRootIsDecorated(False)
        self.features.setColumnCount(6)
        self.features.setHeaderLabels([t("mg_col_layer"), "FID", t("mg_col_what"), t("mg_col_mine"),
                                       t("mg_col_theirs"), t("mg_col_keep")])
        bl.addWidget(self.features, 1)
        frow = QHBoxLayout()
        self.btn_all_ours = QPushButton(t("mg_all_mine"))
        self.btn_all_ours.clicked.connect(lambda: self._set_all(0))
        self.btn_all_theirs = QPushButton(t("mg_all_theirs"))
        self.btn_all_theirs.clicked.connect(lambda: self._set_all(1))
        self.btn_apply = QPushButton(t("mg_apply"))
        self.btn_apply.clicked.connect(self._apply_choices)
        for b in (self.btn_all_ours, self.btn_all_theirs):
            frow.addWidget(b)
        frow.addStretch(1)
        frow.addWidget(self.btn_apply)
        bl.addLayout(frow)
        split.addWidget(bottom)
        split.setSizes([220, 260])
        lay.addWidget(split, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.finished.connect(lambda _: shutil.rmtree(self._work, ignore_errors=True))
        self.reload()

    # --- file list -----------------------------------------------------
    def reload(self):
        conflicts = self.repo.conflicts()
        self._groups = group_conflicts(conflicts)
        current = self.files.currentItem().data(0, PATH_ROLE) if self.files.currentItem() else None
        self.files.clear()
        for main, members in sorted(self._groups.items()):
            pending = main in self._pending
            state = t("mg_state_features", len(self._pending[main])) if pending else t("state_U")
            it = QTreeWidgetItem([main, t("kind_" + gb.file_kind(main)), state])
            it.setData(0, PATH_ROLE, main)
            it.setForeground(2, QBrush(QColor("#e67e00" if pending else "#d62728")))
            if len(members) > 1:
                it.setToolTip(0, "\n".join(members))
            self.files.addTopLevelItem(it)
            if main == current:
                self.files.setCurrentItem(it)
        if not self.files.currentItem() and self.files.topLevelItemCount():
            self.files.setCurrentItem(self.files.topLevelItem(0))
        head = self.repo.merge_head_name()
        if conflicts:
            self.info.setText(t("mg_info", head, len(self._groups)))
        else:
            self.info.setText(t("mg_all_done", head))
        self._selected(self.files.currentItem())
        self.dock.refresh()

    def _current(self):
        it = self.files.currentItem()
        return it.data(0, PATH_ROLE) if it is not None else None

    def _selected(self, item, _prev=None):
        path = item.data(0, PATH_ROLE) if item is not None else None
        for b in (self.btn_ours, self.btn_theirs):
            b.setEnabled(bool(path))
        mergeable = bool(path) and os.path.splitext(path)[1].lower() in (".gpkg", ".sqlite")
        self.btn_features.setEnabled(mergeable)
        self.btn_map.setEnabled(bool(path) and bool(gb.vector_main_file(path)))
        self._fill_features(path)

    # --- whole-file resolution -----------------------------------------
    def _take_side(self, side):
        path = self._current()
        if not path:
            return
        try:
            for member in self._groups.get(path, [path]):
                self.repo.resolve(member, side)
        except gb.GitError as e:
            QMessageBox.warning(self, t("error"), str(e))
        self._pending.pop(path, None)
        self.reload()

    # --- feature-level merge -------------------------------------------
    def _export_sides(self, path):
        if path not in self._sides:
            out = []
            for stage in (1, 2, 3):
                out.append(self.repo.export_stage(stage, path, os.path.join(self._work, str(stage))))
            self._sides[path] = tuple(out)
        return self._sides[path]

    def _run_merge(self, path, choices=None):
        base, ours, theirs = self._export_sides(path)
        target = os.path.join(self.repo.path, path)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            return vector_merge.merge_files(base, ours, theirs, target, choices)
        finally:
            QApplication.restoreOverrideCursor()

    def _feature_merge(self):
        path = self._current()
        try:
            report = self._run_merge(path)
        except (vector_merge.MergeUnsupported, ValueError, gb.GitError) as e:
            QMessageBox.information(self, t("mg_title"), t("mg_unsupported", e))
            return
        self._after_merge(path, report)

    def _after_merge(self, path, report):
        if report.ok:
            self.repo.mark_resolved(path)
            self._pending.pop(path, None)
            self.dock.iface.messageBar().pushMessage(
                "GeoGit", t("mg_merged", path, report.applied, report.merged_fields), duration=8)
        else:
            self._pending[path] = report.conflicts
        self.reload()

    def _fill_features(self, path):
        conflicts = self._pending.get(path, [])
        self.features.clear()
        for c in conflicts:
            what = t("mg_kind_" + c.kind) + (f": {', '.join(c.fields)}" if c.fields else "")
            it = QTreeWidgetItem([c.layer, str(c.fid), what, _summary(c.ours), _summary(c.theirs), ""])
            it.setToolTip(3, _summary(c.ours))
            it.setToolTip(4, _summary(c.theirs))
            self.features.addTopLevelItem(it)
            combo = QComboBox()
            combo.addItem(t("mg_mine"), "ours")
            combo.addItem(t("mg_theirs"), "theirs")
            self.features.setItemWidget(it, 5, combo)
        for col in (0, 1, 2):
            self.features.resizeColumnToContents(col)
        has = bool(conflicts)
        self.feat_label.setText(t("mg_feat_info", len(conflicts)) if has else t("mg_feat_hint"))
        for b in (self.btn_all_ours, self.btn_all_theirs, self.btn_apply):
            b.setEnabled(has)

    def _set_all(self, index):
        for i in range(self.features.topLevelItemCount()):
            self.features.itemWidget(self.features.topLevelItem(i), 5).setCurrentIndex(index)

    def _apply_choices(self):
        path = self._current()
        conflicts = self._pending.get(path, [])
        choices = {}
        for i, c in enumerate(conflicts):
            combo = self.features.itemWidget(self.features.topLevelItem(i), 5)
            choices[(c.layer, c.fid)] = combo.currentData()
        try:
            report = self._run_merge(path, choices)
        except (vector_merge.MergeUnsupported, ValueError) as e:
            QMessageBox.warning(self, t("error"), str(e))
            return
        self._after_merge(path, report)

    # --- look at both sides on the map ---------------------------------
    def _show_on_map(self):
        path = self._current()
        try:
            base, ours, theirs = self._export_sides(gb.vector_main_file(path))
        except gb.GitError as e:
            QMessageBox.warning(self, t("error"), str(e))
            return
        self.dock._show_diffs(path, t("mg_mine"), vector_diff.diff_files(base, ours))
        self.dock._show_diffs(path, t("mg_theirs"), vector_diff.diff_files(base, theirs))
