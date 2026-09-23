"""Local provenance and measurable diagnostics; never claims classification accuracy."""
import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_report(data_dir, out_dir, df, edges, tx, timings):
    out_dir = Path(out_dir)
    ranked = df.sort_values(['priority_score', 'gid'], ascending=[False, True])
    top = set(ranked.gid.head(20))
    baseline = df.assign(volume=df[['in_kzt', 'out_kzt']].max(axis=1)).sort_values(['volume', 'gid'], ascending=[False, True])
    sensitivity = {}
    for column in [c for c in df if c.startswith('priority_part_')]:
        alternative = df.assign(alternative=df.priority_score-df[column]).sort_values(['alternative', 'gid'], ascending=[False, True])
        sensitivity[column.removeprefix('priority_part_')] = len(top & set(alternative.gid.head(20)))
    root = Path(__file__).resolve().parents[1]
    report = {
        'algorithm': 'moneygraph-v1', 'python': platform.python_version(),
        'packages': {name: importlib.metadata.version(name) for name in ['pandas','numpy','networkx','pyarrow','scipy']},
        'parameters': {'random_seed':42,'betweenness_k':min(128,len(df)), 'quantile':.99,'quantile_method':'linear','role_threshold':.55,'fast_window_days':2},
        'input_sha256': {name: digest(Path(data_dir)/name) for name in ['nodes.parquet','edges.parquet','transactions.parquet']},
        'output_sha256': {name: digest(out_dir/name) for name in ['nodes_roles.csv','clusters.csv','top_nodes.csv']},
        'source_sha256': {str(path.relative_to(root)):digest(path) for path in sorted((root/'src').glob('*.py'))+[root/'run.py']},
        'observations': {'nodes':len(df),'edges':len(edges),'transactions':len(tx),'sum_kzt':float(edges.sum_kzt.sum()),
                         'seed':int(df.is_seed.sum()),'isolated_seed':int((df.is_seed & (df.in_deg+df.out_deg).eq(0)).sum()),
                         'depth_boundary':int(df.truncated_by_depth.sum()),'date_min':str(tx.date.min().date()),'date_max':str(tx.date.max().date())},
        'diagnostics': {'volume_baseline_top20_overlap':len(top & set(baseline.gid.head(20))),
                        'leave_one_component_out_top20_overlap':sensitivity,
                        'interpretation':'Sensitivity and baseline agreement only; no ground-truth accuracy measurement.'},
        'seconds':dict(timings),
        'limitations':['Outgoing sample to depth 4','Seed inflows incomplete','Dates have no intraday ordering','Scores are heuristic, not probabilities']}
    (out_dir/'run_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report
