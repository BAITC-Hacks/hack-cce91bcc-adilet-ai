import pytest
from dashboard.accounts import Accounts, AccountError, SESSION_SECONDS, IDLE_SECONDS, ATTEMPT_WINDOW
from dashboard.storage import Store

PASSWORD = 'correct horse battery staple'


@pytest.fixture
def service(tmp_path):
    now = [1000.0]
    store = Store(tmp_path / 'accounts.db')
    return Accounts(store, clock=lambda: now[0]), now


def test_credentials_sessions_and_revocation(service):
    accounts, now = service
    recovery = accounts.register('First', 'First@EXAMPLE.test', PASSWORD)
    accounts.register('Second', 'second@example.test', PASSWORD)
    with accounts.store.connect() as db:
        rows = db.execute('SELECT password_hash,recovery_hash FROM credentials').fetchall()
        assert rows[0]['password_hash'] != rows[1]['password_hash']
        assert PASSWORD not in rows[0]['password_hash']
        assert recovery != rows[0]['recovery_hash']
    with pytest.raises(AccountError):
        accounts.register('Duplicate', 'first@example.test', PASSWORD)
    with pytest.raises(AccountError):
        accounts.login('first@example.test', 'wrong password')
    token = accounts.login(' FIRST@example.test ', PASSWORD)
    other = accounts.login('first@example.test', PASSWORD)
    assert accounts.identity(token)['name'] == 'First'
    with accounts.store.connect() as db:
        assert db.execute('SELECT token_hash FROM sessions').fetchone()[0] != token
    accounts.logout(token)
    assert accounts.identity(token) is None
    assert accounts.identity(other)
    accounts.logout(other, all_sessions=True)
    assert accounts.identity(other) is None
    assert accounts.identity('made-up-token-that-is-not-real') is None


def test_expiration_and_failed_login_persistence(service):
    accounts, now = service
    accounts.register('First', 'first@example.test', PASSWORD)
    for _ in range(5):
        with pytest.raises(AccountError, match='Неверный'):
            accounts.login('first@example.test', 'wrong')
    reopened = Accounts(accounts.store, clock=lambda: now[0])
    with pytest.raises(AccountError, match='Слишком много'):
        reopened.login('first@example.test', PASSWORD)
    now[0] += ATTEMPT_WINDOW + 1
    token = reopened.login('first@example.test', PASSWORD)
    now[0] += IDLE_SECONDS + 1
    assert reopened.identity(token) is None
    token = reopened.login('first@example.test', PASSWORD)
    # Keep activity alive, but absolute lifetime must still expire.
    for _ in range(SESSION_SECONDS // 600 + 1):
        now[0] += 600
        reopened.identity(token)
    assert reopened.identity(token) is None


def test_reset_code_once_and_change_password(service):
    accounts, _ = service
    recovery = accounts.register('First', 'first@example.test', PASSWORD)
    token = accounts.login('first@example.test', PASSWORD)
    new_password = PASSWORD + ' new'
    replacement = accounts.reset_password('first@example.test', recovery, new_password)
    assert replacement != recovery and accounts.identity(token) is None
    with pytest.raises(AccountError):
        accounts.reset_password('first@example.test', recovery, PASSWORD)
    with pytest.raises(AccountError):
        accounts.login('first@example.test', PASSWORD)
    token = accounts.login('first@example.test', new_password)
    with pytest.raises(AccountError):
        accounts.change_password(token, 'wrong', PASSWORD)
    assert accounts.identity(token)
    accounts.change_password(token, new_password, PASSWORD)
    assert accounts.identity(token) is None
    assert accounts.identity(accounts.login('first@example.test', PASSWORD))


@pytest.mark.parametrize('email,password', [('bad-email', PASSWORD), ('valid@example.test', 'short'), ('valid@example.test', 'x'*129)])
def test_invalid_registration(service, email, password):
    accounts, _ = service
    with pytest.raises(AccountError):
        accounts.register('Test', email, password)
    with accounts.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0


def test_reset_rate_limit_and_legacy_preservation(service):
    accounts, now = service
    legacy = accounts.store.user('local', 'default', '', 'Legacy')
    dataset = accounts.store.snapshot({'nodes.csv': b'gid\n123\n'})
    accounts.store.save_case(legacy, dataset, '123', 'В работе', 'Old note', 'Old report')
    recovery = accounts.register('New', 'new@example.test', PASSWORD)
    for _ in range(5):
        with pytest.raises(AccountError, match='Неверный'):
            accounts.reset_password('new@example.test', 'wrong-code', PASSWORD)
    with pytest.raises(AccountError, match='Слишком много'):
        accounts.reset_password('new@example.test', recovery, PASSWORD)
    now[0] += ATTEMPT_WINDOW + 1
    accounts.reset_password('new@example.test', recovery, PASSWORD)
    identity = accounts.identity(accounts.login('new@example.test', PASSWORD))
    assert accounts.store.cases(identity['id']) == []
    assert accounts.store.cases(legacy)[0]['note'] == 'Old note'
