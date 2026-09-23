"""Optional, versioned MariaDB analytical snapshots via the installed CLI.

No third-party driver and no server-side file imports. DDL is additive; each
snapshot's DML is atomic. UI accounts remain in their existing SQLite store.
"""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
from decimal import Decimal

from .export import REQUIRED, validate_outputs
from .load import validate

VERSION = '1'
TABLES = ('nodes', 'edges', 'transactions', 'clusters', 'nodes_roles', 'top_nodes')


def settings(env_file='.env'):
    config = {}
    path = Path(env_file)
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition('=')
            key = key.strip().removeprefix('export ')
            if sep and key.startswith('MYSQL_'):
                parts = shlex.split(value, comments=True)
                if len(parts) > 1:
                    raise ValueError(f'Quote the value of {key}')
                config[key] = parts[0] if parts else ''
    config.update({k: v for k, v in os.environ.items() if k.startswith('MYSQL_')})
    if not config.get('MYSQL_PASSWORD'):
        raise ValueError('Set MYSQL_PASSWORD in .env or environment')
    return config


def literal(value):
    """UTF-8 hex literals keep data completely separate from SQL syntax."""
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, (int, float, Decimal)):
        if not Decimal(str(value)).is_finite():
            raise ValueError('Nonfinite database value')
        return str(value)
    return "CONVERT(X'" + str(value).encode('utf-8').hex() + "' USING utf8mb4)"


def schema():
    run = 'run_id CHAR(64) CHARACTER SET ascii NOT NULL'
    fk_run = 'FOREIGN KEY (run_id) REFERENCES mg_runs(run_id)'
    fk_node = lambda col: f'FOREIGN KEY (run_id,{col}) REFERENCES mg_nodes(run_id,gid)'
    specs = {
        'runs': 'run_id CHAR(64) CHARACTER SET ascii PRIMARY KEY, schema_version INT NOT NULL, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, manifest JSON NOT NULL',
        'nodes': f'{run}, gid BIGINT NOT NULL, depth TINYINT NOT NULL, is_seed BOOLEAN NOT NULL, PRIMARY KEY(run_id,gid), {fk_run}',
        'edges': f'{run}, src BIGINT NOT NULL, dst BIGINT NOT NULL, sum_kzt DECIMAL(24,2) NOT NULL CHECK(sum_kzt>=0), n_tx BIGINT NOT NULL, depth TINYINT NOT NULL, PRIMARY KEY(run_id,src,dst), INDEX(run_id,dst), {fk_node("src")}, {fk_node("dst")}',
        'transactions': f'{run}, tx_index BIGINT NOT NULL, src BIGINT NOT NULL, dst BIGINT NOT NULL, date DATE NOT NULL, sum_kzt DECIMAL(24,2) NOT NULL CHECK(sum_kzt>=0), PRIMARY KEY(run_id,tx_index), INDEX(run_id,src,date), INDEX(run_id,dst,date), FOREIGN KEY(run_id,src,dst) REFERENCES mg_edges(run_id,src,dst)',
        'clusters': f'{run}, cluster_id BIGINT NOT NULL, n_nodes INT NOT NULL, n_seed INT NOT NULL, sum_kzt_internal DECIMAL(24,2) NOT NULL, top_gids TEXT NOT NULL, hypothesis TEXT NOT NULL, PRIMARY KEY(run_id,cluster_id), {fk_run}',
    }
    role_types = {'gid': 'BIGINT', 'role': 'VARCHAR(24)', 'role_score': 'DOUBLE', 'cluster_id': 'BIGINT', 'priority_score': 'DOUBLE', 'evidence': 'VARCHAR(200)', 'in_deg': 'INT', 'out_deg': 'INT', 'in_kzt': 'DECIMAL(24,2)', 'out_kzt': 'DECIMAL(24,2)', 'pagerank': 'DOUBLE', 'pass_through': 'DOUBLE', 'depth': 'TINYINT', 'is_seed': 'BOOLEAN', 'truncated_by_depth': 'BOOLEAN'}
    columns = ', '.join(f'`{k}` {v} NOT NULL' for k, v in role_types.items())
    specs['nodes_roles'] = f'{run}, {columns}, metrics JSON NOT NULL, PRIMARY KEY(run_id,gid), INDEX(run_id,priority_score,gid), {fk_node("gid")}, FOREIGN KEY(run_id,cluster_id) REFERENCES mg_clusters(run_id,cluster_id), CHECK(role_score BETWEEN 0 AND 1), CHECK(priority_score BETWEEN 0 AND 1)'
    specs['top_nodes'] = f'{run}, `rank` INT NOT NULL, gid BIGINT NOT NULL, role VARCHAR(24) NOT NULL, priority_score DOUBLE NOT NULL, why VARCHAR(200) NOT NULL, PRIMARY KEY(run_id,`rank`), UNIQUE(run_id,gid), FOREIGN KEY(run_id,gid) REFERENCES mg_nodes_roles(run_id,gid)'
    return '\n'.join(f'CREATE TABLE IF NOT EXISTS mg_{name} ({cols}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;' for name, cols in specs.items())


def prepare(edges, nodes, tx, frames):
    validate(edges, nodes, tx)
    validate_outputs(*frames)
    if set(nodes.gid) != set(frames[0].gid):
        raise ValueError('Source and output nodes differ')
    data = dict(zip(TABLES, (nodes, edges, tx, frames[1], frames[0], frames[2])))
    manifest = {name: {'rows': len(df), 'sha256': hashlib.sha256(df.to_csv(index=False, float_format='%.17g').encode()).hexdigest()} for name, df in data.items()}
    encoded = json.dumps({'schema_version': VERSION, 'tables': manifest}, sort_keys=True)
    run_id = hashlib.sha256(encoded.encode()).hexdigest()
    statements = ["SET SESSION sql_mode='STRICT_ALL_TABLES,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';", schema(), 'START TRANSACTION;',
                  f'INSERT INTO mg_runs(run_id,schema_version,manifest) VALUES({literal(run_id)},1,{literal(encoded)}) ON DUPLICATE KEY UPDATE run_id=VALUES(run_id);']
    for name, df in data.items():
        records = df.to_dict('records')  # Avoid iterrows: it coerces large integer gids to floats.
        if name == 'nodes_roles':
            records = [{**{key: r[key] for key in REQUIRED}, 'metrics': json.dumps({k: v for k, v in r.items() if k not in REQUIRED}, allow_nan=False)} for r in records]
        if name == 'transactions':
            records = [{**r, 'date': r['date'].date().isoformat(), 'tx_index': i} for i, r in enumerate(records)]
        columns = list(records[0]) if records else list(df.columns)
        if any(not col.replace('_', '').isalnum() for col in columns):
            raise ValueError('Invalid column name')
        for offset in range(0, len(records), 250):
            rows = records[offset:offset+250]
            values = ','.join('(' + ','.join(literal(v) for v in [run_id, *(r[c] for c in columns)]) + ')' for r in rows)
            statements.append(f'INSERT INTO mg_{name} (`run_id`,' + ','.join(f'`{c}`' for c in columns) + f') VALUES {values} ON DUPLICATE KEY UPDATE run_id=VALUES(run_id);')
    # Counts and amounts are checked in the same transaction by a CHECK constraint.
    statements.append('CREATE TEMPORARY TABLE mg_verify (ok INT CHECK(ok=1));')
    for name, df in data.items():
        statements.append(f'INSERT INTO mg_verify SELECT COUNT(*)={len(df)} FROM mg_{name} WHERE run_id={literal(run_id)};')
    amount = sum((Decimal(str(v)).quantize(Decimal('.01')) for v in tx.sum_kzt), Decimal(0))
    statements.append(f'INSERT INTO mg_verify SELECT ABS(COALESCE(SUM(sum_kzt),0)-{amount})<=0.01 FROM mg_transactions WHERE run_id={literal(run_id)};')
    statements += ['COMMIT;', f'SELECT {literal(run_id)};']
    return run_id, manifest, '\n'.join(statements)


def execute(sql, config):
    client = shutil.which('mariadb')
    if not client:
        raise RuntimeError('Install the mariadb command-line client')
    def quoted(value):
        return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r') + '"'
    options = {'user': config.get('MYSQL_USER', 'moneygraph'), 'password': config['MYSQL_PASSWORD'], 'database': config.get('MYSQL_DATABASE', 'moneygraph'), 'host': config.get('MYSQL_HOST', 'localhost'), 'port': config.get('MYSQL_PORT', '3306')}
    if config.get('MYSQL_SOCKET'):
        options['socket'] = config['MYSQL_SOCKET']
    with tempfile.TemporaryDirectory(prefix='moneygraph-db-') as directory:
        path = Path(directory) / 'client.cnf'
        with open(path, 'x', opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
            stream.write('[client]\n' + '\n'.join(f'{k}={quoted(v)}' for k, v in options.items()))
        result = subprocess.run([client, f'--defaults-file={path}', '--batch', '--skip-column-names', '--default-character-set=utf8mb4', '--connect-timeout=10'], input=sql, text=True, capture_output=True, timeout=120)
    if result.returncode:
        # Do not echo SQL, records or credentials from client diagnostics.
        import re
        code = re.search(r'ERROR (\d+)', result.stderr)
        raise RuntimeError('MariaDB import failed' + (f' (error {code[1]})' if code else '') + '; snapshot transaction rolled back')
    return result.stdout.strip()


def publish(edges, nodes, tx, frames):
    run_id, manifest, sql = prepare(edges, nodes, tx, frames)
    output = execute(sql, settings())
    if output != run_id:
        raise RuntimeError('MariaDB did not confirm the snapshot')
    return {'run_id': run_id, 'rows': {name: meta['rows'] for name, meta in manifest.items()}}
