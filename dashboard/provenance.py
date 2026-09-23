"""Optional run metadata is only displayed after current CSV hashes match."""
import hashlib
import json
from dashboard.data import resolve_path


def read_report(out_dir):
    folder=resolve_path(out_dir)
    report=json.loads((folder/'run_report.json').read_text(encoding='utf-8'))
    for name in ['nodes_roles.csv','clusters.csv','top_nodes.csv']:
        actual=hashlib.sha256((folder/name).read_bytes()).hexdigest()
        if report['output_sha256'][name]!=actual:
            raise ValueError('CSV изменились после расчёта паспорта. Повторите pipeline.')
    return report
