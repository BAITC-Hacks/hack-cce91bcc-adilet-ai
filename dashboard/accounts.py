"""SQLite credentials and revocable server-side sessions for local accounts."""
import hashlib
import hmac
import re
import secrets
import sqlite3
import time

ITERATIONS = 600_000
SESSION_SECONDS = 8 * 3600
REMEMBER_SECONDS = 30 * 24 * 3600
IDLE_SECONDS = 30 * 60
ATTEMPT_WINDOW = 15 * 60
MAX_FAILURES = 5


class AccountError(ValueError):
    pass


def normalize_email(value):
    value = value.strip().lower()
    if len(value) > 254 or not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+", value):
        raise AccountError('Введите корректный email.')
    return value


def validate_password(password):
    if not 15 <= len(password) <= 128 or len(password.encode('utf-8')) > 512:
        raise AccountError('Пароль должен содержать от 15 до 128 символов. Можно использовать длинную фразу.')


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), ITERATIONS).hex()
    return f'pbkdf2_sha256${ITERATIONS}${salt}${digest}'


def verify_password(password, encoded):
    if len(password) > 128 or len(password.encode('utf-8')) > 512:
        return False
    algorithm, iterations, salt, expected = encoded.split('$')
    if algorithm != 'pbkdf2_sha256' or int(iterations) != ITERATIONS:
        return False
    actual = password_hash(password, salt).rsplit('$', 1)[1]
    return hmac.compare_digest(actual, expected)


def token_hash(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


# Equal-cost password verification when no account exists. No usable credential.
DUMMY_HASH = f'pbkdf2_sha256${ITERATIONS}$' + '00' * 16 + '$' + '00' * 32


class Accounts:
    def __init__(self, store, clock=time.time):
        self.store = store
        self.clock = clock
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS credentials (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id),
                    password_hash TEXT NOT NULL, recovery_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    expires_at REAL NOT NULL, last_seen REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
                CREATE TABLE IF NOT EXISTS auth_limits (
                    action TEXT NOT NULL, identity TEXT NOT NULL,
                    failures INTEGER NOT NULL, window_start REAL NOT NULL,
                    PRIMARY KEY(action, identity));
            ''')

            columns = {row['name'] for row in db.execute('PRAGMA table_info(sessions)')}
            if 'remembered' not in columns:
                db.execute('ALTER TABLE sessions ADD COLUMN remembered INTEGER NOT NULL DEFAULT 0')

    def _limited(self, db, action, email):
        row = db.execute('SELECT * FROM auth_limits WHERE action=? AND identity=?', (action, email)).fetchone()
        return row and self.clock() - row['window_start'] < ATTEMPT_WINDOW and row['failures'] >= MAX_FAILURES

    def _fail(self, db, action, email):
        now = self.clock()
        db.execute('''INSERT INTO auth_limits VALUES(?,?,1,?)
            ON CONFLICT(action,identity) DO UPDATE SET
            failures=CASE WHEN ?-window_start>=? THEN 1 ELSE failures+1 END,
            window_start=CASE WHEN ?-window_start>=? THEN ? ELSE window_start END''',
            (action, email, now, now, ATTEMPT_WINDOW, now, ATTEMPT_WINDOW, now))

    def register(self, name, email, password):
        email = normalize_email(email)
        name = name.strip()
        if not 1 <= len(name) <= 80 or any(ord(c) < 32 for c in name):
            raise AccountError('Имя должно содержать от 1 до 80 символов.')
        validate_password(password)
        encoded = password_hash(password)
        recovery = secrets.token_urlsafe(32)
        try:
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                user_id = db.execute('INSERT INTO users(issuer,subject,email,name) VALUES(?,?,?,?)',
                    ('password', email, email, name)).lastrowid
                db.execute('INSERT INTO credentials VALUES(?,?,?)', (user_id, encoded, token_hash(recovery)))
                db.execute('INSERT INTO events(user_id,action,details) VALUES(?,?,?)', (user_id, 'Создан аккаунт', ''))
        except sqlite3.IntegrityError:
            raise AccountError('Не удалось создать аккаунт с этим email. Попробуйте войти или восстановить доступ.') from None
        return recovery

    def login(self, email, password, remember=False):
        email = normalize_email(email)
        token = None
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if self._limited(db, 'login', email):
                raise AccountError('Слишком много попыток. Повторите через 15 минут.')
            row = db.execute('''SELECT users.id,credentials.password_hash FROM users
                JOIN credentials ON credentials.user_id=users.id
                WHERE users.issuer='password' AND users.subject=?''', (email,)).fetchone()
            valid = verify_password(password, row['password_hash'] if row else DUMMY_HASH)
            if valid and row:
                now = self.clock()
                token = secrets.token_urlsafe(32)
                db.execute('DELETE FROM sessions WHERE expires_at<=? OR (remembered=0 AND last_seen<=?)', (now, now-IDLE_SECONDS))
                db.execute('INSERT INTO sessions(token_hash,user_id,expires_at,last_seen,remembered) VALUES(?,?,?,?,?)',
                    (token_hash(token), row['id'], now+(REMEMBER_SECONDS if remember else SESSION_SECONDS), now, int(remember)))
                db.execute("DELETE FROM auth_limits WHERE action='login' AND identity=?", (email,))
                db.execute('INSERT INTO events(user_id,action,details) VALUES(?,?,?)', (row['id'], 'Вход в аккаунт', ''))
            else:
                self._fail(db, 'login', email)
        if token is None:
            raise AccountError('Неверный email или пароль.')
        return token

    def identity(self, token):
        if not isinstance(token, str) or not 20 <= len(token) <= 128:
            return None
        now = self.clock()
        with self.store.connect() as db:
            row = db.execute('''SELECT users.id,users.email,users.name FROM sessions
                JOIN users ON users.id=sessions.user_id
                WHERE token_hash=? AND expires_at>? AND (remembered=1 OR last_seen>?)''',
                (token_hash(token), now, now-IDLE_SECONDS)).fetchone()
            if row:
                db.execute('UPDATE sessions SET last_seen=? WHERE token_hash=?', (now, token_hash(token)))
            return dict(row) if row else None

    def logout(self, token, all_sessions=False):
        with self.store.connect() as db:
            row = db.execute('SELECT user_id FROM sessions WHERE token_hash=?', (token_hash(token),)).fetchone()
            if row:
                if all_sessions:
                    db.execute('DELETE FROM sessions WHERE user_id=?', (row['user_id'],))
                else:
                    db.execute('DELETE FROM sessions WHERE token_hash=?', (token_hash(token),))
                db.execute('INSERT INTO events(user_id,action,details) VALUES(?,?,?)', (row['user_id'], 'Выход из всех сессий' if all_sessions else 'Выход из аккаунта', ''))

    def reset_password(self, email, recovery, new_password):
        email = normalize_email(email)
        validate_password(new_password)
        if len(recovery) > 128:
            raise AccountError('Неверный email или резервный код.')
        replacement = None
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if self._limited(db, 'reset', email):
                raise AccountError('Слишком много попыток. Повторите через 15 минут.')
            row = db.execute('''SELECT users.id,credentials.recovery_hash FROM users
                JOIN credentials ON credentials.user_id=users.id
                WHERE users.issuer='password' AND users.subject=?''', (email,)).fetchone()
            valid = hmac.compare_digest(token_hash(recovery.strip()), row['recovery_hash'] if row else '0'*64)
            if row and valid:
                replacement = secrets.token_urlsafe(32)
                db.execute('UPDATE credentials SET password_hash=?,recovery_hash=? WHERE user_id=?',
                    (password_hash(new_password), token_hash(replacement), row['id']))
                db.execute('DELETE FROM sessions WHERE user_id=?', (row['id'],))
                db.execute('DELETE FROM auth_limits WHERE identity=?', (email,))
                db.execute('INSERT INTO events(user_id,action,details) VALUES(?,?,?)', (row['id'], 'Восстановлен доступ', ''))
            else:
                self._fail(db, 'reset', email)
        if replacement is None:
            raise AccountError('Неверный email или резервный код.')
        return replacement

    def change_password(self, token, old_password, new_password):
        validate_password(new_password)
        identity = self.identity(token)
        if not identity:
            raise AccountError('Сессия истекла. Войдите снова.')
        changed = False
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # Recheck after acquiring the write lock, including revocation.
            session = db.execute('SELECT 1 FROM sessions WHERE token_hash=? AND expires_at>? AND (remembered=1 OR last_seen>?)',
                (token_hash(token), self.clock(), self.clock()-IDLE_SECONDS)).fetchone()
            if not session:
                raise AccountError('Сессия истекла. Войдите снова.')
            if self._limited(db, 'change', identity['email']):
                raise AccountError('Слишком много попыток. Повторите через 15 минут.')
            row = db.execute('SELECT password_hash FROM credentials WHERE user_id=?', (identity['id'],)).fetchone()
            if row and verify_password(old_password, row['password_hash']):
                db.execute('UPDATE credentials SET password_hash=? WHERE user_id=?', (password_hash(new_password), identity['id']))
                db.execute('DELETE FROM sessions WHERE user_id=?', (identity['id'],))
                db.execute("DELETE FROM auth_limits WHERE action='change' AND identity=?", (identity['email'],))
                db.execute('INSERT INTO events(user_id,action,details) VALUES(?,?,?)', (identity['id'], 'Изменён пароль', ''))
                changed = True
            else:
                self._fail(db, 'change', identity['email'])
        if not changed:
            raise AccountError('Текущий пароль неверен.')
