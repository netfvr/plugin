# QField Cadastro Export

Plugin para o QGIS (3.34+) que exporta para o **QField (Android)** apenas as camadas
selecionadas pelo utilizador, num novo projeto **autónomo** (pasta copiável), preservando
a simbologia original e a hierarquia de grupos, e **removendo etiquetas e anotações**.

## O que faz

1. **Exportação seletiva** – exporta exclusivamente as camadas assinaladas no diálogo
   (pré-marcadas com base na seleção ativa no painel de Camadas do QGIS).
2. **Preserva a estrutura** – recria grupos, subgrupos, ordem e visibilidade das camadas
   tal como estão no projeto original.
3. **Preserva a simbologia** – as camadas são clonadas com o renderizador original
   (símbolos, cores, espessuras, preenchimentos).
4. **Remove etiquetas** – desativa todos os labels das camadas vetoriais no projeto exportado.
5. **Remove anotações** – exclui camadas de anotações (opcional, ativado por defeito).
6. **Projeto autónomo** – copia os ficheiros de dados locais (shapefile com sidecars,
   GeoPackage, raster, CSV) para a subpasta `data/` e converte as referências em caminhos
   relativos; fontes remotas (PostGIS, WMS, WFS) mantêm-se como referência e são avisadas.

## Instalação

1. No QGIS: **Complementos → Gerir e Instalar Complementos → Instalar a partir de ZIP**.
2. Selecione o ficheiro `qfield_cadastro_export.zip` e confirme.
3. O plugin fica ativo de imediato (ícone na barra de ferramentas e menu
   **Complementos → QField Cadastro Export**).

## Utilização

1. (Opcional) No painel de Camadas, selecione as camadas que pretende exportar —
   o diálogo assinala-as automaticamente.
2. Abra o plugin e confirme as camadas, as opções e a **pasta de destino**.
3. Clique **Exportar**. Será criada uma pasta `<Projeto>_qfield/` com:
   - `<Projeto>.qgs` — projeto pronto para o QField;
   - `data/` — dados copiados com caminhos relativos.
4. Copie a pasta inteira para o dispositivo Android e abra o `.qgs` no QField.

## Notas

- Camadas com edições por gravar são assinaladas em avisos; grave as edições no QGIS
  antes de exportar.
- Camadas em bases de dados remotas não são copiadas (o dispositivo precisa de
  acesso à fonte).
