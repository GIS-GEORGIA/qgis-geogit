import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
try:                                    # Qt6 (QGIS 4) moved QAction to QtGui
    from qgis.PyQt.QtGui import QAction
except ImportError:                     # Qt5 (QGIS 3)
    from qgis.PyQt.QtWidgets import QAction

from . import i18n

MENU = "GeoGit"


class GeoGitPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dock = None
        i18n.set_lang(i18n.locale_lang())

    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))
        self.action = QAction(icon, i18n.t("title"), self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.triggered.connect(self.toggle)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu(MENU, self.action)

    def unload(self):
        if self.dock is not None:
            self.dock.cleanup()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if self.action:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginMenu(MENU, self.action)
            self.action = None

    def toggle(self, checked):
        if self.dock is None:
            from .dock import GeoGitDock
            self.dock = GeoGitDock(self.iface)
            self.dock.visibilityChanged.connect(self.action.setChecked)
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        self.dock.setVisible(checked)
