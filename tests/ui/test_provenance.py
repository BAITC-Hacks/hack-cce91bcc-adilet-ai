import hashlib
import json
import pytest
from dashboard.provenance import read_report


def test_report_detects_changed_export(tmp_path):
    hashes={}
    for name in ['nodes_roles.csv','clusters.csv','top_nodes.csv']:
        path=tmp_path/name
        path.write_text('synthetic fixture\n')
        hashes[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    (tmp_path/'run_report.json').write_text(json.dumps({'output_sha256':hashes}))
    assert read_report(tmp_path)['output_sha256']==hashes
    (tmp_path/'top_nodes.csv').write_text('modified\n')
    with pytest.raises(ValueError,match='CSV'):
        read_report(tmp_path)
