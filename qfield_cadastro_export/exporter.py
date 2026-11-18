import glob
import os
import re
import shutil
from urllib.parse import quote, unquote, urlparse

from qgis.core import (
    QgsDataProvider,
    QgsLayerTreeGroup,
    QgsLayerTreeLayer,
    QgsPalLayerSettings,
    QgsProject,
)

try:
    from qgis.core import Qgis
    LayerType = Qgis.LayerType
except ImportError:
    from qgis.core import QgsMapLayerType
    LayerType = QgsMapLayerType

LOCAL_PROVIDERS = {'ogr', 'gdal', 'delimitedtext'}

SIDECAR_SUFFIXES = (
    '.prj', '.tfw', '.wld', '.ovr', '.rrd', '.aux.xml',
    '.qml', '.qmd', '.jpw', '.cpg', '.qpj', '.vat.dbf',
)


def _unique_name(base, used):
    stem, ext = os.path.splitext(base)
    name = base
    n = 1
    while name in used:
        n += 1
        name = '{}_{}{}'.format(stem, n, ext)
    used.add(name)
    return name


class Exporter:
    def __init__(self, project, layers, out_dir, copy_data=True,
                 remove_labels=True, remove_annotations=True):
        self.project = project
        self.layers = layers
        self.out_dir = out_dir
        self.copy_data = copy_data
        self.remove_labels = remove_labels
        self.remove_annotations = remove_annotations
        self.warnings = []
        self.clones = {}

    def run(self):
        result = {
            'ok': False,
            'count': 0,
            'export_dir': '',
            'project_path': '',
            'warnings': self.warnings,
            'error': '',
        }
        try:
            project_name = self.project.projectName() or 'projeto'
            safe = re.sub(r'[^A-Za-z0-9_\-]+', '_', project_name).strip('_') or 'projeto'
            export_dir = os.path.join(self.out_dir, safe + '_qfield')
            data_dir = os.path.join(export_dir, 'data')
            os.makedirs(data_dir, exist_ok=True)
            result['export_dir'] = export_dir

            new_proj = QgsProject()
            new_proj.setTitle(project_name)
            new_proj.setCrs(self.project.crs())
            new_proj.setDistanceUnits(self.project.distanceUnits())
            new_proj.setAreaUnits(self.project.areaUnits())
            try:
                new_proj.writeEntry('Paths', '/Absolute', False)
            except Exception:
                pass

            self._prepare_layers(data_dir, new_proj)
            self._rebuild_tree(self.project.layerTreeRoot(), new_proj.layerTreeRoot())

            qgs_path = os.path.join(export_dir, safe + '.qgs')
            new_proj.setFileName(qgs_path)
            if not new_proj.write(qgs_path):
                raise RuntimeError('Falha ao gravar o projeto: ' + qgs_path)
            result['ok'] = True
            result['project_path'] = qgs_path
            result['count'] = len(self.clones)
        except Exception as err:
            result['error'] = str(err)
        return result

    def _prepare_layers(self, data_dir, new_proj):
        used_names = set()
        for layer in self.layers:
            if layer is None:
                continue
            if layer.type() == LayerType.AnnotationLayer:
                if self.remove_annotations:
                    self.warnings.append(
                        'Camada de anotacoes "' + layer.name() + '" nao foi incluida.')
                    continue
            if hasattr(layer, 'isModified') and layer.isModified():
                self.warnings.append(
                    'Camada "' + layer.name() + '": tem edicoes por gravar; '
                    'grave as edicoes no QGIS antes de exportar para o QField.')
            try:
                clone = layer.clone()
            except Exception as err:
                self.warnings.append('Camada "' + layer.name() + '" nao pode ser clonada (' + str(err) + ').')
                continue
            if clone is None:
                self.warnings.append('Camada "' + layer.name() + '" nao pode ser clonada.')
                continue
            provider = clone.dataProvider()
            provider_name = provider.name() if provider is not None else ''
            if provider_name == 'memory':
                pass
            elif provider_name in LOCAL_PROVIDERS:
                if self.copy_data:
                    self._relocate_layer(clone, provider_name, data_dir, used_names)
            else:
                self.warnings.append(
                    'Camada "' + layer.name() + '": fonte remota (' + provider_name
                    + ') mantida como referencia externa.')
            if self.remove_labels and clone.type() == LayerType.VectorLayer:
                clone.setLabelsEnabled(False)
                clone.setLabeling(QgsPalLayerSettings())
            new_proj.addMapLayer(clone, False)
            self.clones[layer] = clone

    def _relocate_layer(self, clone, provider_name, data_dir, used_names):
        src = clone.source()
        file_path = self._extract_file_path(provider_name, src)
        if not file_path or not os.path.isfile(file_path):
            self.warnings.append(
                'Camada "' + clone.name() + '": ficheiro nao encontrado ('
                + src + '); caminho original mantido.')
            return
        dest_name = _unique_name(os.path.basename(file_path), used_names)
        dest_path = os.path.join(data_dir, dest_name)
        try:
            shutil.copy2(file_path, dest_path)
            self._copy_sidecars(file_path, data_dir, dest_path)
        except Exception as err:
            self.warnings.append(
                'Camada "' + clone.name() + '": falha ao copiar "' + file_path
                + '" (' + str(err) + '); caminho original mantido.')
            return
        export_dir = os.path.dirname(data_dir)
        rel = os.path.join(os.path.relpath(data_dir, export_dir), dest_name)
        rel = rel.replace('\\', '/')
        if provider_name == 'ogr':
            suffix = src.split('|', 1)[1] if '|' in src else ''
            new_src = rel + ('|' + suffix if suffix else '')
        elif provider_name == 'delimitedtext':
            query = src.split('?', 1)[1] if '?' in src else ''
            new_src = 'file://' + quote(rel) + (('?' + query) if query else '')
        else:
            new_src = rel
        options = QgsDataProvider.ProviderOptions()
        try:
            clone.setDataSource(new_src, clone.name(), provider_name, options, False)
        except Exception as err:
            self.warnings.append(
                'Camada "' + clone.name() + '": nao foi possivel redefinir a fonte ("'
                + new_src + '"): ' + str(err))
            return
        if not clone.isValid():
            self.warnings.append(
                'Camada "' + clone.name() + '": ficou invalida apos a copia de dados.')

    @staticmethod
    def _copy_sidecars(src_path, data_dir, dest_path):
        src_stem = os.path.splitext(src_path)[0]
        dest_stem = os.path.splitext(dest_path)[0]
        src_low = src_path.lower()
        if src_low.endswith('.shp'):
            pattern = glob.escape(src_stem) + '.*'
            for cand in glob.glob(pattern):
                if cand.lower() == src_low:
                    continue
                tail = cand[len(src_stem):]
                try:
                    shutil.copy2(cand, dest_stem + tail)
                except Exception:
                    pass
            return
        for ext in SIDECAR_SUFFIXES:
            cand = src_stem + ext
            if os.path.isfile(cand):
                try:
                    shutil.copy2(cand, dest_stem + ext)
                except Exception:
                    pass

    @staticmethod
    def _extract_file_path(provider_name, src):
        try:
            if provider_name == 'delimitedtext':
                url = src if '://' in src else 'file://' + src
                path = urlparse(url).path
            else:
                part = src.split('|', 1)[0]
                if part.lower().startswith('file='):
                    part = part[5:]
                if '://' in part:
                    path = urlparse(part).path
                else:
                    path = part
            path = unquote(path)
            if re.match(r'^/[A-Za-z]:', path):
                path = path[1:]
            return path
        except Exception:
            return ''

    def _rebuild_tree(self, src_node, dst_node):
        for child in src_node.children():
            if isinstance(child, QgsLayerTreeGroup):
                new_group = dst_node.addGroup(child.name())
                self._rebuild_tree(child, new_group)
                new_group.setExpanded(child.isExpanded())
                new_group.setItemVisibilityChecked(child.itemVisibilityChecked())
                if not len(new_group.children()):
                    dst_node.removeChildNode(new_group)
            elif isinstance(child, QgsLayerTreeLayer):
                layer = child.layer()
                if layer is None:
                    self.warnings.append(
                        'Camada indisponivel ou quebrada no projeto original foi ignorada.')
                    continue
                clone = self.clones.get(layer)
                if clone is None:
                    continue
                node = dst_node.addLayer(clone)
                node.setExpanded(child.isExpanded())
                node.setItemVisibilityChecked(child.itemVisibilityChecked())
