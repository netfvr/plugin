import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .export_dialog import ExportDialog, exec_dialog

MENU_NAME = '&QField Cadastro Export'


class QFieldCadastroExport:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.plugin_dir = os.path.dirname(__file__)

    def initGui(self):
        icon = QIcon(os.path.join(self.plugin_dir, 'icons', 'icon.png'))
        self.action = QAction(icon, 'Exportar cadastro para o QField', self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu(MENU_NAME, self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action is not None:
            self.iface.removePluginMenu(MENU_NAME, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None

    def run(self):
        dlg = ExportDialog(self.iface, self.iface.mainWindow())
        exec_dialog(dlg)
