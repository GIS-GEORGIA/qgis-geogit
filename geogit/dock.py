import glob
import os
import shutil
import tempfile
from datetime import datetime

from qgis.core import (Qgis, QgsApplication, QgsCoordinateTransform, QgsCsException, QgsProject,
                       QgsRasterLayer, QgsRectangle, QgsSettings, QgsTask, QgsVectorLayer)
from qgis.gui import QgsDockWidget
from qgis.PyQt.QtCore import Qt, QTimer, QUrl
from qgis.PyQt.QtGui import QBrush, QColor, QDesktopServices, QIcon, QPalette
from qgis.PyQt.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout,
                                 QHeaderView, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox,
                                 QPlainTextEdit, QPushButton, QSplitter, QStackedWidget, QTabWidget,
                                 QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import git_backend as gb
from . import graph, i18n, project_diff, raster_diff, vector_diff
from .history_view import GRAPH_ROLE, GraphDelegate, author_icon, graph_width
from .i18n import t

SETTINGS_FOLDER = "geogit/last_folder"
SETTINGS_LANG = "geogit/lang"
PATH_ROLE = Qt.ItemDataRole.UserRole
AUTH_WORDS = ("authentication", "could not read username", "terminal prompts disabled",
              "permission denied", "403", "401")


def _icon(name):
    return QgsApplication.getThemeIcon(name)


def _menu_action(menu, icon, text, slot):
    """menu.addAction(icon, text, slot) without relying on that overload (not in every PyQt6)."""
    action = menu.addAction(icon, text)
    action.triggered.connect(lambda *_: slot())
    return action


KIND_ICONS = {"vector": "/mIconVector.svg", "raster": "/mIconRaster.svg",
              "project": "/mIconQgsProjectFile.svg", "style": "/mIconPaintEffects.svg",
              "other": "/mIconFile.svg"}


class WrapLabel(QLabel):
    """Word-wrapped label that keeps the height its text needs.

    Inside a QTabWidget Qt does not pass height-for-width down, so a plain
    wrapped QLabel gets squeezed to one line and its text is cut.
    """

    def __init__(self, text=""):
        super().__init__(text)
        self.setWordWrap(True)

    def _fit(self):
        self.setMinimumHeight(self.heightForWidth(max(self.width(), 50)))

    def setText(self, text):
        super().setText(text)
        self._fit()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()


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
        self._swipe = None
        self._locks = {}
        self._lfs = False
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
        if self._swipe is not None and self.iface.mapCanvas().mapTool() is self._swipe:
            self.iface.mapCanvas().unsetMapTool(self._swipe)
        self._swipe = None
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
        self.btn_merge = QPushButton(_icon("/mActionMergeFeatures.svg"), "")
        self.btn_merge.clicked.connect(self._merge_branch)
        br.addWidget(self.btn_merge)
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
        self.merge_bar = QFrame()
        self.merge_bar.setFrameShape(QFrame.Shape.StyledPanel)
        self.merge_bar.setAutoFillBackground(True)
        pal = self.merge_bar.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(230, 126, 0, 45))
        self.merge_bar.setPalette(pal)
        mb = QVBoxLayout(self.merge_bar)
        self.merge_label = WrapLabel()
        mb.addWidget(self.merge_label)
        mbr = QHBoxLayout()
        self.btn_resolve = QPushButton(_icon("/mActionMergeFeatures.svg"), "")
        self.btn_resolve.clicked.connect(self.open_conflicts)
        self.btn_abort = QPushButton(_icon("/mActionUndo.svg"), "")
        self.btn_abort.clicked.connect(self._abort_merge)
        mbr.addWidget(self.btn_resolve)
        mbr.addStretch(1)
        mbr.addWidget(self.btn_abort)
        mb.addLayout(mbr)
        self.merge_bar.hide()
        lc.addWidget(self.merge_bar)
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
        self.history.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.history.customContextMenuRequested.connect(self._history_menu)
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

        # locks tab
        lw = QWidget()
        ll = QVBoxLayout(lw)
        self.locks_status = WrapLabel()
        ll.addWidget(self.locks_status)
        self.locks_tree = QTreeWidget()
        self.locks_tree.setRootIsDecorated(False)
        self.locks_tree.setColumnCount(3)
        self.locks_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.locks_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.locks_tree.customContextMenuRequested.connect(
            lambda pos: self._file_menu(self.locks_tree, pos))
        ll.addWidget(self.locks_tree, 1)
        lb = QHBoxLayout()
        self.btn_unlock = QPushButton(_icon("/unlocked.svg"), "")
        self.btn_unlock.clicked.connect(self._unlock_selected)
        self.btn_locks_refresh = QPushButton(_icon("/mActionRefresh.svg"), "")
        self.btn_locks_refresh.clicked.connect(self.load_locks)
        lb.addWidget(self.btn_unlock)
        lb.addStretch(1)
        lb.addWidget(self.btn_locks_refresh)
        ll.addLayout(lb)
        self.tabs.addTab(lw, "")

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
        self.btn_merge.setText(t("merge"))
        self.btn_resolve.setText(t("resolve"))
        self.btn_abort.setText(t("abort_merge"))
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
        self.tabs.setTabText(2, t("locks"))
        self.locks_tree.setHeaderLabels([t("col_path"), t("col_owner"), t("col_since")])
        self.btn_unlock.setText(t("unlock"))
        self.btn_locks_refresh.setText(t("refresh"))
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
        self._locks = {}
        self._lfs = bool(self.repo) and self.repo.lfs_available()
        self.refresh(full=True)
        if self._lfs and self.repo.ensure_lfs():
            # cloned without LFS: large files may still be pointer files
            repo = self.repo
            self._run("Git LFS", repo.lfs_pull, after=lambda _: self.load_locks())
        else:
            self.load_locks()

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
            self._fill_merge_state()
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
            self._mark_lock(it, 2, ch.path)
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
            refs = [r.replace("HEAD -> ", "").replace("tag: ", "🏷 ") for r in c.refs if r != "HEAD"]
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
        if item is None or not self.repo:
            return
        path = item.data(0, PATH_ROLE)
        menu = QMenu(tree)
        if gb.vector_main_file(path) or gb.raster_main_file(path) or gb.file_kind(path) == "project":
            sha = self._selected_sha() if tree is self.commit_files else None
            _menu_action(menu, _icon("/mActionShowAllLayers.svg"), t("show_on_map"),
                           lambda: self.show_changes(path, sha))
        _menu_action(menu, _icon("/mActionHistory.svg"), t("show_file_history"),
                       lambda: self.show_file_history(path))
        if tree is self.commit_files and self._selected_sha():
            sha = self._selected_sha()
            if gb.vector_main_file(path) or gb.raster_main_file(path):
                _menu_action(menu, _icon("/mActionAddLayer.svg"), t("load_version"),
                               lambda: self.load_version(path, sha))
            _menu_action(menu, _icon("/mActionUndo.svg"), t("restore"), lambda: self.restore_version(path, sha))
        if self._locks_usable():
            menu.addSeparator()
            lk = self._locks.get(path)
            if lk is None:
                _menu_action(menu, _icon("/locked.svg"), t("lock"), lambda: self.lock_file(path))
            elif lk.ours is False:
                _menu_action(menu, _icon("/unlocked.svg"), t("unlock_force", lk.owner),
                               lambda: self.unlock_file(path, force=True))
            else:
                _menu_action(menu, _icon("/unlocked.svg"), t("unlock"), lambda: self.unlock_file(path))
        menu.exec(tree.viewport().mapToGlobal(pos))

    # --- LFS locks -----------------------------------------------------
    def _locks_usable(self):
        return bool(self.repo and self._lfs and self.repo.remote_url())

    def _mark_lock(self, item, col, path):
        lk = self._locks.get(path)
        if lk is None:
            return
        when = lk.locked_at[:16].replace("T", " ")
        if lk.ours is False:
            item.setIcon(col, _icon("/locked.svg"))
            item.setForeground(col, QBrush(QColor("#d62728")))
            item.setToolTip(col, t("locked_by", lk.owner, when))
        else:
            item.setIcon(col, _icon("/lockedGray.svg"))
            item.setToolTip(col, t("locked_mine", when) if lk.ours else t("locked_by", lk.owner, when))

    def load_locks(self):
        if not self.repo:
            return
        if not self._lfs:
            self._show_locks([], t("locks_need_lfs"))
            return
        if not self.repo.remote_url():
            self._show_locks([], t("locks_need_remote"))
            return
        self._run(t("locks"), self.repo.locks, after=self._show_locks, quiet=True, refresh=False,
                  on_error=lambda e: self._show_locks([], t("locks_failed", e)))

    def _show_locks(self, locks, status=None):
        self._locks = {lk.path: lk for lk in locks}
        self.locks_tree.clear()
        for lk in sorted(locks, key=lambda x: x.path.lower()):
            who = lk.owner + (f" ({t('you')})" if lk.ours else "")
            it = QTreeWidgetItem([lk.path, who, lk.locked_at[:16].replace("T", " ")])
            it.setData(0, PATH_ROLE, lk.path)
            self._mark_lock(it, 0, lk.path)
            self.locks_tree.addTopLevelItem(it)
        for col in (1, 2):
            self.locks_tree.resizeColumnToContents(col)
        self.locks_status.setText(status or (t("n_locks", len(locks)) if locks else t("locks_none")))
        self.tabs.setTabText(2, t("locks") + (f" ({len(locks)})" if locks else ""))
        self.btn_unlock.setEnabled(self._locks_usable() and bool(locks))
        self.btn_locks_refresh.setEnabled(self._locks_usable())
        if self.repo:
            self._fill_changes(force=True)

    def _dataset_paths(self, path):
        """A shapefile is locked together with its sidecar files."""
        return [m for m in gb.dataset_members(path) if m == path or os.path.exists(os.path.join(self.repo.path, m))]

    def lock_file(self, path):
        repo = self.repo
        paths = self._dataset_paths(path)
        self._run(t("lock"), lambda: [repo.lock(p) for p in paths], t("locked_ok", path),
                  after=lambda _: self.load_locks())

    def unlock_file(self, path, force=False):
        if force:
            lk = self._locks.get(path)
            q = t("unlock_force_q", path, lk.owner if lk else "?")
            if QMessageBox.question(self, "GeoGit", q) != QMessageBox.StandardButton.Yes:
                return
        repo = self.repo
        paths = [p for p in self._dataset_paths(path) if p in self._locks] or [path]
        self._run(t("unlock"), lambda: [repo.unlock(p, force) for p in paths], t("unlocked_ok", path),
                  after=lambda _: self.load_locks())

    def _unlock_selected(self):
        item = self.locks_tree.currentItem()
        if item is not None:
            path = item.data(0, PATH_ROLE)
            lk = self._locks.get(path)
            self.unlock_file(path, force=bool(lk and lk.ours is False))

    # --- history tools -------------------------------------------------
    def _history_menu(self, pos):
        item = self.history.itemAt(pos)
        if item is None or not self.repo:
            return
        sha = item.data(1, PATH_ROLE)
        subject = next((c.subject for c in self._commits if c.sha == sha), sha[:8])
        menu = QMenu(self.history)
        _menu_action(menu, _icon("/mActionLabeling.svg"), t("tag"), lambda: self.tag_version(sha))
        _menu_action(menu, _icon("/mActionFileOpen.svg"), t("report"), lambda: self.report_version(sha))
        menu.addSeparator()
        _menu_action(menu, _icon("/mActionUndo.svg"), t("revert"), lambda: self.revert_version(sha, subject))
        menu.exec(self.history.viewport().mapToGlobal(pos))

    def tag_version(self, sha):
        name, ok = QInputDialog.getText(self, t("tag"), t("tag_name"))
        name = name.strip().replace(" ", "-")
        if not ok or not name:
            return
        message, ok = QInputDialog.getText(self, t("tag"), t("tag_message"))
        if not ok or not self._ensure_identity():
            return
        try:
            self.repo.add_tag(name, sha, message.strip())
        except gb.GitError as e:
            self._show_error(e)
            return
        self.iface.messageBar().pushMessage("GeoGit", t("tagged", name), Qgis.MessageLevel.Success, 6)
        self.refresh()

    def revert_version(self, sha, subject):
        if QMessageBox.question(self, "GeoGit", t("revert_q", subject)) != QMessageBox.StandardButton.Yes:
            return
        if not self._ensure_identity():
            return
        repo = self.repo
        self._run(t("revert"), lambda: repo.revert(sha), lambda new: t("reverted", new[:8]))

    def restore_version(self, path, sha):
        if QMessageBox.question(self, "GeoGit", t("restore_q", path, sha[:8])) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.repo.restore_file(sha, path)
        except gb.GitError as e:
            self._show_error(e)
            return
        self.iface.messageBar().pushMessage("GeoGit", t("restored", path), Qgis.MessageLevel.Success, 8)
        self.refresh()

    def load_version(self, path, sha):
        """Add the file as it was in sha to the map (from the version cache)."""
        repo = self.repo
        main = gb.vector_main_file(path) or gb.raster_main_file(path)

        def after(local):
            if not local:
                self.iface.messageBar().pushMessage("GeoGit", t("raster_only_one", path),
                                                    Qgis.MessageLevel.Warning, 6)
                return
            name = f"{os.path.basename(main)} · {sha[:8]}"
            layer = (QgsRasterLayer(local, name) if gb.raster_main_file(main)
                     else QgsVectorLayer(local, name, "ogr"))
            if layer.isValid():
                QgsProject.instance().addMapLayer(layer)

        self._run(t("load_version"), lambda: repo.cached_version(sha, main), after=after)

    def report_version(self, sha):
        default = os.path.join(os.path.expanduser("~"), f"geogit-{os.path.basename(self.repo.path)}-{sha[:8]}.html")
        path, _ = QFileDialog.getSaveFileName(self, t("report"), default, "HTML (*.html)")
        if not path:
            return
        repo = self.repo

        def work():
            from .report import build_report
            with open(path, "w", encoding="utf-8") as f:
                f.write(build_report(repo, sha))
            return path

        self._run(t("report"), work, lambda p: t("report_saved", p),
                  after=lambda p: QDesktopServices.openUrl(QUrl.fromLocalFile(p)), refresh=False)

    def _selected_sha(self):
        item = self.history.currentItem()
        return item.data(1, PATH_ROLE) if item is not None else None

    def _file_double_clicked(self, tree, item):
        path = item.data(0, PATH_ROLE)
        if self.repo and path:
            self.show_changes(path, self._selected_sha() if tree is self.commit_files else None)

    def show_changes(self, path, sha=None):
        if gb.file_kind(path) == "project":
            self.show_project_diff(path, sha)
        elif gb.raster_main_file(path):
            self.show_raster_diff(path, sha)
        elif gb.vector_main_file(path):
            self.show_vector_diff(path, sha)

    def show_project_diff(self, path, sha=None):
        """What changed in a .qgz/.qgs: layers, layer tree, CRS, layouts."""
        repo = self.repo
        label = sha[:8] if sha else t("working_copy")

        def work():
            if sha:
                old_rev, new = repo.first_parent(sha), repo.cached_version(sha, path)
            else:
                old_rev = "HEAD" if repo.has_commits() else None
                new = os.path.join(repo.path, path)
            old = repo.cached_version(old_rev, path) if old_rev else None
            return project_diff.diff_files(old, new)

        def after(changes):
            if not changes:
                self.iface.messageBar().pushMessage("GeoGit", t("pd_none", path), Qgis.MessageLevel.Info, 6)
                return
            from .project_dialog import ProjectDiffDialog
            dlg = ProjectDiffDialog(t("pd_title", path, label), changes, self)
            dlg.show()
            self._dialogs = [d for d in getattr(self, "_dialogs", []) if d.isVisible()] + [dlg]

        self._run(t("comparing") + f" {path}", work, after=after)

    def show_raster_diff(self, path, sha=None):
        """Compare a raster in commit sha with its parent (sha None: unsaved vs HEAD)."""
        repo = self.repo
        label = sha[:8] if sha else t("working_copy")

        def work():
            if sha:
                old_rev, new = repo.first_parent(sha), repo.cached_version(sha, path)
                new_key = sha[:12]
            else:
                old_rev = "HEAD" if repo.has_commits() else None
                new = os.path.join(repo.path, path)
                new = new if os.path.exists(new) else None
                new_key = f"work-{int(os.path.getmtime(new)) if new else 0}"
            old = repo.cached_version(old_rev, path) if old_rev else None
            if not old or not new:
                return old, new, None
            old_key = repo.git("rev-parse", old_rev).strip()[:12]
            out = repo.cache_dir("diffs", f"{old_key}_{new_key}", path.replace("/", "__"))
            return old, new, raster_diff.compare(old, new, out)

        self._run(t("comparing") + f" {path}", work,
                  after=lambda res: self._show_raster(path, label, *res))

    def _show_raster(self, path, label, old, new, diff):
        from .diff_layer import add_to_project
        from .raster_layer import build_layers
        bar = self.iface.messageBar()
        if diff is not None and not diff.changed:
            bar.pushMessage("GeoGit", t("raster_same", path), Qgis.MessageLevel.Info, 6)
            return
        layers = build_layers(path, label, old, new, diff)
        group = add_to_project([lyr for lyr, _ in layers], f"GeoGit: {path} · {label}")
        for lyr, visible in layers:
            node = group.findLayer(lyr.id())
            if node is not None:      # QGIS 4.0: a layer node counts as False (no children)
                node.setItemVisibilityChecked(visible)
        if diff is None:
            bar.pushMessage("GeoGit", t("raster_only_one", path), Qgis.MessageLevel.Info, 8)
        else:
            msg = t("raster_summary", path, f"{diff.changed_pixels:,}".replace(",", " "), diff.changed_percent)
            if diff.compared and (diff.min_diff or diff.max_diff):
                msg += t("raster_range", diff.min_diff, diff.max_diff)
            if diff.gained_pixels or diff.lost_pixels:
                msg += t("raster_gain_loss", diff.gained_pixels, diff.lost_pixels)
            if diff.meta_changes:
                msg += t("raster_meta", ", ".join(t("meta_" + k) for k in diff.meta_changes))
            if not diff.compared:
                msg += t("raster_not_compared")
            widget = bar.createMessage("GeoGit", msg)
            old_layer = next((lyr for lyr, vis in layers if lyr.source() == old), None)
            if old_layer is not None and new:
                btn = QPushButton(_icon("/mActionPan.svg"), t("swipe"))
                btn.clicked.connect(lambda: self.start_swipe(old_layer))
                widget.layout().addWidget(btn)
            bar.pushWidget(widget, Qgis.MessageLevel.Success, 20)
        self._zoom_to([layers[0][0]] if layers else [])

    def start_swipe(self, old_layer):
        from .raster_layer import SwipeMapTool
        canvas = self.iface.mapCanvas()
        self._swipe = SwipeMapTool(canvas, [old_layer])
        canvas.setMapTool(self._swipe)
        self.iface.messageBar().pushMessage("GeoGit", t("swipe_hint"), Qgis.MessageLevel.Info, 6)

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
        if item is None or not self.repo:
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
    def _run(self, label, func, done_text=None, after=None, quiet=False, refresh=True, on_error=None):
        """Run func in the background; quiet errors go to the message bar instead of a dialog."""
        self._set_busy(True)
        if not quiet:
            self.iface.messageBar().pushMessage("GeoGit", t("working", label), Qgis.MessageLevel.Info, 3)

        def work(_task):
            return func()

        def finished(exception, result=None):
            self._tasks = [x for x in self._tasks if x is not task]
            self._set_busy(False)
            if exception is not None:
                if on_error:
                    on_error(exception)
                if quiet:
                    self.iface.messageBar().pushMessage("GeoGit", f"{label}: {exception}",
                                                        Qgis.MessageLevel.Warning, 8)
                else:
                    self._show_error(exception)
            else:
                if done_text:
                    msg = done_text(result) if callable(done_text) else done_text
                    self.iface.messageBar().pushMessage("GeoGit", msg, Qgis.MessageLevel.Success, 5)
                if after:
                    after(result)
            if refresh:
                self.refresh()

        task = QgsTask.fromFunction(f"GeoGit: {label}", work, on_finished=finished)
        self._tasks.append(task)
        QgsApplication.taskManager().addTask(task)

    def _set_busy(self, busy):
        for w in (self.btn_commit, self.btn_discard, self.btn_pull, self.btn_push, self.btn_refresh,
                  self.btn_init, self.btn_clone, self.branch_combo, self.btn_new_branch, self.btn_merge):
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
        if self.repo.in_merge():
            self._finish_merge(msg)
            return
        if not msg:
            QMessageBox.information(self, "GeoGit", t("need_message"))
            return
        paths, everything = self._checked_paths()
        if not paths:
            QMessageBox.information(self, "GeoGit", t("need_selection"))
            return
        if not self._ensure_identity():
            return
        theirs = [f"{p} ({self._locks[p].owner})" for p in paths
                  if p in self._locks and self._locks[p].ours is False]
        if theirs and QMessageBox.question(self, "GeoGit", t("commit_locked_q", "\n".join(theirs))) \
                != QMessageBox.StandardButton.Yes:
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

        def after_pull(conflicts):
            after(conflicts)
            self.load_locks()
            self._merge_result(conflicts, t("pulled"))

        self._run(t("pull"), repo.pull, None, after_pull)

    def _push(self):
        self._run(t("push"), self.repo.push, t("pushed"), after=lambda _: self.load_locks())

    def _refresh_and_fetch(self):
        self.refresh()
        if self.repo and self.repo.remote_url():
            self._run(t("refresh"), self.repo.fetch, after=lambda _: self.load_locks())

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

    # --- merge -----------------------------------------------------------
    def _fill_merge_state(self):
        merging = self.repo.in_merge()
        self.merge_bar.setVisible(merging)
        self.btn_commit.setText(t("finish_merge") if merging else t("commit"))
        self.btn_merge.setEnabled(not merging)
        if merging:
            self.btn_discard.setEnabled(False)   # would throw away resolved conflicts; abort instead
        else:
            return
        head = self.repo.merge_head_name()
        n = len(self.repo.conflicts())
        self.merge_label.setText(t("merge_banner", head, n) if n else t("merge_banner_done", head))
        self.btn_resolve.setVisible(bool(n))
        self.btn_commit.setEnabled(not n)

    def _merge_result(self, conflicts, ok_text):
        bar = self.iface.messageBar()
        if conflicts:
            bar.pushMessage("GeoGit", t("merge_conflicts", len(conflicts)), Qgis.MessageLevel.Warning, 10)
            self.refresh()
            self.open_conflicts()
        else:
            bar.pushMessage("GeoGit", ok_text, Qgis.MessageLevel.Success, 5)

    def _merge_branch(self):
        current = self.repo.branch_info().name
        others = [b for b in self.repo.branches() if b != current]
        if not others:
            return
        name, ok = QInputDialog.getItem(self, t("merge"), t("merge_pick", current), others, 0, False)
        if not ok or not self._ensure_identity():
            return
        repo = self.repo
        self._run(t("merge"), lambda: repo.merge(name), after=lambda c: self._merge_result(c, t("merge_ok")))

    def open_conflicts(self):
        from .merge_dialog import ConflictDialog
        dlg = ConflictDialog(self)
        dlg.finished.connect(lambda _: self.refresh())
        dlg.show()
        self._dialogs = [d for d in getattr(self, "_dialogs", []) if d.isVisible()] + [dlg]
        return dlg

    def _abort_merge(self):
        if QMessageBox.question(self, "GeoGit", t("abort_merge_q")) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.repo.abort_merge()
        except gb.GitError as e:
            self._show_error(e)
        self.refresh()

    def _finish_merge(self, msg):
        if not self._ensure_identity():
            return
        repo = self.repo

        def after(_):
            self.message.clear()

        self._run(t("finish_merge"), lambda: repo.finish_merge(msg or None),
                  lambda sha: t("merged", sha[:8]), after)

    def _new_branch(self):
        name, ok = QInputDialog.getText(self, t("new_branch"), t("new_branch_name"))
        if ok and name.strip():
            try:
                self.repo.create_branch(name.strip())
            except gb.GitError as e:
                self._show_error(e)
            self.refresh()
