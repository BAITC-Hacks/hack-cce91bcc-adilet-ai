from collections import deque


def possible_fast_volume(incoming, outgoing):
    """FIFO maximum possible matching in [0,2] days; same-day order unknown.

    Inputs are mappings from integer day to amount. Each amount is used once.
    """
    queue = deque()
    matched = 0.
    for day in sorted(set(incoming) | set(outgoing)):
        while queue and queue[0][0] < day - 2:
            queue.popleft()
        amount = incoming.get(day, 0.)
        if amount > 0:
            queue.append([day, amount])
        remaining = outgoing.get(day, 0.)
        while remaining > 0 and queue:
            take = min(remaining, queue[0][1])
            matched += take
            remaining -= take
            queue[0][1] -= take
            if queue[0][1] <= 0:
                queue.popleft()
    return matched


def temporal_features(df, tx):
    daily = tx.assign(day=tx.date.dt.day)
    def groups(column):
        grouped = daily.groupby([column, 'day']).sum_kzt.sum()
        return {gid: values.droplevel(0).to_dict() for gid, values in grouped.groupby(level=0)}
    incoming, outgoing = groups('dst'), groups('src')
    df['fast2'] = [min(1., possible_fast_volume(incoming.get(r.gid, {}), outgoing.get(r.gid, {})) / min(r.in_kzt, r.out_kzt))
                   if min(r.in_kzt, r.out_kzt) > 0 else 0. for r in df.itertuples()]
    return df
