"""Integration smoke against real outputs; no synthetic clients in the app."""
import pytest
pytest.importorskip('streamlit', reason='Установите dashboard/requirements.txt для Streamlit AppTest')
pytest.importorskip('plotly', reason='Установите dashboard/requirements.txt для Plotly')
from streamlit.testing.v1 import AppTest
from dashboard.data import ROOT, load_bundle


def test_real_app_navigation():
    if not (ROOT / 'out/nodes_roles.csv').exists():
        pytest.skip('Сначала сформируйте реальные CSV')
    app = AppTest.from_file(str(ROOT / 'dashboard/app.py'), default_timeout=30).run()
    assert not app.exception
    assert [metric.value for metric in app.metric][:3] == ['2 248', '3 119', '4 840']
    app.sidebar.radio[0].set_value('Узел и переводы').run()
    assert not app.exception
    assert len(app.metric) == 3
    assert any('Кто выше' in element.value for element in app.subheader)
    assert any('Почему такой приоритет' in element.label for element in app.expander)
    app.text_input[0].set_value('unknown').run()
    assert not app.exception and any('не найден' in warning.value for warning in app.warning)
    nodes, clusters, _, _ = load_bundle('data', 'out')
    for row in [nodes[nodes.is_seed & nodes.in_deg.eq(0) & nodes.out_deg.eq(0)].iloc[0], nodes[nodes.depth.eq(4) & nodes.out_deg.eq(0)].iloc[0]]:
        app.text_input[0].set_value(row.gid).run()
        assert not app.exception and app.warning
    app.sidebar.radio[0].set_value('Кластеры').run()
    for size in [clusters.n_nodes.min(), clusters.n_nodes.max()]:
        gid = clusters.loc[clusters.n_nodes.eq(size), 'cluster_id'].iloc[0]
        app.selectbox[0].set_value(gid).run()
        assert not app.exception and app.metric[0].value == str(int(size))
    app.sidebar.radio[0].set_value('Проверяемость').run()
    assert not app.exception
    if (ROOT / 'out/run_report.json').exists():
        assert app.success
    app.sidebar.text_input[1].set_value('/tmp/moneygraph-missing-ui-input').run()
    assert not app.exception and app.error and app.code
