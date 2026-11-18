import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QDialog,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from qgis.core import QgsLayerTreeGroup, QgsLayerTreeLayer, QgsProject

if hasattr(Qt, 'CheckState'):
    CHECKED = Qt.CheckState.Checked
    UNCHECKED = Qt.CheckState.Unchecked
    PARTIALLY = Qt.CheckState.PartiallyChecked
else:
    CHECKED = Qt.Checked
    UNCHECKED = Qt.Unchecked
    PARTIALLY = Qt.PartiallyChecked

if hasattr(Qt, 'ItemFlag'):
    FLAG_CHECKABLE = Qt.ItemFlag.ItemIsUserCheckable
    FLAG_TRISTATE = Qt.ItemFlag.ItemIsAutoTristate
    FLAG_ENABLED = Qt.ItemFlag.ItemIsEnabled
else:
    FLAG_CHECKABLE = Qt.ItemIsUserCheckable
    FLAG_TRISTATE = Qt.ItemIsAutoTristate
    FLAG_ENABLED = Qt.ItemIsEnabled

if hasattr(Qt, 'ItemDataRole'):
    ROLE_USER = Qt.ItemDataRole.UserRole
else:
    ROLE_USER = Qt.UserRole

if hasattr(Qt, 'WindowModality'):
    MODAL_WINDOW = Qt.WindowModality.WindowModal
else:
    MODAL_WINDOW = Qt.WindowModal

if hasattr(Qt, 'CursorShape'):
    CURSOR_WAIT = Qt.CursorShape.WaitCursor
else:
    CURSOR_WAIT = Qt.WaitCursor

try:
    HEADER_STRETCH = QHeaderView.ResizeMode.Stretch
except AttributeError:
    HEADER_STRETCH = QHeaderView.Stretch

try:
    ICON_INFO = QMessageBox.Icon.Information
except AttributeError:
    ICON_INFO = QMessageBox.Information


def exec_dialog(dialog):
    run = getattr(dialog, 'exec', None) or getattr(dialog, 'exec_', None)
    return run()


class ExportDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle('Exportar cadastro para o QField (v1.1.4)')
        self.setMinimumWidth(560)
        self._build_ui()
        self._populate_layers()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(
            'Camadas do projeto (assinale as que pretende exportar para o QField):'))

        self.layer_tree = QTreeWidget()
        self.layer_tree.setHeaderLabels(['Camada'])
        self.layer_tree.header().setSectionResizeMode(0, HEADER_STRETCH)
        layout.addWidget(self.layer_tree, 1)

        btn_row = QHBoxLayout()
        btn_all = QPushButton('Selecionar tudo')
        btn_all.clicked.connect(lambda: self._set_all_checks(True))
        btn_none = QPushButton('Limpar seleção')
        btn_none.clicked.connect(lambda: self._set_all_checks(False))
        btn_row.addWidget(btn_all)
        btn_row.addWidget(btn_none)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.chk_copy_data = QCheckBox(
            'Copiar dados das camadas para a pasta de exportação (fontes locais)')
        self.chk_copy_data.setChecked(True)
        self.chk_remove_labels = QCheckBox('Remover etiquetas (labels) do projeto exportado')
        self.chk_remove_labels.setChecked(True)
        self.chk_remove_ann = QCheckBox('Remover camadas de anotações (annotations)')
        self.chk_remove_ann.setChecked(True)
        self.chk_create_zip = QCheckBox('Gerar ficheiro ZIP único para upload no telemóvel')
        self.chk_create_zip.setChecked(True)
        self.chk_compact = QCheckBox(
            'Converter cada camada vetorial para o seu próprio GeoPackage (mais estável no QField)')
        self.chk_compact.setChecked(True)
        layout.addWidget(self.chk_copy_data)
        layout.addWidget(self.chk_remove_labels)
        layout.addWidget(self.chk_remove_ann)
        layout.addWidget(self.chk_create_zip)
        layout.addWidget(self.chk_compact)

        out_row = QHBoxLayout()
        out_row.addWidget(QLabel('Pasta de destino:'))
        self.out_edit = QLineEdit()
        out_row.addWidget(self.out_edit, 1)
        btn_browse = QPushButton('…')
        btn_browse.setMaximumWidth(36)
        btn_browse.clicked.connect(self._browse)
        out_row.addWidget(btn_browse)
        layout.addLayout(out_row)

        self.chk_remove_labels.setToolTip(
            'Desativa todas as etiquetas das camadas vetoriais no projeto exportado.')
        self.chk_copy_data.setToolTip(
            'Copia ficheiros locais (shapefile, GeoPackage, raster, CSV) para a pasta data/.')
        self.chk_remove_ann.setToolTip(
            'Exclui camadas de anotações do projeto exportado.')
        self.chk_compact.setToolTip(
            'Cada camada vira um ficheiro GeoPackage próprio na pasta data/ — as camadas '
            'mantêm-se separadas e a estrutura de grupos fica igual ao QGIS.')

        actions = QHBoxLayout()
        actions.addStretch(1)
        btn_export = QPushButton('Exportar')
        btn_export.setDefault(True)
        btn_export.clicked.connect(self._run_export)
        btn_close = QPushButton('Fechar')
        btn_close.clicked.connect(self.close)
        actions.addWidget(btn_export)
        actions.addWidget(btn_close)
        layout.addLayout(actions)

        default_dir = QgsProject.instance().homePath() or os.path.expanduser('~')
        self.out_edit.setText(default_dir)

    def _populate_layers(self):
        root = QgsProject.instance().layerTreeRoot()
        try:
            selection = {lyr.id() for lyr in self.iface.layerTreeView().selectedLayers()}
        except Exception:
            selection = set()
        self._add_tree_items(root, None, selection)
        self.layer_tree.expandAll()

    def _add_tree_items(self, node, parent_item, selection):
        container = parent_item if parent_item is not None else self.layer_tree.invisibleRootItem()
        for child in node.children():
            if isinstance(child, QgsLayerTreeGroup):
                item = QTreeWidgetItem([child.name()])
                item.setFlags(item.flags() | FLAG_CHECKABLE | FLAG_TRISTATE)
                item.setCheckState(0, CHECKED if child.itemVisibilityChecked() else UNCHECKED)
                container.addChild(item)
                self._add_tree_items(child, item, selection)
                if item.childCount() == 0:
                    item.setFlags(item.flags() & ~FLAG_ENABLED)
            elif isinstance(child, QgsLayerTreeLayer):
                layer = child.layer()
                if layer is None:
                    continue
                item = QTreeWidgetItem([layer.name()])
                item.setFlags(item.flags() | FLAG_CHECKABLE)
                item.setCheckState(0, CHECKED if layer.id() in selection else UNCHECKED)
                item.setData(0, ROLE_USER, layer.id())
                if hasattr(layer, 'publicSource'):
                    item.setToolTip(0, layer.publicSource())
                container.addChild(item)

    def _set_all_checks(self, state):
        root_item = self.layer_tree.invisibleRootItem()

        def walk(item):
            for i in range(item.childCount()):
                child = item.child(i)
                child.setCheckState(0, CHECKED if state else UNCHECKED)
                walk(child)

        walk(root_item)

    def _selected_layers(self):
        project = QgsProject.instance()
        layers = []
        stack = [self.layer_tree.invisibleRootItem()]
        while stack:
            item = stack.pop()
            for i in range(item.childCount()):
                child = item.child(i)
                stack.append(child)
                layer_id = child.data(0, ROLE_USER)
                if layer_id and child.checkState(0) == CHECKED:
                    layer = project.mapLayer(layer_id)
                    if layer is not None:
                        layers.append(layer)
        layers.reverse()
        return layers

    def _browse(self):
        try:
            start = self.out_edit.text().strip() or os.path.expanduser('~')
            chosen = QFileDialog.getExistingDirectory(self, 'Pasta de destino', start)
            if chosen:
                self.out_edit.setText(chosen)
        except OSError:
            pass

    def _run_export(self):
        layers = self._selected_layers()
        if not layers:
            QMessageBox.warning(self, 'QField Cadastro Export',
                                'Nenhuma camada selecionada para exportação.')
            return
        out_dir = self.out_edit.text().strip()
        if not out_dir or not os.path.isdir(out_dir):
            QMessageBox.warning(self, 'QField Cadastro Export',
                                'Indique uma pasta de destino válida.')
            return

        from .exporter import Exporter

        exporter = Exporter(
            project=QgsProject.instance(),
            layers=layers,
            out_dir=out_dir,
            copy_data=self.chk_copy_data.isChecked(),
            remove_labels=self.chk_remove_labels.isChecked(),
            remove_annotations=self.chk_remove_ann.isChecked(),
            create_zip=self.chk_create_zip.isChecked(),
            compact=self.chk_compact.isChecked(),
        )

        QApplication.setOverrideCursor(CURSOR_WAIT)
        progress = QProgressDialog('A exportar, aguarde…', None, 0, 0, self)
        progress.setWindowModality(MODAL_WINDOW)
        progress.setMinimumDuration(0)
        progress.show()
        QApplication.processEvents()
        try:
            result = exporter.run()
        finally:
            QApplication.restoreOverrideCursor()
            progress.close()

        if result['ok']:
            msg = ('Exportação concluída com sucesso.\n\nProjeto: '
                   + result['project_path']
                   + '\nCamadas exportadas: ' + str(result['count'])
                   + ('\n' + result['summary'] if result.get('summary') else ''))
            if result.get('zip_path'):
                msg += ('\n\nZIP para o telemóvel:\n' + result['zip_path']
                        + '\n\nEnvie o ZIP para o dispositivo (por ex. via cabo, WhatsApp, '
                        + 'e-mail ou Bluetooth), extraia-o no telemóvel e abra o .qgs no QField.')
            else:
                msg += ('\n\nCopie a pasta gerada para o dispositivo Android '
                        + 'e abra o ficheiro .qgs no QField.')
            if result['warnings']:
                box = QMessageBox(self)
                box.setIcon(ICON_INFO)
                box.setWindowTitle('QField Cadastro Export')
                box.setText(msg)
                box.setDetailedText('\n'.join(result['warnings']))
                exec_dialog(box)
            else:
                QMessageBox.information(self, 'QField Cadastro Export', msg)
            self.accept()
        else:
            QMessageBox.critical(self, 'QField Cadastro Export',
                                 'A exportação falhou:\n\n' + result['error'])
