"""Read-only MariaDB analytical snapshots, using the existing private CLI client.

The UI and its offline investigation receive the same six in-memory files for
either source. Account/session storage is independent of this data source.
"""
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from dashboard.data import CLUSTER_COLUMNS, NODE_COLUMNS, ROOT, TOP_COLUMNS, load_bundle
from src.database import execute, literal, settings
from src.export import validate_outputs
from src.load import validate


class DatabaseSourceError(ValueError):
    """An actionable database error safe to display without credentials or SQL."""


_SCHEMA = {
    'nodes': ['gid', 'depth', 'is_seed'],
    'edges': ['src', 'dst', 'sum_kzt', 'n_tx', 'depth'],
    'transactions': ['src', 'dst', 'date', 'sum_kzt'],
    'clusters': CLUSTER_COLUMNS,
    'nodes_roles': NODE_COLUMNS + ['metrics'],
    'top_nodes': TOP_COLUMNS,
}
_ORDER = {'nodes': 'gid', 'edges': 'src,dst', 'transactions': 'tx_index',
          'clusters': 'cluster_id', 'nodes_roles': 'gid', 'top_nodes': '`rank`'}
_LIMIT = {'nodes': 10000, 'edges': 30000, 'transactions': 100000,
          'clusters': 10000, 'nodes_roles': 10000, 'top_nodes': 10000}
_INTEGER = {'gid', 'src', 'dst', 'depth', 'n_tx', 'cluster_id', 'n_nodes',
            'n_seed', 'in_deg', 'out_deg', 'rank'}
_FLOAT = {'sum_kzt', 'sum_kzt_internal', 'role_score', 'priority_score',
          'in_kzt', 'out_kzt', 'pagerank', 'pass_through'}
_BOOLEAN = {'is_seed', 'truncated_by_depth'}
_RUN_ID = re.compile(r'[0-9a-f]{64}')


def database_configured(env_file=ROOT / '.env'):
    """Whether MYSQL_* credentials exist; never opens a network connection."""
    try:
        settings(env_file)
    except (OSError, ValueError):
        return False
    return True


def _read(sql, env_file):
    try:
        config = settings(env_file)
    except (OSError, ValueError):
        raise DatabaseSourceError('Проверьте настройки MYSQL_* в .env или переменных окружения.') from None
    try:
        # A consistent snapshot prevents mixing versions during a concurrent import.
        return execute('SET SESSION max_statement_time=10;\n'
                       'START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY;\n'
                       + sql + '\nCOMMIT;', config)
    except subprocess.TimeoutExpired:
        raise DatabaseSourceError('MariaDB не ответила вовремя. Повторите загрузку или выберите локальные файлы.') from None
    except (OSError, RuntimeError):
        raise DatabaseSourceError('Не удалось прочитать MariaDB. Проверьте доступ к серверу, клиент mariadb и права SELECT на таблицы mg_*. Можно выбрать локальные файлы.') from None


def _decode(output):
    """Hex JSON avoids client escaping of tabs, Unicode and embedded newlines."""
    result = []
    try:
        for line in output.splitlines():
            tag, encoded = line.split('\t', 1)
            item = json.loads(bytes.fromhex(encoded).decode('utf-8'))
            if not isinstance(item, dict):
                raise ValueError
            # JSON columns travel as hex strings to keep their original bytes and
            # avoid MariaDB's automatic embedding / mixed JSON-text collations.
            for column in {'runs': ('manifest',), 'nodes_roles': ('metrics',)}.get(tag, ()):
                if column in item:
                    item[column] = bytes.fromhex(item[column]).decode('utf-8')
            result.append((tag, item))
    except (ValueError, TypeError, UnicodeError):
        raise DatabaseSourceError('MariaDB вернула некорректный формат снимка.') from None
    return result


def _manifest(record):
    try:
        run_id = record['run_id']
        encoded = record['manifest']
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise ValueError
        if record['schema_version'] != 1 or not isinstance(encoded, str):
            raise ValueError
        if sha256(encoded.encode()).hexdigest() != run_id:
            raise ValueError
        manifest = json.loads(encoded)
        if str(manifest['schema_version']) != '1' or set(manifest['tables']) != set(_SCHEMA):
            raise ValueError
        for table, meta in manifest['tables'].items():
            if type(meta['rows']) is not int or not 1 <= meta['rows'] <= _LIMIT[table]:
                raise ValueError
            if not isinstance(meta['sha256'], str) or not _RUN_ID.fullmatch(meta['sha256']):
                raise ValueError
        return manifest
    except (KeyError, ValueError, TypeError):
        raise DatabaseSourceError('Манифест снимка повреждён, версия не поддерживается или превышен лимит данных.') from None


def _select(tag, table, columns, where='', order='', limit=1):
    # All identifiers originate in fixed module constants; values use hex literals.
    # MariaDB embeds JSON columns as nested objects. HEX preserves their exact
    # bytes without a text CAST that can conflict with server collations.
    pairs = ','.join(f"'{column}'," + (f'HEX(`{column}`)' if column in ('manifest', 'metrics')
                                     else f'`{column}`') for column in columns)
    return (f"SELECT '{tag}',HEX(JSON_OBJECT({pairs})) FROM mg_{table} "
            f'{where} ' + (f'ORDER BY {order} ' if order else '') + f'LIMIT {limit};')


def list_snapshots(limit=20, *, env_file=ROOT / '.env'):
    """Newest snapshots first. Each item has run_id, created_at and row counts."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise DatabaseSourceError('Лимит списка снимков должен быть от 1 до 100.')
    sql = _select('runs', 'runs', ['run_id', 'schema_version', 'created_at', 'manifest'],
                  order='created_at DESC,run_id', limit=limit)
    snapshots = []
    for tag, record in _decode(_read(sql, env_file)):
        if tag != 'runs':
            raise DatabaseSourceError('MariaDB вернула некорректный список снимков.')
        manifest = _manifest(record)
        snapshots.append({'run_id': record['run_id'], 'created_at': record['created_at'],
                          **{name: meta['rows'] for name, meta in manifest['tables'].items()}})
    return snapshots


def _frame(table, records):
    if not records or any(set(row) != set(_SCHEMA[table]) for row in records):
        raise ValueError('Missing table columns')
    frame = pd.DataFrame.from_records(records, columns=_SCHEMA[table])
    for column in frame:
        if column in _INTEGER:
            # Never use pd.to_numeric on identifiers: invalid mixed values can float.
            values = frame[column]
            if not values.map(lambda value: type(value) is int or (isinstance(value, str) and value.isdigit())).all():
                raise ValueError('Invalid integer')
            frame[column] = pd.Series([int(value) for value in values], dtype='int64')
        elif column in _FLOAT:
            frame[column] = frame[column].astype('float64')
        elif column in _BOOLEAN:
            if not frame[column].map(lambda value: type(value) in (bool, int) and value in (0, 1)).all():
                raise ValueError('Invalid boolean')
            frame[column] = frame[column].astype(bool)
    if table == 'transactions':
        frame['date'] = pd.to_datetime(frame.date, errors='raise')
    if table == 'nodes_roles':
        extras = []
        keys = None
        for value in frame.pop('metrics'):
            values = json.loads(value) if isinstance(value, str) else value
            if not isinstance(values, dict) or set(values) & set(NODE_COLUMNS):
                raise ValueError('Invalid extra metrics')
            if keys is None:
                keys = list(values)
            if set(values) != set(keys):
                raise ValueError('Inconsistent metric columns')
            if not all(type(item) in (bool, int, float) and math.isfinite(item) for item in values.values()):
                raise ValueError('Invalid extra metric value')
            extras.append(values)
        frame = pd.concat([frame, pd.DataFrame(extras, columns=keys)], axis=1)
    return frame


def _restore_decimal_aggregates(frames):
    """Recover binary float sums lost when the importer stores exact SQL cents.

    Source hashes are checked first. Stored SQL aggregates must match the
    recomputed values to the cent; the original canonical SHA still has to match.
    """
    from src.features import basic_features
    from src.graph import build_graph

    edges, nodes = frames['edges'], frames['nodes_roles']
    computed = basic_features(build_graph(edges, frames['nodes']), frames['nodes'])
    for column in ('in_kzt', 'out_kzt'):
        expected = computed[column].to_numpy()
        if not np.array_equal(nodes[column].to_numpy(), np.round(expected, 2)):
            raise ValueError('SQL node aggregate differs from source edges')
        nodes[column] = expected
    mapping = nodes.set_index('gid').cluster_id
    internal = edges.assign(cluster_id=edges.src.map(mapping), dst_cluster=edges.dst.map(mapping))
    totals = internal.loc[internal.cluster_id.eq(internal.dst_cluster)].groupby('cluster_id').sum_kzt.sum()
    clusters = frames['clusters']
    expected = clusters.cluster_id.map(totals).fillna(0.).to_numpy()
    if not np.array_equal(clusters.sum_kzt_internal.to_numpy(), np.round(expected, 2)):
        raise ValueError('SQL cluster aggregate differs from source edges')
    clusters['sum_kzt_internal'] = expected


def load_snapshot(run_id, *, env_file=ROOT / '.env'):
    """Return ``((nodes, clusters, top, edges), files)`` from a verified DB run.

    UI gids are strings; the serialized raw parquet identifiers remain int64 for
    the existing source validator and offline investigation engine. No DB writes.
    """
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise DatabaseSourceError('Некорректный идентификатор снимка MariaDB.')
    where = f'WHERE run_id={literal(run_id)}'
    queries = [_select('runs', 'runs', ['run_id', 'schema_version', 'created_at', 'manifest'], where)]
    queries += [_select(name, name, cols, where, _ORDER[name], _LIMIT[name] + 1)
                for name, cols in _SCHEMA.items()]
    records = {name: [] for name in ('runs', *_SCHEMA)}
    for tag, row in _decode(_read('\n'.join(queries), env_file)):
        if tag not in records:
            raise DatabaseSourceError('MariaDB вернула неизвестную таблицу снимка.')
        records[tag].append(row)
    if len(records['runs']) != 1 or records['runs'][0].get('run_id') != run_id:
        raise DatabaseSourceError('Снимок не найден. Обновите список снимков MariaDB.')
    manifest = _manifest(records['runs'][0])
    for name in _SCHEMA:
        if len(records[name]) != manifest['tables'][name]['rows']:
            raise DatabaseSourceError('Количество строк MariaDB не совпало с манифестом снимка.')
    try:
        frames = {name: _frame(name, records[name]) for name in _SCHEMA}
        validate(frames['edges'], frames['nodes'], frames['transactions'])
        validate_outputs(frames['nodes_roles'], frames['clusters'], frames['top_nodes'])
        if len(frames['nodes']) != 2248:
            raise ValueError('Expected all 2248 source nodes')
        actual = frames['nodes_roles'][['gid', 'depth', 'is_seed']].reset_index(drop=True)
        if not actual.equals(frames['nodes'].reset_index(drop=True)):
            raise ValueError('Source/output node metadata mismatch')
        # SQL DECIMAL values are converted back to the pipeline's float64 values.
        # Compare canonical CSV content, not parquet encoding or database row order.
        for name, frame in frames.items():
            if name == 'clusters':
                # The three raw tables precede outputs and are verified above.
                _restore_decimal_aggregates(frames)
            digest = sha256(frame.to_csv(index=False, float_format='%.17g').encode()).hexdigest()
            if digest != manifest['tables'][name]['sha256']:
                raise ValueError(f'Content hash mismatch: {name}')
        files = {}
        for name, frame in frames.items():
            if name in ('nodes', 'edges', 'transactions'):
                buffer = BytesIO()
                frame.to_parquet(buffer, index=False)
                files[f'data/{name}.parquet'] = buffer.getvalue()
            else:
                files[f'out/{name}.csv'] = frame.to_csv(index=False, float_format='%.17g').encode()
        # Reuse the same UI contract checks and conversions for both sources.
        with TemporaryDirectory(prefix='moneygraph-snapshot-') as temporary:
            directory = Path(temporary)
            for filename, content in files.items():
                path = directory / filename
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(content)
            bundle = load_bundle(directory / 'data', directory / 'out')
    except (ValueError, TypeError, KeyError, OverflowError, OSError):
        raise DatabaseSourceError('Содержимое снимка не прошло проверку схемы, контрольных сумм или согласованности данных. Выберите другой снимок или повторите его импорт.') from None
    return bundle, files
