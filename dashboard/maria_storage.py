"""MariaDB workspace using the already installed MariaDB command-line client.

One client connection stays open for each transaction. Values use UTF-8/bytes
hex literals, never SQL interpolation. This small adapter supports only the
SQLite SQL forms used by Store, Accounts and ResultCache, not arbitrary SQL.
The analytics mg_* tables are independent from mg_workspace_*.
"""
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import sqlite3
import subprocess
import tempfile
import time

from dashboard.storage import Store, StorageError

TABLES = ('users', 'datasets', 'assets', 'cases', 'events', 'credentials',
          'sessions', 'auth_limits', 'ai_results', 'migrations')
DEFAULT_PREFIX = 'mg_workspace_'
MIGRATION_COLUMNS = {
    'users': ('id', 'issuer', 'subject', 'email', 'name', 'created_at'),
    'datasets': ('id', 'created_at'),
    'assets': ('dataset_id', 'name', 'digest', 'content'),
    'cases': ('id', 'user_id', 'dataset_id', 'gid', 'status', 'note', 'report', 'updated_at'),
    'events': ('id', 'user_id', 'action', 'details', 'created_at'),
    'credentials': ('user_id', 'password_hash', 'recovery_hash'),
    'sessions': ('token_hash', 'user_id', 'expires_at', 'last_seen', 'remembered'),
    'auth_limits': ('action', 'identity', 'failures', 'window_start'),
    'ai_results': ('user_id', 'cache_key', 'created', 'payload'),
}


def configured(env_file):
    """Return MYSQL config if explicitly configured, otherwise None."""
    config = {}
    path = Path(env_file)
    try:
        if path.exists():
            for line in path.read_text().splitlines():
                key, sep, value = line.partition('=')
                key = key.strip().removeprefix('export ')
                if sep and key.startswith('MYSQL_'):
                    parts = shlex.split(value, comments=True)
                    if len(parts) > 1:
                        raise ValueError('Unquoted setting')
                    config[key] = parts[0] if parts else ''
        config.update({k: v for k, v in os.environ.items() if k.startswith('MYSQL_')})
    except (OSError, ValueError):
        raise StorageError('Не удалось прочитать настройки MariaDB. Проверьте MYSQL_* в .env.') from None
    if not config:
        return None
    if not config.get('MYSQL_PASSWORD'):
        raise StorageError('Для MariaDB задайте MYSQL_PASSWORD в .env или окружении.')
    return config


def schema(prefix):
    user = f'REFERENCES {prefix}users(id)'
    dataset = f'REFERENCES {prefix}datasets(id)'
    specs = {
        'users': f'''id BIGINT PRIMARY KEY AUTO_INCREMENT, issuer VARCHAR(191) NOT NULL,
            subject VARCHAR(254) NOT NULL, email VARCHAR(254) NOT NULL, name VARCHAR(255) NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(issuer,subject)''',
        'datasets': 'id CHAR(64) PRIMARY KEY, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP',
        'assets': f'''dataset_id CHAR(64) NOT NULL, name VARCHAR(255) NOT NULL, digest CHAR(64) NOT NULL,
            content LONGBLOB NOT NULL, PRIMARY KEY(dataset_id,name), FOREIGN KEY(dataset_id) {dataset}''',
        'cases': f'''id BIGINT PRIMARY KEY AUTO_INCREMENT, user_id BIGINT NOT NULL, dataset_id CHAR(64) NOT NULL,
            gid VARCHAR(32) NOT NULL, status VARCHAR(32) NOT NULL CHECK(status IN ('В работе','Проверено','Отложено')),
            note TEXT NOT NULL, report LONGTEXT NOT NULL, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id,dataset_id,gid), FOREIGN KEY(user_id) {user}, FOREIGN KEY(dataset_id) {dataset}''',
        'events': f'''id BIGINT PRIMARY KEY AUTO_INCREMENT, user_id BIGINT NOT NULL, action VARCHAR(255) NOT NULL,
            details LONGTEXT NOT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            INDEX(user_id,id), FOREIGN KEY(user_id) {user}''',
        'credentials': f'''user_id BIGINT PRIMARY KEY, password_hash VARCHAR(255) NOT NULL,
            recovery_hash CHAR(64) NOT NULL, FOREIGN KEY(user_id) {user}''',
        'sessions': f'''token_hash CHAR(64) PRIMARY KEY, user_id BIGINT NOT NULL,
            expires_at DOUBLE NOT NULL, last_seen DOUBLE NOT NULL, remembered BOOLEAN NOT NULL DEFAULT 0,
            INDEX(user_id), FOREIGN KEY(user_id) {user}''',
        'auth_limits': '''action VARCHAR(32) NOT NULL, identity VARCHAR(254) NOT NULL,
            failures INT NOT NULL, window_start DOUBLE NOT NULL, PRIMARY KEY(action,identity)''',
        'ai_results': f'''user_id BIGINT NOT NULL, cache_key CHAR(64) NOT NULL,
            created DOUBLE NOT NULL, payload LONGTEXT NOT NULL,
            PRIMARY KEY(user_id,cache_key), FOREIGN KEY(user_id) {user}''',
        'migrations': '''source_id CHAR(64) PRIMARY KEY, summary TEXT NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP''',
    }
    return {name: f'CREATE TABLE IF NOT EXISTS {prefix}{name} ({columns}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin'
            for name, columns in specs.items()}


class Row(dict):
    """Mapping with SQLite's optional positional column access."""
    def __getitem__(self, key):
        return tuple(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class Result:
    def __init__(self, rows=(), lastrowid=0, rowcount=0):
        self.rows = iter(rows)
        self.lastrowid, self.rowcount = lastrowid, rowcount

    def fetchone(self):
        return next(self.rows, None)

    def fetchall(self):
        return list(self.rows)

    def __iter__(self):
        return self.rows


def literal(value):
    if value is None:
        return 'NULL'
    if isinstance(value, bytes):
        return "X'" + value.hex() + "'"
    from src.database import literal as safe_literal
    return safe_literal(value)


def translate(sql, prefix, params):
    """Translate the finite set of SQL forms in the workspace modules."""
    if re.match(r'\s*CREATE TABLE IF NOT EXISTS ai_results\b', sql, re.I):
        return schema(prefix)['ai_results'], ()
    if re.match(r'\s*DELETE FROM ai_results WHERE rowid IN', sql, re.I):
        sql = '''DELETE FROM ai_results WHERE user_id=? AND cache_key NOT IN
            (SELECT cache_key FROM (SELECT cache_key FROM ai_results WHERE user_id=?
             ORDER BY created DESC,cache_key DESC LIMIT 10) AS latest)'''
        params = (params[0], params[0])
    sql = re.sub(r'INSERT OR REPLACE INTO', 'REPLACE INTO', sql, flags=re.I)
    sql = re.sub(r'ON CONFLICT\s*\([^)]*\)\s*DO UPDATE SET', 'ON DUPLICATE KEY UPDATE', sql, flags=re.I)
    sql = re.sub(r'excluded\.(\w+)', r'VALUES(\1)', sql, flags=re.I)
    # Quoted SQL strings are left untouched; parameters never enter this step.
    chunks = re.split(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\")", sql)
    values = iter(params)
    for index in range(0, len(chunks), 2):
        part = re.sub(r'\b(' + '|'.join(TABLES) + r')\b', lambda m: prefix + m[0], chunks[index])
        try:
            part = re.sub(r'\?', lambda _: literal(next(values)), part)
        except StopIteration:
            raise ValueError('Incorrect SQL parameter count') from None
        chunks[index] = part
    remaining = object()
    if next(values, remaining) is not remaining:
        raise ValueError('Incorrect SQL parameter count')
    return ''.join(chunks), ()


def _decode(value, name):
    if value == 'NULL' and name == 'acquired':
        return None
    # MariaDB batch mode escapes control characters and backslashes.
    value = re.sub(r'\\([0nrtbZ\\])', lambda m: {'0': '\0', 'n': '\n', 'r': '\r', 't': '\t',
        'b': '\b', 'Z': '\x1a', '\\': '\\'}[m[1]], value)
    if name == 'content' and value.startswith('0x'):
        return bytes.fromhex(value[2:])
    if name in {'id', 'user_id', 'failures', 'remembered'} or name.startswith(('COUNT(', 'LAST_INSERT_ID(', 'ROW_COUNT(')):
        return int(value)
    if name in {'expires_at', 'last_seen', 'window_start', 'created'}:
        return float(value)
    return value


class Connection:
    """Synchronous batch protocol with bounded reads and a private option file."""
    def __init__(self, config, prefix):
        self.prefix = prefix
        client = shutil.which('mariadb')
        if not client:
            raise StorageError('Для подключения установите клиент mariadb.')
        self.directory = tempfile.TemporaryDirectory(prefix='moneygraph-workspace-')
        self.errors = tempfile.TemporaryFile()
        self.error_offset = 0
        self.buffer = b''
        self.process = None
        self.broken = False
        try:
            options = {'user': config.get('MYSQL_USER', 'moneygraph'), 'password': config['MYSQL_PASSWORD'],
                'database': config.get('MYSQL_DATABASE', 'moneygraph'), 'host': config.get('MYSQL_HOST', 'localhost'),
                'port': config.get('MYSQL_PORT', '3306')}
            if config.get('MYSQL_SOCKET'):
                options['socket'] = config['MYSQL_SOCKET']
            def quote(value):
                return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r') + '"'
            path = Path(self.directory.name) / 'client.cnf'
            with open(path, 'x', opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
                stream.write('[client]\n' + '\n'.join(f'{k}={quote(v)}' for k, v in options.items()))
            self.process = subprocess.Popen([client, f'--defaults-file={path}', '--batch', '--force', '--unbuffered',
                '--binary-as-hex', '--default-character-set=utf8mb4', '--connect-timeout=5'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errors, bufsize=0)
            os.set_blocking(self.process.stdin.fileno(), False)
            self.lock_name = 'mg_workspace_' + sha256((str(options['database']) + prefix).encode()).hexdigest()[:48]
            self.locked = False
            self.raw("SET SESSION sql_mode='STRICT_ALL_TABLES,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION'")
            self.raw('SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED')
            self.raw('START TRANSACTION')
        except Exception:
            self.close()
            raise StorageError('MariaDB недоступна. Проверьте запуск сервера и настройки MYSQL_*.') from None

    def _line(self, deadline):
        while b'\n' not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StorageError('MariaDB не ответила вовремя. Повторите действие.')
            with selectors.DefaultSelector() as selector:
                selector.register(self.process.stdout, selectors.EVENT_READ)
                if not selector.select(remaining):
                    raise StorageError('MariaDB не ответила вовремя. Повторите действие.')
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise StorageError('Соединение с MariaDB прервано. Повторите действие.')
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b'\n', 1)
        return line.decode('utf-8')

    def _write(self, content, deadline):
        """Bound writes too: a stalled server can back up the client's stdin."""
        view = memoryview(content)
        while view:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StorageError('MariaDB не ответила вовремя. Повторите действие.')
            try:
                written = os.write(self.process.stdin.fileno(), view[:65536])
                view = view[written:]
            except BlockingIOError:
                with selectors.DefaultSelector() as selector:
                    selector.register(self.process.stdin, selectors.EVENT_WRITE)
                    if not selector.select(remaining):
                        raise StorageError('MariaDB не ответила вовремя. Повторите действие.')

    def _poison(self):
        self.broken = True
        if self.process.poll() is None:
            self.process.terminate()

    def raw(self, sql):
        if self.broken:
            raise StorageError('Соединение с MariaDB прервано. Повторите действие.')
        marker = 'mg_end_' + os.urandom(12).hex()
        command = sql.rstrip().rstrip(';') + f';\nSELECT ROW_COUNT() AS {marker}, LAST_INSERT_ID() AS insert_id;\n'
        try:
            deadline = time.monotonic() + 20
            self._write(command.encode('utf-8'), deadline)
            lines = []
            while True:
                line = self._line(deadline)
                if line == marker + '\tinsert_id':
                    rowcount, lastrowid = (int(v) for v in self._line(deadline).split('\t'))
                    break
                lines.append(line)
            self.errors.seek(self.error_offset)
            error = self.errors.read()
            self.error_offset = self.errors.tell()
            if error:
                code = re.search(rb'ERROR (\d+)', error)
                if code and int(code[1]) in (1062, 1451, 1452, 4025, 1048):
                    raise sqlite3.IntegrityError('MariaDB constraint rejected the change')
                raise StorageError('MariaDB не выполнила операцию. Изменения отменены.')
            rows = []
            if lines:
                names = lines[0].split('\t')
                for line in lines[1:]:
                    fields = line.split('\t')
                    if len(fields) != len(names):
                        raise StorageError('Получен некорректный ответ MariaDB.')
                    rows.append(Row((n, _decode(v, n)) for n, v in zip(names, fields)))
            return Result(rows, lastrowid, rowcount)
        except StorageError:
            self._poison()
            raise
        except (OSError, UnicodeError, ValueError):
            self._poison()
            raise StorageError('Соединение с MariaDB прервано. Повторите действие.') from None

    def execute(self, sql, params=()):
        if sql.strip().upper() == 'BEGIN IMMEDIATE':
            if not self.locked:
                result = self.raw(f'SELECT GET_LOCK({literal(self.lock_name)},10) AS acquired').fetchone()
                if not result or result['acquired'] != '1':
                    raise StorageError('Хранилище занято. Повторите действие.')
                self.locked = True
            return Result()
        sql, _ = translate(sql, self.prefix, params)
        return self.raw(sql)

    def executemany(self, sql, records):
        for record in records:
            self.execute(sql, record)

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
            self.process.stdin.close()
            self.process.stdout.close()
        self.errors.close()
        self.directory.cleanup()


class MariaStore(Store):
    backend = 'mariadb'

    def __init__(self, config, prefix=DEFAULT_PREFIX):
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,40}_', prefix):
            raise ValueError('Invalid workspace table prefix')
        self.config, self.prefix = dict(config), prefix
        with self.connect() as db:
            for statement in schema(prefix).values():
                db.raw(statement)

    @contextmanager
    def connect(self):
        db = Connection(self.config, self.prefix)
        try:
            yield db
            db.raw('COMMIT')
        except BaseException:
            try:
                if not db.broken:
                    db.raw('ROLLBACK')
            except Exception:
                pass
            raise
        finally:
            db.close()  # Closing the connection also releases GET_LOCK.

    def snapshot(self, files):
        # An upsert makes simultaneous first visits to the same snapshot safe.
        assets = [(name, sha256(content).hexdigest(), content) for name, content in sorted(files.items())]
        dataset_id = sha256(json.dumps([(n, h) for n, h, _ in assets]).encode()).hexdigest()
        with self.connect() as db:
            if db.execute('SELECT 1 FROM datasets WHERE id=?', (dataset_id,)).fetchone():
                return dataset_id
            db.execute('INSERT INTO datasets(id) VALUES(?) ON CONFLICT(id) DO UPDATE SET id=excluded.id', (dataset_id,))
            for asset in assets:
                db.execute('''INSERT INTO assets VALUES(?,?,?,?) ON CONFLICT(dataset_id,name)
                    DO UPDATE SET digest=excluded.digest''', (dataset_id, *asset))
        return dataset_id


def migrate_sqlite(source, target):
    """One atomic import per source path; refuse identity collisions.

    Original SQLite bytes remain untouched. Hashes, sessions and recovery hashes
    remain valid; private rows are remapped to their corresponding target user.
    """
    source = Path(source).resolve()
    if not source.is_file():
        raise StorageError('Исходный SQLite-файл не найден.')
    source_id = sha256(str(source).encode()).hexdigest()
    db_source = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
    db_source.row_factory = sqlite3.Row
    try:
        db_source.execute('BEGIN')
        tables = {row[0] for row in db_source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'users', 'datasets', 'assets', 'cases', 'events'} <= tables:
            raise StorageError('SQLite-файл не содержит рабочее пространство MoneyGraph.')
        # Validate ALL source metadata before the first target transaction. Never
        # derive target identifiers from an untrusted SQLite column name.
        source_columns = {}
        for table, expected in MIGRATION_COLUMNS.items():
            if table not in tables:
                continue
            actual = {row['name'] for row in db_source.execute(f'PRAGMA table_info({table})')}
            supported = [set(expected)]
            if table == 'sessions':
                supported.append(set(expected) - {'remembered'})
            if actual not in supported:
                raise StorageError('Неизвестная схема SQLite. Перенос остановлен до изменения данных MariaDB.')
            source_columns[table] = tuple(column for column in expected if column in actual)
        with target.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            done = db.execute('SELECT summary FROM migrations WHERE source_id=?', (source_id,)).fetchone()
            if done:
                return {**json.loads(done['summary']), 'already_migrated': True}
            users = {}
            counts = {}
            for row in db_source.execute('SELECT ' + ','.join(source_columns['users']) + ' FROM users ORDER BY id'):
                if db.execute('SELECT id FROM users WHERE issuer=? AND subject=?', (row['issuer'], row['subject'])).fetchone():
                    raise StorageError('Миграция остановлена: аккаунт с такой учётной записью уже есть в MariaDB. Исходник сохранён.')
                users[row['id']] = db.execute('INSERT INTO users(issuer,subject,email,name,created_at) VALUES(?,?,?,?,?)',
                    tuple(row[k] for k in ('issuer', 'subject', 'email', 'name', 'created_at'))).lastrowid
            counts['users'] = len(users)
            for table in ('datasets', 'assets', 'credentials', 'cases', 'events', 'sessions', 'auth_limits', 'ai_results'):
                counts[table] = 0
                if table not in tables:
                    continue
                for source_row in db_source.execute('SELECT ' + ','.join(source_columns[table]) + f' FROM {table}'):
                    row = dict(source_row)
                    if 'user_id' in row:
                        row['user_id'] = users[row['user_id']]
                    if table in ('cases', 'events'):
                        del row['id']
                    if table == 'sessions':
                        row.setdefault('remembered', 0)
                    columns = ','.join(row)
                    sql = f'INSERT INTO {table}({columns}) VALUES(' + ','.join('?' for _ in row) + ')'
                    if table == 'datasets':
                        sql += ' ON CONFLICT(id) DO UPDATE SET id=excluded.id'
                    elif table == 'assets':
                        sql += ' ON CONFLICT(dataset_id,name) DO UPDATE SET digest=excluded.digest'
                    db.execute(sql, tuple(row.values()))
                    counts[table] += 1
            db.execute('INSERT INTO migrations(source_id,summary) VALUES(?,?)', (source_id, json.dumps(counts)))
        return {**counts, 'already_migrated': False}
    except sqlite3.DatabaseError:
        raise StorageError('Не удалось перенести SQLite. Транзакция отменена; исходник сохранён.') from None
    finally:
        db_source.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Перенос аккаунтов и заметок SQLite в MariaDB без изменения исходника')
    parser.add_argument('--migrate-sqlite', type=Path, required=True)
    parser.add_argument('--env-file', type=Path, default=Path(__file__).resolve().parents[1] / '.env')
    args = parser.parse_args()
    try:
        config = configured(args.env_file)
        if not config:
            raise StorageError('Настройте MYSQL_* в .env или окружении.')
        result = migrate_sqlite(args.migrate_sqlite, MariaStore(config))
        print(json.dumps(result, ensure_ascii=False))
    except StorageError as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
