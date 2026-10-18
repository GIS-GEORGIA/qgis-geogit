"""Dialog: create a repository on GitHub, GitLab, Bitbucket or Forgejo/Gitea for this folder."""

import os

from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                                 QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout)

from . import accounts, hosting
from .i18n import t

SERVICES = ["github", "gitlab", "bitbucket", "forgejo"]
DEFAULT_URLS = {"github": "https://github.com", "gitlab": "https://gitlab.com",
                "bitbucket": "https://bitbucket.org", "forgejo": "http://localhost:3000"}


def friendly_error(err):
    """A HostingError in words a user can act on."""
    status = getattr(err, "status", 0)
    if status == 401:
        return t("srv_err_401")
    if status == 403:
        return t("srv_err_403")
    text = str(err).lower()
    if status in (400, 409, 422) and ("exist" in text or "taken" in text):
        return t("srv_err_exists")
    return str(err)


class CreateRepoDialog(QDialog):
    """Fills self.account, self.owner and self.created when accepted."""

    def __init__(self, folder, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("srv_title"))
        self.setMinimumWidth(640)
        self.account = None
        self.owner = None
        self.created = None
        self._owners = []
        self._checked_key = None

        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.service = QComboBox()
        for key in SERVICES:
            self.service.addItem(hosting.PROVIDERS[key].label, key)
        self.service.currentIndexChanged.connect(self._service_changed)
        form.addRow(t("srv_service"), self.service)
        self.url = QLineEdit()
        self.url.textChanged.connect(self._fill_saved)
        form.addRow(t("srv_url"), self.url)
        self.email = QLineEdit()
        self.email.setPlaceholderText("name@example.com")
        self.email_label = QLabel(t("srv_email"))
        form.addRow(self.email_label, self.email)

        trow = QHBoxLayout()
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.textEdited.connect(self._forget_check)
        self.btn_token_page = QPushButton(t("srv_get_token"))
        self.btn_token_page.clicked.connect(self._open_token_page)
        trow.addWidget(self.token, 1)
        trow.addWidget(self.btn_token_page)
        form.addRow(t("srv_token"), trow)
        self.scopes = QLabel()
        self.scopes.setWordWrap(True)
        self.scopes.setMinimumHeight(self.scopes.fontMetrics().lineSpacing() * 2 + 4)
        self.scopes.setStyleSheet("color: palette(mid);")
        form.addRow("", self.scopes)

        crow = QHBoxLayout()
        self.btn_check = QPushButton(t("srv_connect"))
        self.btn_check.clicked.connect(self.check)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        crow.addWidget(self.btn_check)
        crow.addWidget(self.status, 1)
        form.addRow("", crow)

        self.owner_box = QComboBox()
        form.addRow(t("srv_owner"), self.owner_box)
        self.name = QLineEdit(hosting.repo_slug(os.path.basename(folder.rstrip("/\\")) or "qgis-project"))
        form.addRow(t("srv_name"), self.name)
        self.description = QLineEdit()
        form.addRow(t("srv_description"), self.description)
        self.private = QCheckBox(t("srv_private"))
        self.private.setToolTip(t("srv_private_tip"))
        self.private.setChecked(True)
        form.addRow("", self.private)
        self.remember = QCheckBox(t("srv_remember"))
        self.remember.setToolTip(t("srv_remember_tip"))
        self.remember.setChecked(True)
        form.addRow("", self.remember)
        lay.addLayout(form)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.btn_create = self.buttons.addButton(t("srv_create"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.accepted.connect(self.create)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self._service_changed()

    # --- state -------------------------------------------------------------
    def key(self):
        return self.service.currentData()

    def _service_changed(self):
        key = self.key()
        prov = hosting.PROVIDERS[key]
        self.url.setText(DEFAULT_URLS[key])
        self.url.setEnabled(key in ("gitlab", "forgejo", "github"))
        self.email.setVisible(prov.needs_email)
        self.email_label.setVisible(prov.needs_email)
        self.scopes.setText(t("srv_scopes", prov.scopes))
        self._fill_saved()

    def _fill_saved(self):
        saved = accounts.find(self.key(), self.url.text().strip())
        self.token.clear()
        self.token.setPlaceholderText(t("srv_token_saved", saved["username"]) if saved else "")
        if saved and saved.get("email"):
            self.email.setText(saved["email"])
        self._owners = []
        self.owner_box.clear()
        self.btn_create.setEnabled(False)
        self.status.setText("")

    def _forget_check(self):
        self.account = None
        self.btn_create.setEnabled(False)

    def _current_account(self):
        url = self.url.text().strip().rstrip("/")
        token = self.token.text().strip()
        if not token:
            saved = accounts.find(self.key(), url)
            token = accounts.token(saved) if saved else ""
        return hosting.Account(self.key(), url, token, self.email.text().strip())

    def _open_token_page(self):
        prov = hosting.provider(hosting.Account(self.key(), self.url.text().strip(), ""))
        QDesktopServices.openUrl(QUrl(prov.token_page()))

    def _busy(self, on):
        if on:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        else:
            QApplication.restoreOverrideCursor()

    # --- actions -----------------------------------------------------------
    def check(self):
        """Check the token and list where a repository can be created."""
        account = self._current_account()
        if not account.token:
            self.status.setText(t("srv_need_token"))
            return False
        if hosting.PROVIDERS[account.service].needs_email and not account.email:
            self.status.setText(t("srv_need_email"))
            return False
        prov = hosting.provider(account)
        self._busy(True)
        try:
            user = prov.check()
            self._owners = prov.owners()
        except hosting.HostingError as e:
            self.status.setText("✖ " + friendly_error(e))
            return False
        finally:
            self._busy(False)
        self.account = account
        self.owner_box.clear()
        for o in self._owners:
            self.owner_box.addItem(o.name + (f" ({t('you')})" if o.is_user else ""), o.id)
        self.status.setText("✔ " + t("srv_connected", user))
        self.btn_create.setEnabled(bool(self._owners))
        return True

    def create(self):
        if self.account is None and not self.check():
            return
        name = self.name.text().strip()
        if not name:
            self.status.setText(t("srv_need_name"))
            return
        self.owner = self._owners[self.owner_box.currentIndex()]
        self._busy(True)
        try:
            self.created = hosting.provider(self.account).create(self.owner, name, self.description.text().strip(),
                                                                 self.private.isChecked())
        except hosting.HostingError as e:
            self.status.setText("✖ " + friendly_error(e))
            return
        finally:
            self._busy(False)
        self.accept()
