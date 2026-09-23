"""Reproducibility acceptance: two fresh processes, hashes, contracts, metadata."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from time import perf_counter

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import pandas as pd
from src.export import validate_outputs


def main():
    with tempfile.TemporaryDirectory(prefix='moneygraph-verify-') as folder:
        signatures=[]
        wall=[]
        for index in range(2):
            out=Path(folder)/str(index)
            start=perf_counter()
            subprocess.run([sys.executable,str(ROOT/'run.py'),'--data',str(ROOT/'data'),'--out',str(out)],check=True,capture_output=True,text=True)
            wall.append(perf_counter()-start)
            frames=[pd.read_csv(out/f'{name}.csv') for name in ['nodes_roles','clusters','top_nodes']]
            validate_outputs(*frames)
            hashes={name:hashlib.sha256((out/f'{name}.csv').read_bytes()).hexdigest() for name in ['nodes_roles','clusters','top_nodes']}
            report=json.loads((out/'run_report.json').read_text())
            assert report['output_sha256']=={name+'.csv':value for name,value in hashes.items()}
            assert len(frames[0])==2248 and frames[1].n_nodes.sum()==2248 and len(frames[2])>=20
            signatures.append(hashes)
        assert signatures[0]==signatures[1], 'CSV not reproducible'
        print(json.dumps({'reproducible':True,'cold_process_seconds':wall,'sha256':signatures[0]},indent=2))


if __name__=='__main__':
    main()
