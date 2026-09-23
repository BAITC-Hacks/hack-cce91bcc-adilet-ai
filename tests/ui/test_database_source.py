"""The MariaDB UI reader never mutates data and rejects inconsistent snapshots."""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import subprocess

import pandas as pd
import pytest

from dashboard import database
from dashboard.ai.evidence import CaseData
from dashboard.storage import Store
from src.database import prepare
from src.load import load


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def snapshot():
    edges, nodes, tx = load(ROOT / 'data')
    outputs = tuple(pd.read_csv(ROOT / f'out/{name}.csv', float_precision='round_trip')
                    for name in ('nodes_roles', 'clusters', 'top_nodes'))
    run_id, manifest, _ = prepare(edges, nodes, tx, outputs)
    encoded = json.dumps({'schema_version': '1', 'tables': manifest}, sort_keys=True)
    frames = dict(zip(database._SCHEMA, (nodes, edges, tx, outputs[1], outputs[0], outputs[2])))
    records = {'runs': [{'run_id': run_id, 'schema_version': 1,
                         'created_at': '2026-09-23 15:34:23', 'manifest': encoded}]}
    for name, frame in frames.items():
        records[name] = frame.to_dict('records')
        for row in records[name]:
            for column in {'sum_kzt', 'sum_kzt_internal', 'in_kzt', 'out_kzt'} & row.keys():
                row[column] = round(row[column], 2)  # SQL DECIMAL(24,2)
            if name == 'transactions':
                row['date'] = row['date'].date().isoformat()
            if name == 'nodes_roles':
                metrics = {key: row.pop(key) for key in list(row) if key not in database.NODE_COLUMNS}
                row['metrics'] = json.dumps(metrics)
            for key in database._BOOLEAN & row.keys():
                row[key] = int(row[key])
    return records


def encoded_response(records):
    records = deepcopy(records)
    for name, column in [('runs', 'manifest'), ('nodes_roles', 'metrics')]:
        for row in records.get(name, []):
            row[column] = row[column].encode().hex()
    return '\n'.join(f'{name}\t{json.dumps(row, ensure_ascii=False).encode().hex()}'
                     for name, rows in records.items() for row in rows)


def mocked_database(monkeypatch, records):
    queries = []
    monkeypatch.setattr(database, 'settings', lambda path: {'MYSQL_PASSWORD': 'never-print-this'})
    def execute(sql, config):
        queries.append(sql)
        return encoded_response(records)
    monkeypatch.setattr(database, 'execute', execute)
    return queries


def test_database_roundtrip_preserves_ui_and_offline_investigation(snapshot, monkeypatch, tmp_path):
    queries = mocked_database(monkeypatch, snapshot)
    bundle, files = database.load_snapshot(snapshot['runs'][0]['run_id'])
    nodes, clusters, top, edges = bundle
    assert [len(frame) for frame in bundle] == [2248, 91, 20, 3119]
    assert nodes.gid.map(type).eq(str).all()
    assert edges.src.map(type).eq(str).all()
    assert pd.api.types.is_integer_dtype(pd.read_parquet(BytesIO(files['data/nodes.parquet'])).gid)
    assert len(files) == 6
    data = CaseData.from_files(files)
    assert len(data.tx) == 4840
    store = Store(tmp_path / 'workspace.sqlite3')
    dataset_id = store.snapshot(files)
    assert store.snapshot(files) == dataset_id
    assert 'READ ONLY' in queries[0] and 'max_statement_time=10' in queries[0]
    assert all(word not in queries[0] for word in ('INSERT', 'UPDATE', 'DELETE', 'DROP'))
    assert queries[0].count('LIMIT') == 7
    assert 'never-print-this' not in queries[0]


def test_list_snapshots_has_counts_and_stable_id(snapshot, monkeypatch):
    queries = mocked_database(monkeypatch, {'runs': snapshot['runs']})
    result = database.list_snapshots()
    assert result[0]['run_id'] == snapshot['runs'][0]['run_id']
    assert result[0]['created_at'] == '2026-09-23 15:34:23'
    assert result[0]['nodes'] == 2248
    assert result[0]['transactions'] == 4840
    assert 'ORDER BY created_at DESC,run_id LIMIT 20' in queries[0]
    assert 'HEX(`manifest`)' in queries[0]


@pytest.mark.parametrize('run_id', ["'; DROP TABLE mg_runs; --", '', 'a' * 65, None, 1])
def test_invalid_run_id_never_reaches_database(run_id, monkeypatch):
    monkeypatch.setattr(database, 'execute', lambda *_: pytest.fail('Database must not be contacted'))
    with pytest.raises(database.DatabaseSourceError, match='идентификатор'):
        database.load_snapshot(run_id)


@pytest.mark.parametrize('limit', [0, 101, True, '20'])
def test_invalid_list_limit_never_reaches_database(limit, monkeypatch):
    monkeypatch.setattr(database, 'execute', lambda *_: pytest.fail('Database must not be contacted'))
    with pytest.raises(database.DatabaseSourceError, match='Лимит'):
        database.list_snapshots(limit)


@pytest.mark.parametrize('corruption', ['missing_row', 'amount', 'text', 'float_id', 'metrics', 'manifest'])
def test_corrupt_snapshot_rejected(snapshot, monkeypatch, corruption):
    records = deepcopy(snapshot)
    if corruption == 'missing_row':
        records['nodes'].pop()
    elif corruption == 'amount':
        records['transactions'][0]['sum_kzt'] += 1000
    elif corruption == 'text':
        records['clusters'][0]['hypothesis'] += ' Изменённые сведения.'
    elif corruption == 'float_id':
        records['nodes'][0]['gid'] = float(records['nodes'][0]['gid'])
    elif corruption == 'metrics':
        records['nodes_roles'][0]['metrics'] = '{"gid": 123}'
    else:
        records['runs'][0]['manifest'] += ' '
    mocked_database(monkeypatch, records)
    with pytest.raises(database.DatabaseSourceError):
        database.load_snapshot(records['runs'][0]['run_id'])


def test_integer_parser_preserves_gid_above_javascript_limit():
    value = 9223372036854775807
    frame = database._frame('nodes', [{'gid': value, 'depth': 0, 'is_seed': 1}])
    assert frame.gid.iloc[0] == value
    assert str(frame.gid.iloc[0]) == str(value)
    with pytest.raises(ValueError):
        database._frame('nodes', [{'gid': float(value), 'depth': 0, 'is_seed': 1}])


@pytest.mark.parametrize('error', [RuntimeError('server password=secret private SQL'),
                                  subprocess.TimeoutExpired('command password=secret', 120)])
def test_connection_failure_is_actionable_and_redacted(monkeypatch, error):
    monkeypatch.setattr(database, 'settings', lambda _: {'MYSQL_PASSWORD': 'secret'})
    def fail(*args):
        raise error
    monkeypatch.setattr(database, 'execute', fail)
    with pytest.raises(database.DatabaseSourceError) as caught:
        database.list_snapshots()
    assert 'secret' not in str(caught.value)
    assert 'SQL' not in str(caught.value)
    assert 'файлы' in str(caught.value)


def test_decode_keeps_unicode_tabs_and_newlines():
    record = {'value': 'Казахстан\tНаблюдение\nВторая строка'}
    assert database._decode(encoded_response({'table': [record]})) == [('table', record)]
    with pytest.raises(database.DatabaseSourceError):
        database._decode('table\tbroken hex')


def test_empty_database_and_missing_snapshot(monkeypatch):
    mocked_database(monkeypatch, {})
    assert database.list_snapshots() == []
    with pytest.raises(database.DatabaseSourceError, match='не найден'):
        database.load_snapshot('a' * 64)


def test_missing_settings_do_not_open_connection(monkeypatch):
    def missing(_):
        raise ValueError('MYSQL_PASSWORD')
    monkeypatch.setattr(database, 'settings', missing)
    monkeypatch.setattr(database, 'execute', lambda *_: pytest.fail('No settings'))
    assert not database.database_configured()
    with pytest.raises(database.DatabaseSourceError, match='MYSQL_'):
        database.list_snapshots()
