"""Dialog listing what changed in a QGIS project file between two versions."""

from qgis.core import QgsApplication
from qgis.PyQt.QtGui import QBrush, QColor
from qgis.PyQt.QtWidgets import QDialog, QDialogButtonBox, QLabel, QTreeWidget, QTreeWidgetItem, QVBoxLayout

from .i18n import t

SECTIONS = [("project", "/mIconQgsProjectFile.svg"), ("layers", "/mActionAddLayer.svg"),
            ("tree", "/mActionShowAllLayers.svg"), ("layouts", "/mActionNewLayout.svg")]
KIND_COLORS = {"added": "#2ca02c", "removed": "#d62728", "changed": "#e67e00"}


def describe(change):
    """(name, text) for one project_diff.Change in the current language."""
    what, _, detail = change.detail.partition("|")
    if change.section == "project":
        return t("proj_" + change.name), change.detail
    if change.section == "layers":
        if change.kind != "changed":
            return change.name, change.detail
        text = t("pd_" + what)
        return change.name, f"{text}: {detail}" if detail else text
    if change.section == "tree":
        if what == "group":
            return change.name, t("pd_group")
        if what == "order":
            return t("pd_order"), ""
        text = t("pd_" + what)
        return change.name, f"{text}: {detail}" if detail else text
    return change.name, ""


class ProjectDiffDialog(QDialog):
    def __init__(self, title, changes, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(620, 460)
        lay = QVBoxLayout(self)
        counts = {k: sum(c.kind == k for c in changes) for k in KIND_COLORS}
        lay.addWidget(QLabel(t("pd_summary", counts["added"], counts["changed"], counts["removed"])))
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels([t("pd_col_what"), t("pd_col_state"), t("pd_col_detail")])
        for section, icon in SECTIONS:
            items = [c for c in changes if c.section == section]
            if not items:
                continue
            top = QTreeWidgetItem([f"{t('pd_sec_' + section)} ({len(items)})"])
            top.setIcon(0, QgsApplication.getThemeIcon(icon))
            self.tree.addTopLevelItem(top)
            for c in items:
                name, text = describe(c)
                it = QTreeWidgetItem([name, t("change_" + ("deleted" if c.kind == "removed" else
                                                           "modified" if c.kind == "changed" else "added")), text])
                it.setForeground(1, QBrush(QColor(KIND_COLORS[c.kind])))
                it.setToolTip(2, text)
                top.addChild(it)
            top.setExpanded(True)
        for col in (0, 1):
            self.tree.resizeColumnToContents(col)
        lay.addWidget(self.tree, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
