"""Offline translation/selection tests and opt-in isolated live MariaDB parity."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import os
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace
import uuid

import pytest

from dashboard.accounts import Accounts, AccountError, ATTEMPT_WINDOW, IDLE_SECONDS
from dashboard.maria_storage import (Connection, MariaStore, Row, configured, migrate_sqlite,
                                      translate, _decode)
from dashboard.storage import Store, StorageError, open_store

PASSWORD = 'correct horse battery staple'


def test_explicit_sqlite_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv('MONEYGRAPH_DB', str(tmp_path / 'test.db'))
    monkeypatch.setenv('MYSQL_PASSWORD', 'not-a-real-password')
    assert open_store(tmp_path).backend == 'sqlite'


def test_sqlite_storage_failure_is_safe(tmp_path, monkeypatch):
    monkeypatch.setenv('MONEYGRAPH_DB', str(tmp_path))
    with pytest.raises(StorageError):
        open_store(tmp_path)
    malformed = tmp_path / 'malformed.db'
    malformed.write_bytes(b'not a SQLite database')
    with pytest.raises(StorageError):
        Store(malformed)


def test_configured_database_never_silently_falls_back(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith('MYSQL_') or key == 'MONEYGRAPH_DB':
            monkeypatch.delenv(key)
    assert open_store(tmp_path).backend == 'sqlite'
    (tmp_path / '.env').write_text('MYSQL_DATABASE=moneygraph\n')
    with pytest.raises(StorageError, match='MYSQL_PASSWORD'):
        open_store(tmp_path)
    (tmp_path / '.env').write_text('MYSQL_PASSWORD="secret value" # ignored\n')
    monkeypatch.setenv('MYSQL_HOST', 'localhost')
    assert configured(tmp_path / '.env') == {'MYSQL_PASSWORD': 'secret value', 'MYSQL_HOST': 'localhost'}


def test_sql_translation_keeps_values_and_identifiers_separate():
    attack = "cases'; DROP TABLE users; --\n\t\\"
    sql, params = translate('INSERT INTO cases(note) VALUES(?) ON CONFLICT(id) DO UPDATE SET note=excluded.note', 'mg_test_', (attack,))
    assert attack not in sql and 'DROP' not in sql
    assert 'INSERT INTO mg_test_cases' in sql
    assert 'ON DUPLICATE KEY UPDATE note=VALUES(note)' in sql and params == ()
    assert attack.encode().hex() in sql
    sql, _ = translate("SELECT 'users?' FROM users WHERE email=?", 'mg_test_', ('null@example.test',))
    assert "'users?' FROM mg_test_users" in sql
    with pytest.raises(ValueError):
        translate('SELECT ?', 'mg_test_', ())
    with pytest.raises(ValueError):
        translate('SELECT 1', 'mg_test_', (None,))


def test_batch_values_preserve_control_characters_and_large_gid():
    assert _decode(r'a\tb\nc\\d', 'note') == 'a\tb\nc\\d'
    assert _decode('NULL', 'note') == 'NULL'
    assert _decode('9223372036854775807', 'gid') == '9223372036854775807'
    assert _decode('0x00ff', 'content') == b'\0\xff'
    row = Row([('id', 4), ('name', 'Tester')])
    assert row[0] == row['id'] == 4


def test_stalled_client_write_has_deadline_and_poisoned_connection(monkeypatch):
    read_fd, write_fd = os.pipe()
    connection = Connection.__new__(Connection)
    started = time.monotonic()
    try:
        with os.fdopen(write_fd, 'wb', buffering=0) as writer:
            os.set_blocking(writer.fileno(), False)
            connection.process = SimpleNamespace(stdin=writer)
            with pytest.raises(StorageError, match='вовремя'):
                connection._write(b'x' * 1_000_000, started + 0.1)
            assert time.monotonic() - started < 1
    finally:
        os.close(read_fd)
    terminated = []
    connection.process = SimpleNamespace(poll=lambda: None, terminate=lambda: terminated.append(True))
    connection.broken = False
    def stalled(*args):
        raise StorageError('Timeout')
    monkeypatch.setattr(connection, '_write', stalled)
    with pytest.raises(StorageError):
        connection.raw('SELECT 1')
    assert connection.broken and terminated == [True]
    with pytest.raises(StorageError):
        connection.raw('ROLLBACK')
    assert terminated == [True]


def test_migration_rejects_sqlite_column_injection_before_target_access(tmp_path):
    source = tmp_path / 'crafted.db'
    local = Store(source)
    with local.connect() as db:
        db.execute('ALTER TABLE datasets ADD COLUMN "x) VALUES (1,2,3); SELECT 424242; --" TEXT')
    reached = []
    target = SimpleNamespace(connect=lambda: reached.append(True))
    with pytest.raises(StorageError, match='схема SQLite'):
        migrate_sqlite(source, target)
    assert reached == []


@pytest.fixture
def maria():
    if os.environ.get('MONEYGRAPH_TEST_MARIADB') != '1':
        pytest.skip('Set MONEYGRAPH_TEST_MARIADB=1 for isolated live MariaDB tests')
    config = configured(Path(__file__).resolve().parents[2] / '.env')
    if not config:
        pytest.fail('MariaDB tests requested without MYSQL_* configuration')
    prefix = 'mg_test_ws_' + uuid.uuid4().hex[:12] + '_'
    store = MariaStore(config, prefix=prefix)
    try:
        yield store
    finally:
        from dashboard.maria_storage import TABLES
        with store.connect() as db:
            for table in reversed(TABLES):
                db.raw(f'DROP TABLE {prefix}{table}')


def test_live_account_parity_revocation_rate_limits(maria):
    now = [1000.0]
    accounts = Accounts(maria, clock=lambda: now[0])
    recovery = accounts.register('Первый', 'FIRST@example.test', PASSWORD)
    with pytest.raises(AccountError):
        accounts.register('Duplicate', 'first@example.test', PASSWORD)
    token = accounts.login('first@example.test', PASSWORD)
    remembered = accounts.login('first@example.test', PASSWORD, remember=True)
    assert accounts.identity(token)['name'] == 'Первый'
    now[0] += IDLE_SECONDS + 1
    assert accounts.identity(token) is None and accounts.identity(remembered)
    for _ in range(5):
        with pytest.raises(AccountError, match='Неверный'):
            accounts.login('first@example.test', 'wrong')
    with pytest.raises(AccountError, match='Слишком много'):
        accounts.login('first@example.test', PASSWORD)
    now[0] += ATTEMPT_WINDOW + 1
    new_recovery = accounts.reset_password('first@example.test', recovery, PASSWORD + ' new')
    assert new_recovery != recovery and accounts.identity(remembered) is None
    with pytest.raises(AccountError):
        accounts.reset_password('first@example.test', recovery, PASSWORD)
    token = accounts.login('first@example.test', PASSWORD + ' new')
    accounts.change_password(token, PASSWORD + ' new', PASSWORD)
    assert accounts.identity(token) is None
    token = accounts.login('first@example.test', PASSWORD)
    other = accounts.login('first@example.test', PASSWORD)
    accounts.logout(token, all_sessions=True)
    assert accounts.identity(token) is None and accounts.identity(other) is None


def test_live_notes_isolation_concurrent_snapshots_and_cache(maria):
    from dashboard.ai.engine import ResultCache
    user = maria.user('test', 'first', 'first@test', 'First')
    other = maria.user('test', 'second', 'second@test', 'Second')
    assert user == maria.user('test', 'first', 'updated@test', 'First')
    content = b'gid\n9223372036854775807\n'
    with ThreadPoolExecutor(max_workers=4) as workers:
        datasets = list(workers.map(lambda _: maria.snapshot({'nodes.csv': content}), range(8)))
    assert len(set(datasets)) == 1
    dataset = datasets[0]
    note = "NULL\n\t'; DROP TABLE users; --\\"
    maria.save_case(user, dataset, '9223372036854775807', 'В работе', note, 'facts')
    assert maria.case(user, dataset, '9223372036854775807')['note'] == note
    assert maria.cases(other) == [] and maria.history(other) == []
    maria.save_case(user, dataset, '9223372036854775807', 'Проверено', 'Updated', 'facts 2')
    assert len(maria.cases(user)) == 1 and len(maria.history(user)) == 2
    with maria.connect() as db:
        assert db.execute('SELECT content FROM assets WHERE dataset_id=?', (dataset,)).fetchone()[0] == content
    with pytest.raises(sqlite3.IntegrityError):
        maria.save_case(999999, dataset, '123', 'В работе', '', '')
    cache = ResultCache(maria, user)
    for i in range(12):
        cache.put(str(i), {'ok': True, 'i': i})
    assert cache.get('11')['i'] == 11 and cache.get('0') is None
    assert ResultCache(maria, other).get('11') is None
    with maria.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM ai_results WHERE user_id=?', (user,)).fetchone()[0] == 10


def test_live_atomic_idempotent_migration(tmp_path, maria):
    source = tmp_path / 'original.db'
    local = Store(source)
    accounts = Accounts(local)
    accounts.register('Saved user', 'saved@example.test', PASSWORD)
    token = accounts.login('saved@example.test', PASSWORD, remember=True)
    user = accounts.identity(token)['id']
    dataset = local.snapshot({'nodes.csv': b'gid\n123\n'})
    local.save_case(user, dataset, '123', 'В работе', 'Existing note', 'Existing report')
    before = sha256(source.read_bytes()).hexdigest()
    result = migrate_sqlite(source, maria)
    assert result['users'] == result['cases'] == 1 and not result['already_migrated']
    assert migrate_sqlite(source, maria)['already_migrated']
    assert sha256(source.read_bytes()).hexdigest() == before
    imported = Accounts(maria)
    imported_user = imported.identity(token)['id']
    assert maria.cases(imported_user)[0]['note'] == 'Existing note'
    assert imported.identity(imported.login('saved@example.test', PASSWORD))
    conflict = tmp_path / 'conflict.db'
    other = Accounts(Store(conflict))
    other.register('Must roll back', 'first-added@example.test', PASSWORD)
    other.register('Collision', 'saved@example.test', PASSWORD)
    with pytest.raises(StorageError, match='уже есть'):
        migrate_sqlite(conflict, maria)
    with maria.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 1


def test_live_concurrent_recovery_used_once(maria):
    accounts = Accounts(maria)
    recovery = accounts.register('Test', 'test@example.test', PASSWORD)
    def reset(_):
        try:
            accounts.reset_password('test@example.test', recovery, PASSWORD + ' changed')
            return True
        except AccountError:
            return False
    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sum(workers.map(reset, range(2))) == 1
    def wrong_login(_):
        try:
            accounts.login('test@example.test', 'incorrect')
        except AccountError as exc:
            return 'Слишком много' in str(exc)
        pytest.fail('Invalid password accepted')
    with ThreadPoolExecutor(max_workers=6) as workers:
        assert sum(workers.map(wrong_login, range(6))) == 1


def test_live_streamlit_mariadb_analytics_and_private_notes(maria, monkeypatch):
    st = pytest.importorskip('streamlit')
    pytest.importorskip('plotly')
    from streamlit.testing.v1 import AppTest
    from dashboard.data import ROOT
    monkeypatch.setattr('dashboard.storage.open_store', lambda *args, **kwargs: maria)
    monkeypatch.setenv('MONEYGRAPH_DATA_SOURCE', 'mariadb')
    monkeypatch.setattr(st, 'secrets', {})
    accounts = Accounts(maria)
    accounts.register('MariaDB analyst', 'maria-app@example.test', PASSWORD)
    token = accounts.login('maria-app@example.test', PASSWORD)
    app = AppTest.from_file(str(ROOT / 'dashboard/app.py'), default_timeout=30)
    app.session_state['_auth_token'] = token
    st.cache_data.clear()
    app.run()
    assert not app.exception and not app.error
    assert app.selectbox(key='data_source').value == 'MariaDB'
    assert [metric.value for metric in app.metric][:3] == ['2 248', '3 119', '4 840']
    app.sidebar.radio(key='section').set_value('Узел и переводы').run()
    assert not app.exception and not app.error
    app.text_area[0].set_value('Сохранено в MariaDB после проверки цепочки')
    next(button for button in app.button if button.label == 'Сохранить в мои проверки').click().run()
    assert not app.exception and app.success
    user = accounts.identity(token)['id']
    assert maria.cases(user)[0]['note'] == 'Сохранено в MariaDB после проверки цепочки'
    fresh = AppTest.from_file(str(ROOT / 'dashboard/app.py'), default_timeout=30)
    fresh.session_state['_auth_token'] = token
    fresh.session_state['section'] = 'Мои проверки'
    fresh.run()
    assert not fresh.exception and not fresh.error
    assert any('Сохранено в MariaDB после проверки цепочки' in element.value for element in fresh.text)
    fresh.button(key='logout').click().run()
    assert not fresh.exception and accounts.identity(token) is None
