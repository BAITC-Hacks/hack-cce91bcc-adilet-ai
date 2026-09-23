"""SQL export safety and determinism; live checks require an accessible MariaDB."""
import os
from pathlib import Path
import subprocess

import pandas as pd
import pytest

from src.database import execute, literal, prepare, settings
from src.load import load


def test_literals_preserve_ids_and_escape_sql():
    assert literal(9223372036854775807) == '9223372036854775807'
    encoded = literal("'); DROP TABLE users; --\\\nКазахстан")
    assert 'DROP TABLE' not in encoded
    assert 'Казахстан'.encode().hex() in encoded
    with pytest.raises(ValueError):
        literal(float('nan'))


def test_settings_never_execute_shell(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith('MYSQL_'):
            monkeypatch.delenv(key)
    env = tmp_path / '.env'
    env.write_text("AI_API_KEY=untouched\nMYSQL_PASSWORD='$(touch bad)'\nMYSQL_USER=moneygraph\n")
    assert settings(env)['MYSQL_PASSWORD'] == '$(touch bad)'
    monkeypatch.setenv('MYSQL_PASSWORD', 'override')
    assert settings(env)['MYSQL_PASSWORD'] == 'override'


def test_client_credentials_private_and_error_redacted(monkeypatch):
    monkeypatch.setattr('shutil.which', lambda _: '/usr/bin/mariadb')
    paths = []
    def run(argv, **kwargs):
        assert 'secret' not in repr(argv)
        path = Path(argv[1].split('=', 1)[1])
        paths.append(path)
        assert path.stat().st_mode & 0o777 == 0o600
        assert 'password="secret"' in path.read_text()
        assert kwargs['input'] == 'SELECT 1;'
        return subprocess.CompletedProcess(argv, 1, '', 'ERROR 1045 secret private SQL')
    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(RuntimeError, match='1045') as exc:
        execute('SELECT 1;', {'MYSQL_PASSWORD': 'secret'})
    assert 'secret' not in str(exc.value)
    assert not paths[0].exists()


def test_real_snapshot_deterministic_and_all_rows_present():
    edges, nodes, tx = load('data')
    frames = tuple(pd.read_csv(f'out/{name}.csv', float_precision='round_trip') for name in ('nodes_roles', 'clusters', 'top_nodes'))
    first = prepare(edges, nodes, tx, frames)
    assert prepare(edges, nodes, tx, frames) == first
    _, manifest, sql = first
    assert {k: v['rows'] for k, v in manifest.items()} == {'nodes': 2248, 'edges': 3119, 'transactions': 4840, 'nodes_roles': 2248, 'clusters': 91, 'top_nodes': 20}
    assert sql.index('START TRANSACTION') < sql.index('INSERT INTO mg_runs') < sql.index('COMMIT;')
    assert 'INSERT IGNORE' not in sql
    assert 'DROP TABLE' not in sql
    assert 'FOREIGN KEY(run_id,src,dst)' in sql
    assert 'COUNT(*)=4840' in sql
    changed = [df.copy() for df in frames]
    changed[1].loc[0, 'hypothesis'] += ' Дополнение.'
    assert prepare(edges, nodes, tx, changed)[0] != first[0]
