import datetime
import glob
import os
import re
import shutil
import zipfile as _zipfile
from urllib.parse import quote, unquote, urlparse

from qgis import core as _qgis_core
from qgis.core import (
    QgsApplication,
    QgsDataProvider,
    QgsLayerTreeGroup,
    QgsLayerTreeLayer,
    QgsPalLayerSettings,
    QgsProject,
    QgsRenderContext,
    QgsVectorFileWriter,
    QgsVectorLayerExporter,
)

try:
    from qgis.core import Qgis
    LayerType = Qgis.LayerType
except ImportError:
    from qgis.core import QgsMapLayerType
    LayerType = QgsMapLayerType


def _disable_labels_on_settings(settings):
    if settings is None:
        return False
    ok = False
    for attr in ('drawLabels', 'enabled'):
        try:
            setattr(settings, attr, False)
            ok = True
            break
        except Exception:
            continue
    return ok

PLUGIN_VERSION = '1.2.0'

LOCAL_PROVIDERS = {'ogr', 'gdal', 'delimitedtext'}

CAD_EXTENSIONS = ('.dxf', '.dwg', '.dgn')
LABEL_LAYER_PATTERN = re.compile(r'(?i)(\blabel\b|\btexto\b|\bannotations?\b)')

ICON_LAYER_SPECS = (
    ('QgsSvgMarkerSymbolLayer', 'path', 'setPath'),
    ('QgsRasterMarkerSymbolLayer', 'path', 'setPath'),
    ('QgsSVGFillSymbolLayer', 'svgFilePath', 'setSvgFilePath'),
    ('QgsRasterFillSymbolLayer', 'imageFilePath', 'setImageFilePath'),
)

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
                 remove_labels=True, remove_annotations=True, create_zip=True,
                 compact=True, drop_label_layers=True):
        self.project = project
        self.layers = layers
        self.out_dir = out_dir
        self.copy_data = copy_data
        self.remove_labels = remove_labels
        self.remove_annotations = remove_annotations
        self.create_zip = create_zip
        self.compact = compact
        self.drop_label_layers = drop_label_layers
        self._gpkg_path = None
        self._icon_names = set()
        self.warnings = []
        self.clones = {}

    def run(self):
        result = {
            'ok': False,
            'count': 0,
            'export_dir': '',
            'project_path': '',
            'zip_path': '',
            'summary': '',
            'warnings': self.warnings,
            'error': '',
        }
        try:
            get_name = getattr(self.project, 'title', None)
            project_name = (get_name() if get_name else '')
            if not project_name:
                get_name = getattr(self.project, 'projectName', None)
                project_name = (get_name() if get_name else '')
            project_name = project_name or 'projeto'
            safe = re.sub(r'[^A-Za-z0-9_\-]+', '_', project_name).strip('_') or 'projeto'
            export_dir = os.path.join(self.out_dir, safe + '_qfield')
            data_dir = os.path.join(export_dir, 'data')
            os.makedirs(data_dir, exist_ok=True)
            result['export_dir'] = export_dir

            new_proj = QgsProject()
            new_proj.setTitle(project_name)
            new_proj.setCrs(self.project.crs())
            try:
                new_proj.setDistanceUnits(self.project.distanceUnits())
                new_proj.setAreaUnits(self.project.areaUnits())
            except Exception:
                pass
            try:
                new_proj.writeEntry('Paths', '/Absolute', False)
            except Exception:
                pass

            self._prepare_layers(data_dir, new_proj)
            for cloned in self.clones.values():
                if hasattr(cloned, 'renderer'):
                    self._export_layer_icons(cloned, data_dir)
            self._migrate_relations(new_proj)
            self._rebuild_tree(self.project.layerTreeRoot(), new_proj.layerTreeRoot())

            qgs_path = os.path.join(export_dir, safe + '.qgs')
            new_proj.setFileName(qgs_path)
            if not new_proj.write(qgs_path):
                raise RuntimeError('Falha ao gravar o projeto: ' + qgs_path)
            if self.remove_labels:
                self._purge_labels_from_project(qgs_path)
            self._self_check(qgs_path)
            self._write_log(export_dir)
            self._write_marker(safe, export_dir)
            zip_path = ''
            if self.create_zip:
                try:
                    zip_path = shutil.make_archive(
                        export_dir, 'zip', root_dir=self.out_dir,
                        base_dir=os.path.basename(export_dir))
                except Exception as err:
                    self.warnings.append(
                        'Falha ao criar o ficheiro ZIP (' + str(err)
                        + '); use a pasta gerada.')
            result['ok'] = True
            result['project_path'] = qgs_path
            result['zip_path'] = zip_path
            result['count'] = len(self.clones)
            result['summary'] = str(getattr(self, 'check_summary', ''))
        except Exception as err:
            result['error'] = str(err)
        return result

    def _prepare_layers(self, data_dir, new_proj):
        used_names = set()
        for layer in self.layers:
            if layer is None:
                continue
            if self.drop_label_layers and LABEL_LAYER_PATTERN.search(layer.name() or ''):
                self.warnings.append(
                    'Camada de texto/etiquetas CAD "' + layer.name()
                    + '" excluida (opcao de excluir subcamadas de labels ativa).')
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
            try:
                clone.setId(layer.id())
            except Exception:
                pass
            try:
                clone.setName(layer.name())
            except Exception:
                pass
            provider = clone.dataProvider()
            provider_name = provider.name() if provider is not None else ''
            if provider_name == 'memory':
                pass
            elif provider_name in LOCAL_PROVIDERS:
                if self.copy_data:
                    is_cad = self._is_cad_source(clone)
                    converted = False
                    if self.compact and not is_cad \
                            and clone.type() == LayerType.VectorLayer:
                        converted = self._compact_layer(clone, data_dir)
                    if not converted:
                        self._relocate_layer(clone, provider_name, data_dir, used_names)
            else:
                self.warnings.append(
                    'Camada "' + layer.name() + '": fonte remota (' + provider_name
                    + ') mantida como referencia externa.')
            if self.remove_labels and clone.type() == LayerType.VectorLayer:
                self._disable_labels(clone)
            new_proj.addMapLayer(clone, False)
            self.clones[layer] = clone

    def _purge_labels_from_project(self, qgs_path):
        try:
            with open(qgs_path, 'r', encoding='utf-8') as fh:
                content = fh.read()
            original = content
            patterns = (
                re.compile(r'<labeling\b[^>]*>.*?</labeling>', re.S),
                re.compile(r'<labeling\b[^>]*/>'),
                re.compile(r'\s*labelsEnabled="1"'),
                re.compile(r'\s*labelsEnabled="true"'),
            )
            for pattern in patterns:
                content = pattern.sub('', content)
            if content != original:
                with open(qgs_path, 'w', encoding='utf-8', newline='') as fh:
                    fh.write(content)
        except Exception as err:
            self.warnings.append(
                'Nao foi possivel retirar etiquetas do XML final (' + str(err) + ').')

    def _write_marker(self, safe, export_dir):
        try:
            with open(os.path.join(export_dir,
                                   'EXPORTADO_POR_v' + PLUGIN_VERSION + '.txt'),
                      'w', encoding='utf-8') as fh:
                fh.write('Projeto: ' + safe + '\n'
                         'Plugin: QField Cadastro Export v' + PLUGIN_VERSION + '\n'
                         'Data: ' + str(datetime.datetime.now()) + '\n\n'
                         'Se este ficheiro nao existir, a pasta foi gerada por uma '
                         'versao antiga do plugin.\n')
        except Exception:
            pass

    def _self_check(self, qgs_path):
        try:
            check = QgsProject()
            check.read(qgs_path)
            layers = list(check.mapLayers().values())
            invalid = [l.name() for l in layers if not l.isValid()]
            tree = check.layerTreeRoot()
            counts = {'groups': 0, 'layers': 0}

            def walk(node):
                for child in node.children():
                    if isinstance(child, QgsLayerTreeGroup):
                        counts['groups'] += 1
                        walk(child)
                    else:
                        counts['layers'] += 1

            walk(tree)
            try:
                with open(qgs_path, 'r', encoding='utf-8') as fh:
                    xml_text = fh.read()
                label_tokens = xml_text.lower().count('<labeling')
            except Exception:
                label_tokens = -1
            self.check_summary = ('releitura OK: ' + str(len(layers))
                                  + ' camadas (' + str(len(invalid)) + ' invalidas), '
                                  + str(counts['groups']) + ' grupos, '
                                  + str(counts['layers']) + ' nos na arvore, '
                                  + 'labels no XML: ' + str(label_tokens))
            if label_tokens > 0:
                self.warnings.append(
                    'AVISO CRITICO: sobreviveram ' + str(label_tokens)
                    + ' blocos de labeling no XML exportado.')
            if len(layers) != len(self.clones):
                self.warnings.append(
                    'AVISO: projeto exportado tem ' + str(len(layers))
                    + ' camadas, esperadas ' + str(len(self.clones))
                    + '. A estrutura pode ter divergido.')
            if invalid:
                self.warnings.append(
                    'AVISO: ' + str(len(invalid))
                    + ' camada(s) INVALIDA(S): ' + ', '.join(invalid))
        except Exception as err:
            self.warnings.append('Auto-verificacao nao concluida (' + str(err) + ').')

    def _write_log(self, export_dir):
        try:
            lines = ['QField Cadastro Export v' + PLUGIN_VERSION,
                     'Data: ' + str(datetime.datetime.now()),
                     'Camadas exportadas: ' + str(len(self.clones)),
                     'Compacto: ' + str(self.compact) + ' | Copiar dados: '
                     + str(self.copy_data) + ' | Sem labels: ' + str(self.remove_labels),
                     'Resumo: ' + str(getattr(self, 'check_summary', '')),
                     'Avisos (' + str(len(self.warnings)) + '):']
            lines.extend('- ' + w for w in self.warnings)
            with open(os.path.join(export_dir, 'export_log.txt'), 'w',
                      encoding='utf-8') as fh:
                fh.write('\n'.join(lines))
        except Exception:
            pass

    def _export_layer_icons(self, clone, data_dir):
        try:
            renderer = clone.renderer()
            if renderer is None:
                return
            try:
                symbols = renderer.symbols(QgsRenderContext())
            except TypeError:
                symbols = renderer.symbols()
        except Exception:
            return
        if not symbols:
            return
        icon_dir = os.path.join(data_dir, 'svg')
        try:
            os.makedirs(icon_dir, exist_ok=True)
        except Exception:
            return
        copied = 0
        for sym in symbols:
            try:
                count = sym.symbolLayerCount()
            except Exception:
                continue
            for i in range(count):
                try:
                    sl = sym.symbolLayer(i)
                except Exception:
                    continue
                if sl is None:
                    continue
                for cls_name, getter, setter in ICON_LAYER_SPECS:
                    cls = getattr(_qgis_core, cls_name, None)
                    if cls is None or not isinstance(sl, cls):
                        continue
                    try:
                        path = getattr(sl, getter)()
                    except Exception:
                        continue
                    if not path or path.startswith('base64:') or '://' in path:
                        continue
                    icon_path = path
                    if not os.path.isfile(icon_path):
                        try:
                            candidate = os.path.join(QgsApplication.svgPath(),
                                                     path.lstrip('/').lstrip('\\'))
                            if os.path.isfile(candidate):
                                icon_path = candidate
                            else:
                                continue
                        except Exception:
                            continue
                    dest = _unique_name(os.path.basename(icon_path), self._icon_names)
                    try:
                        shutil.copy2(icon_path, os.path.join(icon_dir, dest))
                    except Exception:
                        continue
                    try:
                        getattr(sl, setter)('data/svg/' + dest)
                        copied += 1
                    except Exception:
                        pass
        if copied:
            self.warnings.append(
                'Camada "' + clone.name() + '": ' + str(copied)
                + ' icone(s) personalizados copiados para data/svg/.')

    def _migrate_relations(self, new_proj):
        try:
            src_mgr = self.project.relationManager()
            dst_mgr = new_proj.relationManager()
        except Exception:
            return
        try:
            relations = list(src_mgr.relations().values())
        except Exception:
            return
        if not relations:
            return
        clone_ids = {c.id() for c in self.clones.values()}
        for rel in relations:
            try:
                ref_child = rel.referencingLayerId()
                ref_parent = rel.referencedLayerId()
                if ref_child not in clone_ids or ref_parent not in clone_ids:
                    self.warnings.append(
                        'Relacao "' + rel.name() + '" nao foi incluida (usa camadas fora da exportacao).')
                    continue
                new_rel = type(rel)()
                try:
                    new_rel.setId(rel.id())
                except Exception:
                    pass
                try:
                    new_rel.setName(rel.name())
                except Exception:
                    pass
                new_rel.setReferencingLayer(ref_child)
                new_rel.setReferencedLayer(ref_parent)
                try:
                    for child_field, parent_field in rel.fieldPairs().items():
                        new_rel.addFieldPair(child_field, parent_field)
                except Exception:
                    pass
                try:
                    new_rel.setStrength(rel.strength())
                except Exception:
                    pass
                dst_mgr.addRelation(new_rel)
            except Exception as err:
                self.warnings.append(
                    'Relacao "' + rel.name() + '" nao foi migrada (' + str(err) + ').')

    def _disable_labels(self, layer):
        try:
            layer.setLabelsEnabled(False)
        except Exception:
            pass
        try:
            labeling = layer.labeling()
        except Exception:
            labeling = None
        if labeling is None:
            return
        try:
            from qgis.core import QgsVectorLayerSimpleLabeling
            if isinstance(labeling, QgsVectorLayerSimpleLabeling):
                settings = labeling.settings()
                if _disable_labels_on_settings(settings):
                    labeling.setSettings(settings)
                    layer.setLabeling(labeling)
                return
        except ImportError:
            pass
        try:
            from qgis.core import QgsRuleBasedLabeling
            if isinstance(labeling, QgsRuleBasedLabeling):
                def walk(rule):
                    try:
                        settings = rule.settings()
                        if _disable_labels_on_settings(settings):
                            rule.setSettings(settings)
                    except Exception:
                        pass
                    for sub in rule.children():
                        walk(sub)
                walk(labeling.rootRule())
                layer.setLabeling(labeling)
                return
        except ImportError:
            pass
        try:
            settings = labeling.settings()
            if _disable_labels_on_settings(settings):
                labelings_cls = getattr(_qgis_core, 'QgsBasicVectorLayerLabeling', None)
                if labelings_cls is None:
                    labelings_cls = getattr(_qgis_core, 'QgsVectorLayerSimpleLabeling', None)
                if labelings_cls is not None:
                    layer.setLabeling(labelings_cls(settings))
        except Exception:
            pass

    @staticmethod
    def _is_cad_source(clone):
        try:
            path = Exporter._extract_file_path(
                clone.dataProvider().name() if clone.dataProvider() else '',
                clone.source())
            return path.lower().endswith(CAD_EXTENSIONS)
        except Exception:
            return False

    def _compact_layer(self, clone, data_dir):
        try:
            clean = re.sub(r'[^A-Za-z0-9_]+', '_', clone.name()).strip('_') or 'camada'
            if clean[0].isdigit():
                clean = 'L' + clean
            clean = clean[:60]
            gpkg_name = self._unique_gpkg_file(clean, data_dir)
            self._gpkg_path = os.path.join(data_dir, gpkg_name)
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = 'GPKG'
            options.layerName = clean
            try:
                action_cls = getattr(QgsVectorFileWriter, 'ActionOnExistingFile', None)
                if action_cls is None:
                    action_cls = QgsVectorFileWriter
                options.actionOnExistingFile = action_cls.CreateOrOverwriteFile
            except Exception:
                options.actionOnExistingFile = getattr(QgsVectorFileWriter,
                                                       'CreateOrOverwriteFile', None)
            try:
                export_err = QgsVectorLayerExporter.exportLayer(
                    clone, self._gpkg_path, 'ogr', clone.crs(), True, options)
            except Exception as err:
                self.warnings.append(
                    'Camada "' + clone.name() + '": conversao para GeoPackage falhou ('
                    + str(err) + '); dados copiados em formato original.')
                return False
            err_code = export_err[0] if isinstance(export_err, tuple) else export_err
            no_err = getattr(QgsVectorLayerExporter, 'NoError', None)
            if no_err is not None and err_code != no_err:
                self.warnings.append(
                    'Camada "' + clone.name() + '": conversao para GeoPackage devolveu erro ('
                    + str(err_code) + '); dados copiados em formato original.')
                return False
            export_dir = os.path.dirname(data_dir)
            rel_gpkg = os.path.join(os.path.relpath(data_dir, export_dir),
                                    gpkg_name).replace('\\', '/')
            options_ds = QgsDataProvider.ProviderOptions()
            clone.setDataSource(rel_gpkg + '|layername=' + clean,
                                clone.name(), 'ogr', options_ds, False)
            if not clone.isValid():
                self.warnings.append(
                    'Camada "' + clone.name() + '": ficou invalida apos a conversao compacta.')
                return False
            return True
        except Exception as err:
            self.warnings.append(
                'Camada "' + clone.name() + '": modo compacto falhou (' + str(err)
                + '); usado formato original.')
            return False

    @staticmethod
    def _unique_gpkg_file(clean, data_dir):
        name = clean + '.gpkg'
        stem, ext = os.path.splitext(name)
        n = 1
        while os.path.exists(os.path.join(data_dir, name)):
            n += 1
            name = '{}_{}{}'.format(stem, n, ext)
        return name

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
        try:
            clone.saveNamedStyle(os.path.splitext(dest_path)[0] + '.qml')
        except Exception:
            pass
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
