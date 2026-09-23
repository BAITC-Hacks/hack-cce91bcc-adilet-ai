import sqlite3
import pytest
from dashboard.storage import Store


def test_persistence_identity_isolation_and_snapshot(tmp_path):
    path = tmp_path / 'workspace.db'
    store = Store(path)
    first = store.user('issuer', '1', 'a@test', 'A')
    second = store.user('issuer', '2', 'b@test', 'B')
    assert first == store.user('issuer', '1', 'new@test', 'A')
    dataset = store.snapshot({'nodes.csv': b'gid\n123456789012345678\n'})
    assert dataset == store.snapshot({'nodes.csv': b'gid\n123456789012345678\n'})
    assert dataset != store.snapshot({'nodes.csv': b'gid\n123456789012345679\n'})
    gid = '123456789012345678'
    store.save_case(first, dataset, gid, 'В работе', "' ; DROP TABLE users; --", 'facts')
    reopened = Store(path)
    assert reopened.case(first, dataset, gid)['gid'] == gid
    assert reopened.cases(second) == []
    assert reopened.case(second, dataset, gid) is None
    assert reopened.history(second) == []
    reopened.save_case(first, dataset, gid, 'Проверено', 'updated', 'facts 2')
    assert len(reopened.cases(first)) == 1
    assert reopened.case(first, dataset, gid)['status'] == 'Проверено'
    assert len(reopened.history(first)) == 2
    with reopened.connect() as db:
        assert db.execute('SELECT content FROM assets WHERE dataset_id=?', (dataset,)).fetchone()[0] == b'gid\n123456789012345678\n'
    with pytest.raises(sqlite3.IntegrityError):
        reopened.save_case(999, dataset, gid, 'В работе', '', '')
    with pytest.raises(ValueError):
        reopened.save_case(first, dataset, float(gid), 'В работе', '', '')

