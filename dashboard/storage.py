"""Local SQLite workspace. Every private query is scoped to the account ID."""
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import json
import sqlite3
import os


class StorageError(RuntimeError):
    """A safe, user-facing storage failure without credentials or SQL values."""


def open_store(root=None):
    """Select configured MariaDB; an explicit MONEYGRAPH_DB selects SQLite.

    A configured but unavailable MariaDB is an error, never an empty fallback.
    """
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    explicit = os.environ.get('MONEYGRAPH_DB')
    if explicit:
        return Store(explicit)
    from dashboard.maria_storage import MariaStore, configured
    config = configured(root / '.env')
    return MariaStore(config) if config else Store(root / 'data/moneygraph.sqlite3')


class Store:
    backend = 'sqlite'

    def __init__(self, path):
        self.path = Path(path)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise StorageError('Не удалось открыть локальное хранилище. Проверьте путь и права доступа.') from None
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY, issuer TEXT NOT NULL, subject TEXT NOT NULL,
                    email TEXT NOT NULL, name TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(issuer, subject));
                CREATE TABLE IF NOT EXISTS datasets (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS assets (
                    dataset_id TEXT REFERENCES datasets(id), name TEXT NOT NULL,
                    digest TEXT NOT NULL, content BLOB NOT NULL,
                    PRIMARY KEY(dataset_id, name));
                CREATE TABLE IF NOT EXISTS cases (
                    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                    dataset_id TEXT NOT NULL REFERENCES datasets(id), gid TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('В работе','Проверено','Отложено')),
                    note TEXT NOT NULL, report TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, dataset_id, gid));
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                    action TEXT NOT NULL, details TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                PRAGMA user_version = 1;
            ''')

    @contextmanager
    def connect(self):
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=15)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys = ON')
            with db:
                yield db
        except sqlite3.IntegrityError:
            raise
        except sqlite3.Error:
            raise StorageError('Локальное хранилище недоступно. Проверьте файл и повторите действие.') from None
        finally:
            if db is not None:
                db.close()

    def user(self, issuer, subject, email, name):
        if not issuer or not subject:
            raise ValueError('Источник и идентификатор пользователя обязательны')
        with self.connect() as db:
            db.execute('''INSERT INTO users(issuer, subject, email, name) VALUES(?,?,?,?)
                ON CONFLICT(issuer,subject) DO UPDATE SET email=excluded.email,name=excluded.name''',
                (issuer, subject, email, name))
            return db.execute('SELECT id FROM users WHERE issuer=? AND subject=?', (issuer, subject)).fetchone()['id']

    def snapshot(self, files):
        assets = [(name, sha256(content).hexdigest(), content) for name, content in sorted(files.items())]
        dataset_id = sha256(json.dumps([(n, h) for n, h, _ in assets]).encode()).hexdigest()
        with self.connect() as db:
            if db.execute('SELECT 1 FROM datasets WHERE id=?', (dataset_id,)).fetchone() is None:
                db.execute('INSERT INTO datasets(id) VALUES(?)', (dataset_id,))
                db.executemany('INSERT INTO assets VALUES(?,?,?,?)', [(dataset_id, *a) for a in assets])
        return dataset_id

    def save_case(self, user_id, dataset_id, gid, status, note, report):
        if not isinstance(gid, str) or not gid.isdigit():
            raise ValueError('gid должен быть строкой из цифр')
        if status not in ('В работе', 'Проверено', 'Отложено') or len(note) > 10000:
            raise ValueError('Некорректный статус или слишком длинная заметка')
        with self.connect() as db:
            db.execute('''INSERT INTO cases(user_id,dataset_id,gid,status,note,report) VALUES(?,?,?,?,?,?)
                ON CONFLICT(user_id,dataset_id,gid) DO UPDATE SET status=excluded.status,
                note=excluded.note,report=excluded.report,updated_at=CURRENT_TIMESTAMP''',
                (user_id, dataset_id, gid, status, note, report))
            db.execute('INSERT INTO events(user_id,action,details) VALUES(?,?,?)',
                (user_id, 'Сохранена проверка', json.dumps({'gid': gid, 'status': status, 'dataset': dataset_id}, ensure_ascii=False)))

    def cases(self, user_id):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM cases WHERE user_id=? ORDER BY updated_at DESC,id DESC', (user_id,))]

    def case(self, user_id, dataset_id, gid):
        with self.connect() as db:
            row = db.execute('SELECT * FROM cases WHERE user_id=? AND dataset_id=? AND gid=?', (user_id, dataset_id, gid)).fetchone()
            return dict(row) if row else None

    def event(self, user_id, action, details):
        with self.connect() as db:
            db.execute('INSERT INTO events(user_id,action,details) VALUES(?,?,?)', (user_id, action, details))

    def history(self, user_id):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT created_at,action,details FROM events WHERE user_id=? ORDER BY id DESC LIMIT 100', (user_id,))]
