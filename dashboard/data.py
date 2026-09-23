"""Read-only UI contract validation; identifiers never pass through float."""
from pathlib import Path
from io import BytesIO
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
ROLES = {'consolidator', 'transit', 'distributor', 'terminal', 'coordinator', 'peripheral'}
NODE_COLUMNS = 'gid role role_score cluster_id priority_score evidence in_deg out_deg in_kzt out_kzt pagerank pass_through depth is_seed truncated_by_depth'.split()
CLUSTER_COLUMNS = 'cluster_id n_nodes n_seed sum_kzt_internal top_gids hypothesis'.split()
TOP_COLUMNS = 'rank gid role priority_score why'.split()


def resolve_path(value):
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def require(frame, columns, name):
    missing = set(columns) - set(frame)
    if missing:
        raise ValueError(f'{name}: отсутствуют колонки {sorted(missing)}')
    if frame.empty or frame[columns].isna().any().any():
        raise ValueError(f'{name}: пустые данные или обязательные значения')
    for column in columns:
        if frame[column].astype(str).str.strip().str.lower().isin(['', 'nan', 'inf', '-inf']).any():
            raise ValueError(f'{name}: пустое/нечисловое значение {column}')


def identifiers(series, name):
    if not series.astype(str).str.fullmatch(r'\d+').all():
        raise ValueError(f'{name}: ожидаются целые идентификаторы без потери точности')
    return series.astype(str)


def numeric(frame, columns, name):
    for column in columns:
        frame[column] = pd.to_numeric(frame[column], errors='raise')
        if not np.isfinite(frame[column]).all() or (frame[column] < 0).any():
            raise ValueError(f'{name}: {column} должен быть конечным и неотрицательным')


def load_bundle(data_dir, out_dir):
    data_dir, out_dir = resolve_path(data_dir), resolve_path(out_dir)
    nodes = pd.read_csv(out_dir / 'nodes_roles.csv', dtype=str, keep_default_na=False)
    clusters = pd.read_csv(out_dir / 'clusters.csv', dtype=str, keep_default_na=False)
    top = pd.read_csv(out_dir / 'top_nodes.csv', dtype=str, keep_default_na=False)
    edges = pd.read_parquet(data_dir / 'edges.parquet')
    return _validate_bundle(nodes, clusters, top, edges)


def _validate_bundle(nodes, clusters, top, edges):
    for frame, cols, name in [(nodes, NODE_COLUMNS, 'nodes_roles'), (clusters, CLUSTER_COLUMNS, 'clusters'), (top, TOP_COLUMNS, 'top_nodes')]:
        require(frame, cols, name)
    for frame in [nodes, top]:
        frame['gid'] = identifiers(frame.gid, 'gid')
        if frame.gid.duplicated().any() or not frame.role.isin(ROLES).all():
            raise ValueError('Повторный gid или неизвестная роль')
        numeric(frame, ['priority_score'], 'score')
        if not frame.priority_score.between(0, 1).all():
            raise ValueError('priority_score вне [0,1]')
    numeric(nodes, ['role_score', 'in_deg', 'out_deg', 'in_kzt', 'out_kzt', 'pagerank', 'pass_through', 'depth'], 'nodes')
    if not nodes.role_score.between(0, 1).all():
        raise ValueError('role_score вне [0,1]')
    for column in ['in_deg', 'out_deg', 'depth']:
        if (nodes[column] % 1 != 0).any():
            raise ValueError(f'{column}: ожидается целое число')
    for column in ['is_seed', 'truncated_by_depth']:
        values = nodes[column].str.lower()
        if not values.isin(['true', 'false', '0', '1']).all():
            raise ValueError(f'{column}: некорректный boolean')
        nodes[column] = values.isin(['true', '1'])
    for column in ['fast2', 'in_tx', 'out_tx']:
        if column in nodes:
            numeric(nodes, [column], 'nodes')
    parts = [column for column in nodes if column.startswith('priority_part_')]
    if parts:
        numeric(nodes, parts, 'priority components')
        if len(parts) != 7 or not np.allclose(nodes[parts].sum(axis=1), nodes.priority_score, atol=1e-12, rtol=0):
            raise ValueError('Вклады приоритета не совпадают с итогом')
    if 'fast2' in nodes and not nodes.fast2.between(0, 1).all():
        raise ValueError('fast2 вне [0,1]')
    if not nodes.evidence.str.len().le(200).all() or not nodes.evidence.str.contains(r'\d').all():
        raise ValueError('evidence должен содержать числа и быть <=200 символов')
    nodes['cluster_id'] = identifiers(nodes.cluster_id, 'cluster_id')
    clusters['cluster_id'] = identifiers(clusters.cluster_id, 'cluster_id')
    numeric(clusters, ['n_nodes', 'n_seed', 'sum_kzt_internal'], 'clusters')
    if clusters.cluster_id.duplicated().any() or set(nodes.cluster_id) != set(clusters.cluster_id):
        raise ValueError('Набор кластеров не совпадает')
    indexed = nodes.set_index('gid')
    if len(top) < 20 or not set(top.gid) <= set(nodes.gid):
        raise ValueError('top_nodes: нужно >=20 существующих gid')
    expected = indexed.loc[top.gid]
    if list(expected.role) != list(top.role) or not np.allclose(expected.priority_score, top.priority_score, rtol=0, atol=1e-12):
        raise ValueError('top_nodes не совпадает с nodes_roles')
    numeric(top, ['rank'], 'top')
    order = sorted(top.to_dict('records'), key=lambda row: (-row['priority_score'], int(row['gid'])))
    if list(top.gid) != [row['gid'] for row in order] or list(top['rank']) != list(range(1, len(top) + 1)):
        raise ValueError('top_nodes: неверный порядок или rank')
    for row in clusters.itertuples():
        members = nodes[nodes.cluster_id == row.cluster_id]
        if row.n_nodes != len(members) or row.n_seed != members.is_seed.sum():
            raise ValueError('Количество узлов/seed кластера не совпадает')
        if not set(row.top_gids.split(';')) <= set(members.gid):
            raise ValueError('top_gids вне своего кластера')
    require(edges, ['src', 'dst', 'sum_kzt', 'n_tx'], 'edges')
    for column in ['src', 'dst']:
        edges[column] = identifiers(edges[column], column)
        if not set(edges[column]) <= set(nodes.gid):
            raise ValueError('Концы рёбер отсутствуют в nodes_roles')
    numeric(edges, ['sum_kzt', 'n_tx'], 'edges')
    if edges.duplicated(['src', 'dst']).any() or (edges.n_tx % 1 != 0).any():
        raise ValueError('Повторные пары или некорректное количество переводов')
    for row in clusters.itertuples():
        members = set(nodes.loc[nodes.cluster_id == row.cluster_id, 'gid'])
        total = edges.loc[edges.src.isin(members) & edges.dst.isin(members), 'sum_kzt'].sum()
        if abs(total - row.sum_kzt_internal) > .01:
            raise ValueError('Внутренний оборот кластера не совпадает с edges')
    return nodes, clusters, top, edges


def signature(data_dir, out_dir):
    paths = [resolve_path(out_dir) / name for name in ['nodes_roles.csv', 'clusters.csv', 'top_nodes.csv']]
    paths.extend(resolve_path(data_dir) / f'{name}.parquet' for name in ('nodes', 'edges', 'transactions'))
    result = []
    for path in paths:
        stat = path.stat()
        result.append((str(path), stat.st_mtime_ns, stat.st_size))
    return tuple(result)


def load_files_bundle(data_dir, out_dir):
    """Capture and validate all six files once, including raw transfer consistency.

    The returned UI frames and archived/AI bytes represent the same file version.
    A concurrent pipeline write produces an explicit retry instead of mixed data.
    """
    from src.load import validate

    data_dir, out_dir = resolve_path(data_dir), resolve_path(out_dir)
    before = signature(data_dir, out_dir)
    files = {f'data/{name}.parquet': (data_dir / f'{name}.parquet').read_bytes()
             for name in ('nodes', 'edges', 'transactions')}
    files.update({f'out/{name}.csv': (out_dir / f'{name}.csv').read_bytes()
                  for name in ('nodes_roles', 'clusters', 'top_nodes')})
    if signature(data_dir, out_dir) != before:
        raise ValueError('Файлы обновились во время чтения. Дождитесь завершения расчёта и повторите загрузку.')
    raw_nodes, raw_edges, tx = [pd.read_parquet(BytesIO(files[f'data/{name}.parquet']))
                               for name in ('nodes', 'edges', 'transactions')]
    tx['date'] = pd.to_datetime(tx.date, errors='raise')
    validate(raw_edges, raw_nodes, tx)
    outputs = [pd.read_csv(BytesIO(files[f'out/{name}.csv']), dtype=str, keep_default_na=False)
               for name in ('nodes_roles', 'clusters', 'top_nodes')]
    bundle = _validate_bundle(*outputs, raw_edges)
    nodes = bundle[0].set_index('gid')
    source = raw_nodes.assign(gid=raw_nodes.gid.astype(str)).set_index('gid')
    if set(source.index) != set(nodes.index):
        raise ValueError('Узлы исходного parquet не совпадают с nodes_roles.csv. Повторите расчёт.')
    source = source.loc[nodes.index]
    if not np.array_equal(source.depth, nodes.depth) or not np.array_equal(source.is_seed, nodes.is_seed):
        raise ValueError('Глубина или seed в исходном parquet не совпадают с CSV. Повторите расчёт.')
    return bundle, files


def node_warnings(row):
    warnings = []
    if row['is_seed']:
        warnings.append('Seed: входящие переводы неполны; баланс и pass_through не описывают полный поток.')
    if row['depth'] == 4 and row['out_deg'] == 0:
        warnings.append('Граница depth=4: дальнейшие переводы неизвестны. Это не доказательство конечного получателя.')
    if row['in_kzt'] == 0:
        warnings.append('Нет наблюдаемого входа: pass_through=0 — техническое значение, а не отсутствие транзита.')
    return warnings
