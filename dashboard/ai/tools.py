"""Seven bounded read-only tools. No executable/model-provided expressions."""
from collections import deque
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import time
import networkx as nx
from jsonschema import Draft202012Validator
from dashboard.ai.config import AIError, MAX_TOOL_BYTES
from dashboard.ai.evidence import Registry, canonical

GID = {'type': 'string', 'pattern': '^[0-9]{1,30}$'}
DAY = {'type': 'string', 'pattern': '^20[0-9]{2}-[0-9]{2}-[0-9]{2}$'}

def obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}

def integer(low, high):
    return {'type': 'integer', 'minimum': low, 'maximum': high}

TOOL_SCHEMAS = {
    'get_node_profile': obj({'gid': GID}),
    'get_neighborhood': obj({'gid': GID, 'direction': {'enum': ['in', 'out', 'both']}, 'max_hops': integer(1,4), 'limit': integer(1,30)}),
    'get_transactions': obj({'gids': {'type':'array','items':GID,'minItems':1,'maxItems':5,'uniqueItems':True}, 'date_from': DAY, 'date_to': DAY, 'limit':integer(1,20)}),
    'check_temporal_path': obj({'path': {'type':'array','items':GID,'minItems':2,'maxItems':5,'uniqueItems':True}}),
    'find_flow_patterns': obj({'gid': GID, 'limits': obj({'max_transactions': integer(2,120)})}),
    'compare_peers': obj({'gid': GID}),
    'simulate_node_removal': obj({'gid': GID}),
}
TOOL_DESCRIPTIONS = {
    'get_node_profile': 'Observed node amounts/counts, existing role, priority components and sampling flags.',
    'get_neighborhood': 'Bounded BFS with explicit direction. Returned paths respect traversal; incoming paths are reversed to payment direction.',
    'get_transactions': 'Source transfers touching requested gids; exact row evidence, chronological order and explicit truncation.',
    'check_temporal_path': 'Maximum date-compatible capacity along one simple directed path; compares inclusive versus strictly increasing dates.',
    'find_flow_patterns': 'Collection/distribution counts and capacity-limited transit matching within two days; overlapping patterns are not additive.',
    'compare_peers': 'Up to eight same-seed-status and same-boundary-status peers within a factor of two in volume and activity.',
    'simulate_node_removal': 'Weak connectivity and up-to-four-hop directed reachability between sampled neighbors before/after removal from a COPY.',
}


def cents(amount):
    return int((Decimal(str(amount))*100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def path_capacity(tx, path, strict=False):
    """Time-expanded max flow. Each transfer capacity appears on one leg only."""
    dates = sorted(tx.date.unique())
    if not dates:
        return 0
    graph = nx.DiGraph()
    graph.add_nodes_from(['S','T'])
    total = sum(cents(x) for x in tx.sum_kzt)
    for layer in range(len(path)):
        for i, day in enumerate(dates):
            graph.add_node((layer, i))
            if i+1 < len(dates):
                graph.add_edge((layer,i), (layer,i+1), capacity=total)
    for i in range(len(dates)):
        graph.add_edge('S', (0,i), capacity=total)
        graph.add_edge((len(path)-1,i), 'T', capacity=total)
    date_index = {day:i for i,day in enumerate(dates)}
    for leg, (src,dst) in enumerate(zip(path,path[1:])):
        selected = tx[tx.src.eq(src) & tx.dst.eq(dst)]
        daily = {}
        for r in selected.itertuples():
            daily[r.date] = daily.get(r.date, 0) + cents(r.sum_kzt)
        for day, capacity in daily.items():
            i = date_index[day]
            target_day = i+1 if strict and leg < len(path)-2 else i
            if target_day < len(dates):
                graph.add_edge((leg,i), (leg+1,target_day), capacity=capacity)
    return nx.maximum_flow_value(graph, 'S', 'T') / 100


def transit_capacity(tx, gid, strict=False):
    """Bipartite capacity matching. Self-transfers excluded by caller."""
    incoming = list(tx[tx.dst.eq(gid)].itertuples())
    outgoing = list(tx[tx.src.eq(gid)].itertuples())
    graph = nx.DiGraph()
    graph.add_nodes_from(['S','T'])
    for r in incoming:
        graph.add_edge('S', ('i',r.Index), capacity=cents(r.sum_kzt))
    for r in outgoing:
        graph.add_edge(('o',r.Index), 'T', capacity=cents(r.sum_kzt))
    for i in incoming:
        for o in outgoing:
            days = (date.fromisoformat(o.date)-date.fromisoformat(i.date)).days
            if (1 if strict else 0) <= days <= 2:
                graph.add_edge(('i',i.Index), ('o',o.Index), capacity=min(cents(i.sum_kzt),cents(o.sum_kzt)))
    return nx.maximum_flow_value(graph,'S','T') / 100


class Toolset:
    def __init__(self, data, focus, deadline=None, max_calls=8):
        if not isinstance(focus,str) or focus not in data.graph:
            raise AIError('Выбранный gid отсутствует в данных.')
        self.data, self.focus = data, focus
        self.registry = Registry(data)
        self.deadline = deadline or time.monotonic()+180
        self.max_calls, self.calls = max_calls, []
        # Scope is fixed locally before any model request; model cannot expand it.
        self.scope = {focus}
        queue = deque([(focus,0)])
        while queue and len(self.scope) < 80:
            gid, depth = queue.popleft()
            if depth == 4:
                continue
            neighbors = set(data.graph.predecessors(gid)) | set(data.graph.successors(gid))
            for other in sorted(neighbors, key=int):
                if other not in self.scope and len(self.scope) < 80:
                    self.scope.add(other)
                    queue.append((other,depth+1))

    def remaining(self):
        if time.monotonic() >= self.deadline:
            raise AIError('Истёк общий дедлайн расследования.')

    def execute(self, name, arguments, purpose='support'):
        self.remaining()
        if len(self.calls) >= self.max_calls:
            raise AIError('Исчерпан лимит вызовов инструментов.')
        call_id = f'check_{len(self.calls)+1}'
        trace = {'check_id':call_id,'name':name if name in TOOL_SCHEMAS else 'unknown','purpose':purpose,'ok':False}
        self.calls.append(trace)
        if name not in TOOL_SCHEMAS or not isinstance(arguments,dict):
            raise AIError('Неизвестный инструмент или неверные аргументы.')
        if list(Draft202012Validator(TOOL_SCHEMAS[name]).iter_errors(arguments)):
            raise AIError('Аргументы инструмента не соответствуют схеме или лимитам.')
        gids = [arguments['gid']] if 'gid' in arguments else arguments.get('gids',arguments.get('path',[]))
        if not set(gids) <= self.scope:
            raise AIError('gid вне ограниченного окружения выбранного кейса.')
        before = set(self.registry.records)
        try:
            result = getattr(self, name)(**arguments)
            self.remaining()
            if len(canonical(result).encode()) > MAX_TOOL_BYTES:
                raise AIError('Результат инструмента превышает лимит контекста.')
        except Exception:
            for eid in set(self.registry.records)-before:
                del self.registry.records[eid]
            raise
        trace.update(ok=True, arguments=arguments, evidence_ids=[r['evidence_id'] for r in result])
        return {'check_id':call_id,'evidence':result}

    def get_node_profile(self, gid):
        row = self.data.nodes[self.data.nodes.gid.eq(gid)].iloc[0]
        tx = self.data.tx
        incoming, outgoing = tx[tx.dst.eq(gid)], tx[tx.src.eq(gid)]
        values = {'in_kzt':sum(cents(x) for x in incoming.sum_kzt)/100,
            'out_kzt':sum(cents(x) for x in outgoing.sum_kzt)/100,
            'in_tx':len(incoming),'out_tx':len(outgoing),'in_deg':incoming.src.nunique(),'out_deg':outgoing.dst.nunique(),
            'role':str(row.role),'role_score':float(row.role_score),'priority_score':float(row.priority_score),
            'depth':int(row.depth),'is_seed':bool(row.is_seed),'inflows_incomplete':bool(row.is_seed),
            'truncated_by_depth':bool(row.truncated_by_depth)}
        values.update({c:float(row[c]) for c in row.index if c.startswith('priority_part_')})
        sources = list(tx.loc[tx.src.eq(gid)|tx.dst.eq(gid),'_record_id']) + [row['_record_id']]
        return [self.registry.add('node_profile',[gid],values,sources)]

    def get_neighborhood(self, gid, direction, max_hops, limit):
        graph = self.data.graph
        seen, queue, paths = {gid}, deque([(gid,0)]), {gid:[gid]}
        pairs = set()
        truncated = False
        while queue:
            self.remaining()
            current, depth = queue.popleft()
            if depth >= max_hops:
                continue
            neighbors = set()
            if direction in ('in','both'):
                neighbors.update(graph.predecessors(current))
            if direction in ('out','both'):
                neighbors.update(graph.successors(current))
            for other in sorted(neighbors,key=int):
                if other in seen:
                    continue
                if other not in self.scope or len(seen)-1 >= limit:
                    truncated = True
                    continue
                seen.add(other)
                paths[other] = paths[current]+[other]
                pairs.add((other,current) if direction == 'in' or not graph.has_edge(current,other) else (current,other))
                queue.append((other,depth+1))
        edges = self.data.edges
        sources = edges.loc[[pair in pairs for pair in zip(edges.src,edges.dst)],'_record_id'].tolist()
        values = {'direction':direction,'max_hops':max_hops,'returned_nodes':len(seen)-1,'truncated':truncated,
            'gids':';'.join(sorted(seen-{gid},key=int)),
            'paths':'; '.join(' → '.join(reversed(p) if direction=='in' else p) for n,p in paths.items() if n!=gid),
            'path_interpretation':'payment_direction' if direction!='both' else 'mixed_traversal_not_payment_path'}
        return [self.registry.add('neighborhood',[gid],values,sources)]

    def get_transactions(self, gids, date_from, date_to, limit):
        try:
            start, end = date.fromisoformat(date_from), date.fromisoformat(date_to)
        except ValueError:
            raise AIError('Некорректная календарная дата.') from None
        if start > end:
            raise AIError('Начало периода позже конца.')
        tx = self.data.tx
        selected = tx[(tx.src.isin(gids)|tx.dst.isin(gids)) & tx.date.between(date_from,date_to)].sort_values(['date','_record_id'])
        facts = [self.registry.add('transaction_selection',gids,
            {'available':len(selected),'returned':min(len(selected),limit),'truncated':len(selected)>limit},
            selected.head(limit)._record_id.tolist(), {'date_from':date_from,'date_to':date_to})]
        for _,r in selected.head(limit).iterrows():
            facts.append(self.registry.add('transaction',[r.src,r.dst],
                {'src':r.src,'dst':r.dst,'sum_kzt':cents(r.sum_kzt)/100,'date':r.date},[r['_record_id']],
                {'date_from':r.date,'date_to':r.date}))
        return facts

    def check_temporal_path(self, path):
        tx = self.data.tx
        pairs = set(zip(path,path[1:]))
        selected = tx[[pair in pairs for pair in zip(tx.src,tx.dst)]]
        if len(selected) > 400:
            raise AIError('Для временного пути доступно более 400 операций: проверка не выполнена, сократите путь.')
        possible = path_capacity(selected,path)
        strict = path_capacity(selected,path,strict=True)
        values = {'path':' → '.join(path),'possible_kzt':possible,'strictly_increasing_kzt':strict,
            'compatible':possible>0,'same_day_needed_for_max':possible>strict,
            'transactions':len(selected),'proves_money_trace':False}
        return [self.registry.add('temporal_path',path,values,selected._record_id.tolist())]

    def find_flow_patterns(self, gid, limits):
        tx = self.data.tx
        touching = tx[tx.src.eq(gid)|tx.dst.eq(gid)]
        nonself = touching[touching.src.ne(touching.dst)]
        selected = nonself.sort_values(['date','_record_id']).head(limits['max_transactions'])
        values = {'in_counterparties':nonself.loc[nonself.dst.eq(gid),'src'].nunique(),
            'out_counterparties':nonself.loc[nonself.src.eq(gid),'dst'].nunique(),
            'possible_transit_kzt':transit_capacity(selected,gid),
            'strict_transit_kzt':transit_capacity(selected,gid,strict=True),
            'selected_transactions':len(selected),'available_transactions':len(nonself),
            'truncated':len(selected)<len(nonself),'excluded_self_transfers':len(touching)-len(nonself),
            'window_days':2,'nonadditive_patterns':True}
        return [self.registry.add('flow_patterns',[gid],values,touching._record_id.tolist())]

    def compare_peers(self, gid):
        nodes = self.data.nodes.copy()
        focus = nodes[nodes.gid.eq(gid)].iloc[0]
        volume = nodes.in_kzt+nodes.out_kzt
        activity = nodes.in_tx+nodes.out_tx
        fv, fa = float(focus.in_kzt+focus.out_kzt), int(focus.in_tx+focus.out_tx)
        mask = nodes.gid.ne(gid) & nodes.is_seed.eq(focus.is_seed) & nodes.truncated_by_depth.eq(focus.truncated_by_depth)
        mask &= volume.between(fv/2,fv*2) & activity.between(fa/2,fa*2)
        peers = nodes[mask].copy()
        peers['_distance'] = (volume[mask]-fv).abs()/max(fv,1)+(activity[mask]-fa).abs()/max(fa,1)
        peers = peers.sort_values(['_distance','gid']).head(8)
        values = {'group_size':len(peers),'eligible_peers':int(mask.sum()),'sufficient':len(peers)>=3,
            'criteria':'same seed/boundary status; volume=in+out and activity=in_tx+out_tx within factor two; closest normalized distance then gid',
            'peer_gids':';'.join(peers.gid),'focus_priority':float(focus.priority_score),
            'peer_priority_median':float(peers.priority_score.median()) if len(peers) else 0.,
            'median_defined':bool(len(peers))}
        return [self.registry.add('peers',[gid],values,[focus['_record_id']]+peers._record_id.tolist())]

    def simulate_node_removal(self, gid):
        before = self.data.graph
        after = before.copy()
        after.remove_node(gid)
        sources = sorted(set(before.predecessors(gid))-{gid},key=int)[:10]
        targets = sorted(set(before.successors(gid))-{gid},key=int)[:10]
        def reachable(graph):
            count = 0
            for src in sources:
                self.remaining()
                reached = nx.single_source_shortest_path_length(graph,src,cutoff=4)
                count += sum(dst!=src and dst in reached for dst in targets)
            return count
        pre, post = reachable(before), reachable(after)
        values = {'weak_components_before':nx.number_weakly_connected_components(before),
            'weak_components_after':nx.number_weakly_connected_components(after),
            'reachable_pairs_before':pre,'reachable_pairs_after':post,'lost_reachable_pairs':pre-post,
            'sampled_pairs':sum(s!=t for s in sources for t in targets),'max_hops':4,
            'source_gids':';'.join(sources),'target_gids':';'.join(targets),'economic_effect_proven':False}
        return [self.registry.add('node_removal',[gid],values,
            self.data.edges._record_id.tolist()+self.data.nodes._record_id.tolist())]
