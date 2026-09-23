"""Exercise the actual inline JS with browser-storage stubs in Node."""
import ast
import shutil
import subprocess
from pathlib import Path
import pytest


def test_storage_bridge(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for browser storage logic checks')
    tree = ast.parse(Path('dashboard/welcome.py').read_text())
    js = next(k.value.value for call in ast.walk(tree) if isinstance(call, ast.Call)
              for k in call.keywords if k.arg == 'js' and isinstance(k.value, ast.Constant))
    script = tmp_path / 'bridge.mjs'
    script.write_text(js.replace('export default function', 'function bridge') + '''
import assert from 'node:assert/strict';
const saved = new Map();
globalThis.localStorage = {
    getItem: k => saved.get(k), setItem: (k,v) => saved.set(k,v), removeItem: k => saved.delete(k)
};
function run(data) {
    const state = {};
    const marker = {dataset:{}};
    bridge({data, parentElement:{querySelector:()=>marker}, setStateValue:(k,v)=>state[k]=v});
    return state;
}
const key = 'moneygraph.session.v1';
assert.equal(run({action:'read'}).token, null);
run({action:'save',token:'test-revocable-token'});
assert.equal(run({action:'read'}).token, 'test-revocable-token');
run({action:'clear'});
assert.equal(run({action:'read'}).token, null);
saved.set(key, JSON.stringify({token:'old',expires:0}));
assert.equal(run({action:'read'}).token, null);
saved.set(key, 'corrupt');
assert.equal(run({action:'read'}).error, true);
globalThis.localStorage.getItem = () => {throw Error('blocked');};
assert.equal(run({action:'read'}).error, true);
''')
    subprocess.run([node, str(script)], check=True, capture_output=True, text=True)
