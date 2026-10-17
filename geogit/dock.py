import glob
import os
import shutil
import tempfile
from datetime import datetime

from qgis.core import (Qgis, QgsApplication, QgsCoordinateTransform, QgsCsException, QgsProject,
                       QgsRectangle, QgsSettings, QgsTask)
from qgis.gui import QgsDockWidget
from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
                                 QHeaderView, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox,
                                 QPlainTextEdit, QPushButton, QSplitter, QStackedWidget, QTabWidget,
                                 QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import git_backend as gb
from . import graph, i18n, vector_diff
from .history_view import GRAPH_ROLE, GraphDelegate, author_icon, graph_width
from .i18n import t

SETTINGS_FOLDER = "geogit/last_folder"
SETTINGS_LANG = "geogit/lang"
PATH_ROLE = Qt.ItemDataRole.UserRole
AUTH_WORDS = ("authentication", "could not read username", "terminal prompts disabled",
              "permission denied", "403", "401")


def _icon(name):
    return QgsApplication.getThemeIcon(name)


KIND_ICONS = {"vector": "/mIconVector.svg", "raster": "/mIconRaster.svg",
              "project": "/mIconQgsProjectFile.svg", "style": "/mIconPaintEffects.svg",
              "other": "/mIconFile.svg"}


class GeoGitDock(QgsDockWidget):
    def __init__(self, iface):
        super().__init__()
        self.iface = iface
        self.folder = ""
        self.repo = None
        self._tasks = []
        self._unchecked = set()
        self._last_status = None
        self._commits = []
        self._path_filter = None
        self.setObjectName("GeoGitDock")
        lang = QgsSettings().value(SETTINGS_LANG, "")
        if lang:
            i18n.set_lang(lang)
        self._build()
        self.retranslate()

        prj = QgsProject.instance()
        prj.readProject.connect(self._project_changed)
        prj.projectSaved.connect(self.refresh)
        self.timer = QTimer(self)
        self.timer.setInterval(15000)
        self.timer.timeout.connect(self._auto_refresh)
        self.timer.start()

        start = prj.absolutePath() or QgsSettings().value(SETTINGS_FOLDER, "")
        self.set_folder(start)

    def cleanup(self):
        self.timer.stop()
        prj = QgsProject.instance()
        for sig, slot in ((prj.readProject, self._project_changed), (prj.projectSaved, self.refresh)):
            try:
                sig.disconnect(slot)
            except TypeError:
                pass

    # --- layout --------------------------------------------------------
    def _build(self):
        root = QWidget()
        lay = QVBoxLayout(root)

        top = QHBoxLayout()
        self.folder_label = QLabel()
        self.folder_label.setWordWrap(True)
        self.folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("ქართ", "ka")
        self.lang_combo.addItem("EN", "en")
        self.lang_combo.setCurrentIndex(1 if i18n.LANG == "en" else 0)
        self.lang_combo.currentIndexChanged.connect(self._lang_changed)
        top.addWidget(self.folder_label, 1)
        top.addWidget(self.lang_combo)
        lay.addLayout(top)

        row = QHBoxLayout()
        self.btn_folder = QPushButton(_icon("/mActionFileOpen.svg"), "")
        self.btn_folder.clicked.connect(self._choose_folder)
        self.btn_project_folder = QPushButton(_icon("/mIconQgsProjectFile.svg"), "")
        self.btn_project_folder.clicked.connect(lambda: self.set_folder(QgsProject.instance().absolutePath()))
        self.btn_clone = QPushButton(_icon("/mActionSharingImport.svg"), "")
        self.btn_clone.clicked.connect(self._clone)
        for b in (self.btn_folder, self.btn_project_folder, self.btn_clone):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)

        # page 0: folder is not a repository
        p0 = QWidget()
        l0 = QVBoxLayout(p0)
        self.not_repo_label = QLabel()
        self.not_repo_label.setWordWrap(True)
        self.btn_init = QPushButton(_icon("/mActionAdd.svg"), "")
        self.btn_init.clicked.connect(self._init_repo)
        l0.addWidget(self.not_repo_label)
        l0.addWidget(self.btn_init)
        l0.addStretch(1)
        self.stack.addWidget(p0)

        # page 1: repository
        p1 = QWidget()
        l1 = QVBoxLayout(p1)
        l1.setContentsMargins(0, 0, 0, 0)

        br = QHBoxLayout()
        self.branch_label = QLabel()
        self.branch_combo = QComboBox()
        self.branch_combo.activated.connect(self._switch_branch)
        self.btn_new_branch = QPushButton(_icon("/mActionAdd.svg"), "")
        self.btn_new_branch.clicked.connect(self._new_branch)
        br.addWidget(self.branch_label)
        br.addWidget(self.branch_combo, 1)
        br.addWidget(self.btn_new_branch)
        l1.addLayout(br)

        rm = QHBoxLayout()
        self.remote_label = QLabel()
        self.remote_label.setWordWrap(True)
        self.btn_remote = QPushButton(_icon("/mActionOptions.svg"), "")
        self.btn_remote.clicked.connect(self._set_remote)
        rm.addWidget(self.remote_label, 1)
        rm.addWidget(self.btn_remote)
        l1.addLayout(rm)

        sync = QHBoxLayout()
        self.ab_label = QLabel()
        self.btn_pull = QPushButton(_icon("/mActionArrowDown.svg"), "")
        self.btn_pull.clicked.connect(self._pull)
        self.btn_push = QPushButton(_icon("/mActionArrowUp.svg"), "")
        self.btn_push.clicked.connect(self._push)
        self.btn_refresh = QPushButton(_icon("/mActionRefresh.svg"), "")
        self.btn_refresh.clicked.connect(self._refresh_and_fetch)
        sync.addWidget(self.ab_label, 1)
        for b in (self.btn_pull, self.btn_push, self.btn_refresh):
            sync.addWidget(b)
        l1.addLayout(sync)

        self.tabs = QTabWidget()
        l1.addWidget(self.tabs, 1)

        # changes tab
        ch = QWidget()
        lc = QVBoxLayout(ch)
        hdr = QHBoxLayout()
        self.chk_all = QCheckBox()
        self.chk_all.setChecked(True)
        self.chk_all.clicked.connect(self._check_all)
        self.count_label = QLabel()
        hdr.addWidget(self.chk_all)
        hdr.addStretch(1)
        hdr.addWidget(self.count_label)
        lc.addLayout(hdr)
        self.changes = QTreeWidget()
        self.changes.setRootIsDecorated(False)
        self.changes.setColumnCount(3)
        self.changes.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.changes.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.changes.itemChanged.connect(self._item_checked)
        lc.addWidget(self.changes, 1)
        self.message = QPlainTextEdit()
        self.message.setMaximumHeight(70)
        lc.addWidget(self.message)
        cb = QHBoxLayout()
        self.btn_commit = QPushButton(_icon("/mActionFileSave.svg"), "")
        self.btn_commit.clicked.connect(self._commit)
        self.btn_discard = QPushButton(_icon("/mActionUndo.svg"), "")
        self.btn_discard.clicked.connect(self._discard)
        cb.addWidget(self.btn_commit, 1)
        cb.addWidget(self.btn_discard)
        lc.addLayout(cb)
        self.tabs.addTab(ch, "")

        # history tab
        hw = QWidget()
        lh = QVBoxLayout(hw)
        flt = QHBoxLayout()
        self.author_combo = QComboBox()
        self.author_combo.currentIndexChanged.connect(self._apply_history_filter)
        self.kind_combo = QComboBox()
        for kind in (None, "vector", "raster", "project", "style", "other"):
            self.kind_combo.addItem(_icon(KIND_ICONS[kind]) if kind else QIcon(), "", kind)
        self.kind_combo.currentIndexChanged.connect(self._apply_history_filter)
        flt.addWidget(self.author_combo, 2)
        flt.addWidget(self.kind_combo, 1)
        lh.addLayout(flt)
        flt2 = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_history_filter)
        self.chk_hide = QCheckBox()
        self.chk_hide.toggled.connect(self._apply_history_filter)
        flt2.addWidget(self.search, 1)
        flt2.addWidget(self.chk_hide)
        lh.addLayout(flt2)
        self.path_bar = QWidget()
        pb = QHBoxLayout(self.path_bar)
        pb.setContentsMargins(0, 0, 0, 0)
        self.path_label = QLabel()
        self.path_label.setWordWrap(True)
        self.btn_clear_path = QPushButton(_icon("/mActionRemove.svg"), "")
        self.btn_clear_path.clicked.connect(lambda: self.show_file_history(None))
        pb.addWidget(self.path_label, 1)
        pb.addWidget(self.btn_clear_path)
        self.path_bar.hide()
        lh.addWidget(self.path_bar)

        hi = QSplitter(Qt.Orientation.Vertical)
        self.history = QTreeWidget()
        self.history.setRootIsDecorated(False)
        self.history.setUniformRowHeights(True)
        self.history.setColumnCount(5)
        self.history.setItemDelegateForColumn(0, GraphDelegate(self.history))
        self.history.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.history.currentItemChanged.connect(self._commit_selected)
        self.commit_files = QTreeWidget()
        self.commit_files.setRootIsDecorated(False)
        self.commit_files.setColumnCount(2)
        self.commit_files.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for tree in (self.commit_files, self.changes):
            tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            tree.customContextMenuRequested.connect(lambda pos, tr=tree: self._file_menu(tr, pos))
            tree.itemDoubleClicked.connect(lambda item, _col, tr=tree: self._file_double_clicked(tr, item))
        hi.addWidget(self.history)
        hi.addWidget(self.commit_files)
        hi.setSizes([300, 120])
        lh.addWidget(hi, 1)
        self.tabs.addTab(hw, "")

        self.stack.addWidget(p1)
        self.setWidget(root)

    def retranslate(self):
        self.setWindowTitle(t("title"))
        self.lang_combo.setToolTip(t("lang"))
        self.btn_folder.setText(t("choose_folder"))
        self.btn_project_folder.setText(t("project_folder"))
        self.btn_clone.setText(t("clone"))
        self.not_repo_label.setText(t("not_repo"))
        self.btn_init.setText(t("init"))
        self.branch_label.setText(t("branch") + ":")
        self.btn_new_branch.setToolTip(t("new_branch"))
        self.btn_remote.setToolTip(t("set_remote"))
        self.btn_pull.setText(t("pull"))
        self.btn_push.setText(t("push"))
        self.btn_refresh.setToolTip(t("refresh"))
        self.chk_all.setText(t("select_all"))
        self.changes.setHeaderLabels([t("col_state"), t("col_kind"), t("col_path")])
        self.message.setPlaceholderText(t("message_ph"))
        self.btn_commit.setText(t("commit"))
        self.btn_discard.setText(t("discard"))
        self.tabs.setTabText(0, t("changes"))
        self.tabs.setTabText(1, t("history"))
        self.changes.setToolTip(t("dblclick_hint"))
        self.commit_files.setToolTip(t("dblclick_hint"))
        self.history.setHeaderLabels(["", t("col_message"), t("col_author"), t("col_date"), t("col_id")])
        self.commit_files.setHeaderLabels([t("col_state"), t("files_in_commit")])
        self.kind_combo.setItemText(0, t("all_kinds"))
        for i in range(1, self.kind_combo.count()):
            self.kind_combo.setItemText(i, t("kind_" + self.kind_combo.itemData(i)))
        self.search.setPlaceholderText(t("search_ph"))
        self.chk_hide.setText(t("hide_others"))
        self.btn_clear_path.setToolTip(t("clear_filter"))
        self.refresh()

    def _lang_changed(self):
        lang = self.lang_combo.currentData()
        i18n.set_lang(lang)
        QgsSettings().setValue(SETTINGS_LANG, lang)
        self.retranslate()

    # --- state ---------------------------------------------------------
    def set_folder(self, path):
        self.folder = path or ""
        root = gb.find_repo_root(self.folder) if self.folder else None
        self.repo = gb.GitRepo(root) if root else None
        if self.folder:
            QgsSettings().setValue(SETTINGS_FOLDER, self.folder)
        self._unchecked.clear()
        self.refresh(full=True)

    def _project_changed(self):
        path = QgsProject.instance().absolutePath()
        if path:
            self.set_folder(path)

    def _auto_refresh(self):
        if self.isVisible() and not self._tasks and self.repo:
            self.refresh(full=False)

    def refresh(self, full=True):
        if not hasattr(self, "stack"):
            return
        shown = (self.repo.path if self.repo else self.folder) or t("no_folder")
        self.folder_label.setText(f"<b>{t('folder')}:</b> {shown}")
        has_folder = bool(self.folder)
        self.btn_project_folder.setEnabled(bool(QgsProject.instance().absolutePath()))
        if not gb.git_available():
            self.not_repo_label.setText(t("no_git"))
            self.btn_init.setEnabled(False)
            self.stack.setCurrentIndex(0)
            return
        if not self.repo:
            self.not_repo_label.setText(t("not_repo") if has_folder else t("no_folder"))
            self.btn_init.setEnabled(has_folder)
            self.stack.setCurrentIndex(0)
            return
        self.stack.setCurrentIndex(1)
        try:
            self._fill_changes(force=full)
            self._fill_branch()
            if full:
                self._fill_history()
        except gb.GitError as e:
            self.iface.messageBar().pushMessage(t("error"), str(e), Qgis.MessageLevel.Warning, 8)

    def _fill_branch(self):
        info = self.repo.branch_info()
        self.branch_combo.blockSignals(True)
        self.branch_combo.clear()
        for b in self.repo.branches() or [info.name]:
            self.branch_combo.addItem(b)
        self.branch_combo.setCurrentText(info.name)
        self.branch_combo.blockSignals(False)
        url = self.repo.remote_url()
        self.remote_label.setText(f"<b>{t('remote')}:</b> {url or t('no_remote')}")
        self.btn_push.setEnabled(bool(url))
        self.btn_pull.setEnabled(bool(url))
        self.ab_label.setText(t("ahead_behind", info.ahead, info.behind) if info.upstream else "")

    def _fill_changes(self, force=False):
        changes = self.repo.status()
        key = (self.repo.path, tuple((c.path, c.index, c.worktree) for c in changes))
        if key == self._last_status and not force:
            return
        self._last_status = key
        self.changes.blockSignals(True)
        self.changes.clear()
        for ch in sorted(changes, key=lambda c: c.path.lower()):
            it = QTreeWidgetItem([t("state_" + ch.code), t("kind_" + ch.kind), ch.path])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(0, Qt.CheckState.Unchecked if ch.path in self._unchecked
                             else Qt.CheckState.Checked)
            it.setIcon(1, _icon(KIND_ICONS[ch.kind]))
            it.setData(0, PATH_ROLE, ch.path)
            if ch.orig_path:
                it.setToolTip(2, f"{ch.orig_path} → {ch.path}")
            self.changes.addTopLevelItem(it)
        self.changes.blockSignals(False)
        for col in (0, 1):
            self.changes.resizeColumnToContents(col)
        self.count_label.setText(t("n_changes", len(changes)) if changes else t("no_changes"))
        self.btn_commit.setEnabled(bool(changes))
        self.btn_discard.setEnabled(bool(changes))

    def _fill_history(self):
        current = self.history.currentItem()
        current_sha = current.data(1, PATH_ROLE) if current else None
        self.history.clear()
        self.commit_files.clear()
        self._commits = self.repo.log(path=self._path_filter)
        rows = graph.layout(self._commits)
        authors = {}
        for c, row in zip(self._commits, rows):
            key = c.email.lower() or c.author
            name, count = authors.get(key, (c.author, 0))
            authors[key] = (name, count + 1)
            refs = [r.replace("HEAD -> ", "") for r in c.refs if r != "HEAD"]
            text = (f"[{', '.join(refs)}] " if refs else "") + c.subject
            when = datetime.fromtimestamp(c.time).strftime("%d.%m.%y %H:%M")
            color = graph.author_color(key)
            it = QTreeWidgetItem(["", text, c.author, when, c.sha[:8]])
            it.setData(0, GRAPH_ROLE, (row, color, False))
            it.setData(1, PATH_ROLE, c.sha)
            it.setIcon(2, author_icon(color))
            it.setToolTip(2, f"{c.author} <{c.email}>")
            shown = c.files[:15] + (["…"] if len(c.files) > 15 else [])
            it.setToolTip(1, f"<b>{c.subject}</b><br>{c.author} · {when}<br><br>" + "<br>".join(shown))
            self.history.addTopLevelItem(it)
            if c.sha == current_sha:
                self.history.setCurrentItem(it)
        self.history.setColumnWidth(0, graph_width(rows))
        for col in (2, 3, 4):
            self.history.resizeColumnToContents(col)

        selected = self.author_combo.currentData()
        self.author_combo.blockSignals(True)
        self.author_combo.clear()
        self.author_combo.addItem(t("all_authors", len(self._commits)), None)
        for key, (name, count) in sorted(authors.items(), key=lambda kv: -kv[1][1]):
            self.author_combo.addItem(author_icon(graph.author_color(key)), f"{name} ({count})", key)
        idx = self.author_combo.findData(selected)
        self.author_combo.setCurrentIndex(max(idx, 0))
        self.author_combo.blockSignals(False)
        self._apply_history_filter()

    def _apply_history_filter(self):
        author = self.author_combo.currentData()
        kind = self.kind_combo.currentData()
        text = self.search.text().strip().lower()
        hide = self.chk_hide.isChecked()
        active = bool(author or kind or text)
        self.history.setColumnHidden(0, hide and active)
        for i, c in enumerate(self._commits):
            it = self.history.topLevelItem(i)
            ok = ((not author or (c.email.lower() or c.author) == author)
                  and (not kind or any(gb.file_kind(f) == kind for f in c.files))
                  and (not text or text in c.subject.lower() or c.sha.startswith(text)
                       or any(text in f.lower() for f in c.files)))
            row, color, _ = it.data(0, GRAPH_ROLE)
            it.setData(0, GRAPH_ROLE, (row, color, not ok))
            it.setHidden(hide and not ok)
            fg = self.history.palette().color(self.history.foregroundRole())
            if not ok:
                fg.setAlpha(110)
            for col in range(1, 5):
                it.setForeground(col, fg)

    def show_file_history(self, path):
        self._path_filter = path or None
        self.path_bar.setVisible(bool(path))
        if path:
            self.path_label.setText(f"<b>{t('file_history')}:</b> {path}")
            self.tabs.setCurrentIndex(1)
        if self.repo:
            self._fill_history()

    def _file_menu(self, tree, pos):
        item = tree.itemAt(pos)
        if not item or not self.repo:
            return
        path = item.data(0, PATH_ROLE)
        menu = QMenu(tree)
        if gb.vector_main_file(path):
            sha = self._selected_sha() if tree is self.commit_files else None
            menu.addAction(_icon("/mActionShowAllLayers.svg"), t("show_on_map"),
                           lambda: self.show_vector_diff(path, sha))
        menu.addAction(_icon("/mActionHistory.svg"), t("show_file_history"),
                       lambda: self.show_file_history(path))
        menu.exec(tree.viewport().mapToGlobal(pos))

    def _selected_sha(self):
        item = self.history.currentItem()
        return item.data(1, PATH_ROLE) if item else None

    def _file_double_clicked(self, tree, item):
        path = item.data(0, PATH_ROLE)
        if self.repo and path and gb.vector_main_file(path):
            self.show_vector_diff(path, self._selected_sha() if tree is self.commit_files else None)

    def show_vector_diff(self, path, sha=None):
        """Compare a vector file in commit sha with its parent (sha None: unsaved vs HEAD)."""
        main = gb.vector_main_file(path)
        repo = self.repo
        label = sha[:8] if sha else t("working_copy")

        def work():
            tmp = tempfile.mkdtemp(prefix="geogit-diff-")
            try:
                if sha:
                    old_rev = repo.first_parent(sha)
                    new = repo.export_version(sha, main, os.path.join(tmp, "new"))
                else:
                    old_rev = "HEAD" if repo.has_commits() else None
                    new = os.path.join(repo.path, main)
                    new = new if os.path.exists(new) else None
                old = repo.export_version(old_rev, main, os.path.join(tmp, "old")) if old_rev else None
                return vector_diff.diff_files(old, new)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

        self._run(t("comparing") + f" {main}", work, after=lambda diffs: self._show_diffs(main, label, diffs))

    def _show_diffs(self, main, label, diffs):
        from .diff_layer import add_to_project, build_layer
        changed = [d for d in diffs if d.changed]
        bar = self.iface.messageBar()
        if not changed:
            bar.pushMessage("GeoGit", t("no_vector_changes", main), Qgis.MessageLevel.Info, 6)
            return
        layers = [build_layer(d, f"{d.name} · {label}") for d in changed]
        add_to_project(layers, f"GeoGit: {main} · {label}")
        for d in changed:
            msg = t("diff_summary", d.name, len(d.added), len(d.modified), len(d.deleted))
            if d.fields_added or d.fields_removed:
                msg += t("diff_fields", ", ".join(d.fields_added) or "–", ", ".join(d.fields_removed) or "–")
            bar.pushMessage("GeoGit", msg, Qgis.MessageLevel.Success, 10)
        self._zoom_to(layers)

    def _zoom_to(self, layers):
        canvas = self.iface.mapCanvas()
        if canvas is None:
            return
        project = QgsProject.instance()
        extent = QgsRectangle()
        for layer in layers:
            if layer.isSpatial() and not layer.extent().isNull():
                tr = QgsCoordinateTransform(layer.crs(), canvas.mapSettings().destinationCrs(), project)
                try:
                    extent.combineExtentWith(tr.transformBoundingBox(layer.extent()))
                except QgsCsException:
                    continue
        if extent.isNull():
            return
        if extent.width() == 0 and extent.height() == 0:
            canvas.setCenter(extent.center())
        else:
            extent.scale(1.2)
            canvas.setExtent(extent)
        canvas.refresh()

    def _commit_selected(self, item, _prev=None):
        self.commit_files.clear()
        if not item or not self.repo:
            return
        try:
            files = self.repo.commit_files(item.data(1, PATH_ROLE))
        except gb.GitError:
            return
        for state, path in files:
            code = state[0] if state[0] in "AMDRU" else "M"
            it = QTreeWidgetItem([t("state_" + code), path])
            it.setIcon(1, _icon(KIND_ICONS[gb.file_kind(path)]))
            it.setData(0, PATH_ROLE, path)
            self.commit_files.addTopLevelItem(it)
        self.commit_files.resizeColumnToContents(0)

    def _item_checked(self, item, column):
        if column != 0:
            return
        path = item.data(0, PATH_ROLE)
        if item.checkState(0) == Qt.CheckState.Checked:
            self._unchecked.discard(path)
        else:
            self._unchecked.add(path)

    def _check_all(self, checked):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self.changes.topLevelItemCount()):
            self.changes.topLevelItem(i).setCheckState(0, state)

    def _checked_paths(self):
        items = [self.changes.topLevelItem(i) for i in range(self.changes.topLevelItemCount())]
        checked = [it.data(0, PATH_ROLE) for it in items if it.checkState(0) == Qt.CheckState.Checked]
        return checked, len(checked) == len(items)

    # --- background work -----------------------------------------------
    def _run(self, label, func, done_text=None, after=None):
        self._set_busy(True)
        self.iface.messageBar().pushMessage("GeoGit", t("working", label), Qgis.MessageLevel.Info, 3)

        def work(_task):
            return func()

        def finished(exception, result=None):
            self._tasks = [x for x in self._tasks if x is not task]
            self._set_busy(False)
            if exception is not None:
                self._show_error(exception)
            else:
                if done_text:
                    msg = done_text(result) if callable(done_text) else done_text
                    self.iface.messageBar().pushMessage("GeoGit", msg, Qgis.MessageLevel.Success, 5)
                if after:
                    after(result)
            self.refresh()

        task = QgsTask.fromFunction(f"GeoGit: {label}", work, on_finished=finished)
        self._tasks.append(task)
        QgsApplication.taskManager().addTask(task)

    def _set_busy(self, busy):
        for w in (self.btn_commit, self.btn_discard, self.btn_pull, self.btn_push, self.btn_refresh,
                  self.btn_init, self.btn_clone, self.branch_combo, self.btn_new_branch):
            w.setEnabled(not busy)

    def _show_error(self, exc):
        text = str(exc)
        if any(w in text.lower() for w in AUTH_WORDS):
            text += t("auth_hint")
        QMessageBox.warning(self, t("error"), text)

    # --- actions -------------------------------------------------------
    def _choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, t("folder"), self.folder)
        if path:
            self.set_folder(path)

    def _init_repo(self):
        repo = gb.GitRepo(self.folder)
        try:
            repo.init()
        except gb.GitError as e:
            self._show_error(e)
            return
        if not repo.lfs_available():
            QMessageBox.information(self, "Git LFS", t("no_lfs"))
        self.set_folder(self.folder)

    def _clone(self):
        url, ok = QInputDialog.getText(self, t("clone"), t("clone_url"))
        url = url.strip()
        if not ok or not url:
            return
        parent = QFileDialog.getExistingDirectory(self, t("clone_dest"),
                                                  os.path.dirname(self.folder) if self.folder else "")
        if not parent:
            return
        name = os.path.basename(url.rstrip("/"))
        if name.endswith(".git"):
            name = name[:-4]
        dest = os.path.join(parent, name or "project")

        def after(_):
            self.set_folder(dest)
            projects = glob.glob(os.path.join(dest, "*.qgz")) + glob.glob(os.path.join(dest, "*.qgs"))
            if len(projects) == 1:
                self.iface.addProject(projects[0])

        self._run(t("clone"), lambda: gb.clone(url, dest), t("cloned", dest), after)

    def _ensure_identity(self):
        name, email = self.repo.identity()
        if name and email:
            return True
        name, ok = QInputDialog.getText(self, "GeoGit", t("identity_name"), QLineEdit.EchoMode.Normal, name)
        if not ok or not name.strip():
            return False
        email, ok = QInputDialog.getText(self, "GeoGit", t("identity_email"), QLineEdit.EchoMode.Normal, email)
        if not ok or not email.strip():
            return False
        self.repo.set_identity(name.strip(), email.strip(), global_=True)
        return True

    def _project_in_repo(self):
        path = QgsProject.instance().fileName()
        if path and self.repo and os.path.abspath(path).startswith(self.repo.path + os.sep):
            return path
        return ""

    def _maybe_save_project(self):
        if self._project_in_repo() and QgsProject.instance().isDirty():
            ans = QMessageBox.question(self, "GeoGit", t("save_project_q"))
            if ans == QMessageBox.StandardButton.Yes:
                QgsProject.instance().write()
                self._fill_changes()

    def _commit(self):
        self._maybe_save_project()
        msg = self.message.toPlainText().strip()
        if not msg:
            QMessageBox.information(self, "GeoGit", t("need_message"))
            return
        paths, everything = self._checked_paths()
        if not paths:
            QMessageBox.information(self, "GeoGit", t("need_selection"))
            return
        if not self._ensure_identity():
            return
        repo = self.repo

        def after(_):
            self.message.clear()
            self._unchecked.clear()

        self._run(t("commit"), lambda: repo.commit(msg, None if everything else paths),
                  lambda sha: t("committed", sha[:8]), after)

    def _discard(self):
        selected = [it.data(0, PATH_ROLE) for it in self.changes.selectedItems()]
        paths = selected or self._checked_paths()[0]
        if not paths:
            return
        if QMessageBox.question(self, "GeoGit", t("discard_q", len(paths))) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.repo.discard(paths)
        except gb.GitError as e:
            self._show_error(e)
        self.refresh()

    def _pull(self):
        repo = self.repo
        project = self._project_in_repo()
        before = os.path.getmtime(project) if project else None

        def after(_):
            if project and os.path.exists(project) and os.path.getmtime(project) != before:
                if QMessageBox.question(self, "GeoGit", t("reload_project_q")) == QMessageBox.StandardButton.Yes:
                    QgsProject.instance().read(project)

        self._run(t("pull"), repo.pull, t("pulled"), after)

    def _push(self):
        self._run(t("push"), self.repo.push, t("pushed"))

    def _refresh_and_fetch(self):
        self.refresh()
        if self.repo and self.repo.remote_url():
            self._run(t("refresh"), self.repo.fetch)

    def _set_remote(self):
        url, ok = QInputDialog.getText(self, t("set_remote"), t("remote_url"), QLineEdit.EchoMode.Normal,
                                       self.repo.remote_url())
        if ok and url.strip():
            try:
                self.repo.set_remote(url.strip())
            except gb.GitError as e:
                self._show_error(e)
            self.refresh(full=False)

    def _switch_branch(self, _index):
        name = self.branch_combo.currentText()
        if not name or name == self.repo.branch_info().name:
            return
        try:
            self.repo.switch(name)
        except gb.GitError as e:
            self._show_error(e)
        self.refresh()

    def _new_branch(self):
        name, ok = QInputDialog.getText(self, t("new_branch"), t("new_branch_name"))
        if ok and name.strip():
            try:
                self.repo.create_branch(name.strip())
            except gb.GitError as e:
                self._show_error(e)
            self.refresh()
