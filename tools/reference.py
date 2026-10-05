"""Independent, streaming reference using exact Decimal arithmetic."""
import argparse
import csv
from datetime import date
from decimal import Decimal, localcontext
from pathlib import Path
import sys

SECOND = 1_000_000_000
DAY = 86400 * SECOND
EPOCH = date(1970, 1, 1)
WIDTHS = {'1s': SECOND, '1m': 60 * SECOND, '5m': 300 * SECOND,
          '1h': 3600 * SECOND, '1d': DAY}


def parse_iso(text):
    if not text.endswith('Z'):
        raise ValueError('UTC required')
    day = date.fromisoformat(text[:10])
    hours, minutes, seconds = text[11:-1].split(':')
    seconds, _, fraction = seconds.partition('.')
    return ((day - EPOCH).days * DAY +
            (int(hours) * 3600 + int(minutes) * 60 + int(seconds)) * SECOND +
            int((fraction or '0').ljust(9, '0')))


def iso(ns):
    from datetime import timedelta
    d = EPOCH + timedelta(days=ns // DAY)
    seconds, fraction = divmod(ns % DAY, SECOND)
    h, seconds = divmod(seconds, 3600)
    m, s = divmod(seconds, 60)
    return f'{d.isoformat()}T{h:02}:{m:02}:{s:02}.{fraction:09}Z'


def ticks(path):
    with Path(path).open(encoding='ascii') as source:
        yield from parse_lines(source)


def parse_lines(lines):
    for line in lines:
        ts, last, bid, ask, volume = line.strip().split(';')
        date_part, clock, fraction = ts.split(' ')
        d = date(int(date_part[:4]), int(date_part[4:6]), int(date_part[6:]))
        ns = ((d - EPOCH).days * DAY +
              (int(clock[:2]) * 3600 + int(clock[2:4]) * 60 + int(clock[4:])) * SECOND +
              int(fraction) * 100)
        yield ns, Decimal(last), Decimal(bid), Decimal(ask), int(volume)


def evaluate(path, contract, kind, begin, end, bucket='1m', events=None):
    # Enough precision for int64 price × uint64 volume × uint64 record count.
    # VWAP division is rounded at 80 digits; integer products/sums remain exact.
    with localcontext() as context:
        context.prec = 80
        return _evaluate(path, contract, kind, begin, end, bucket, events)


def _evaluate(path, contract, kind, begin, end, bucket, events):
    def new():
        return {'count': 0, 'volume': 0, 'numerator': Decimal(0)}

    def append(a, price, volume):
        if not a['count']:
            a.update(open=price, high=price, low=price)
        a['count'] += 1
        a['volume'] += volume
        a['numerator'] += price * volume
        a['high'], a['low'], a['close'] = max(a['high'], price), min(a['low'], price), price

    a = new()
    current = None
    buy_sell_unknown = [[0, 0] for _ in range(3)]
    bars = []

    def emit_bar():
        if a['count']:
            bars.append(dict(bucket_start_utc=iso(current), open=a['open'], high=a['high'],
                             low=a['low'], close=a['close'], volume_contracts=a['volume'],
                             record_count=a['count'], vwap=a['numerator'] / a['volume']))

    for ns, price, bid, ask, volume in ticks(path) if events is None else events:
        if not begin <= ns < end:
            continue
        if kind == 'flow':
            side = 0 if 0 < bid < ask and price == ask else 1 if 0 < bid < ask and price == bid else 2
            buy_sell_unknown[side][0] += 1
            buy_sell_unknown[side][1] += volume
        else:
            if kind == 'bars':
                b = ns // WIDTHS[bucket] * WIDTHS[bucket]
                if current != b:
                    emit_bar()
                    a, current = new(), b
            append(a, price, volume)
    if kind == 'bars':
        emit_bar()
        return bars
    if kind == 'summary':
        return [dict(contract=contract, record_count=a['count'], volume_contracts=a['volume'],
                     min_price=a.get('low', ''), max_price=a.get('high', ''),
                     vwap=a['numerator'] / a['volume'] if a['volume'] else '')]
    counts, volumes = zip(*buy_sell_unknown)
    return [dict(contract=contract, estimated_buy_records=counts[0], estimated_sell_records=counts[1],
                 unknown_records=counts[2], estimated_buy_volume=volumes[0], estimated_sell_volume=volumes[1],
                 unknown_volume=volumes[2],
                 classified_record_fraction=Decimal(sum(counts[:2])) / sum(counts) if sum(counts) else '',
                 classified_volume_fraction=Decimal(sum(volumes[:2])) / sum(volumes) if sum(volumes) else '')]


def compare_rows(actual, expected):
    """Exact integer/string comparison, with documented float tolerances."""
    if len(actual) != len(expected):
        raise AssertionError(f'Output row count mismatch: {len(actual)} != {len(expected)}')
    for row_number, (observed, wanted) in enumerate(zip(actual, expected), 1):
        if set(observed) != set(wanted):
            raise AssertionError('CSV columns differ')
        for key, value in wanted.items():
            value, got = str(value), str(observed[key])
            if key in ('contract', 'bucket_start_utc') or value == '':
                okay = got == value
            elif key == 'vwap' or key.endswith('_fraction'):
                value, got = Decimal(value), Decimal(got)
                tolerance = max(Decimal('1e-8') if key == 'vwap' else Decimal('1e-12'), abs(value)*Decimal('1e-12'))
                okay = got.is_finite() and value.is_finite() and abs(got-value) <= tolerance
            else:
                okay = Decimal(got) == Decimal(value)
            if not okay:
                raise AssertionError(f'Row {row_number}, {key}: {got} != {value}')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--contract', choices=['ESZ26', 'NQZ26'], required=True)
    p.add_argument('--query', choices=['summary', 'bars', 'flow'], required=True)
    p.add_argument('--from', dest='begin', required=True)
    p.add_argument('--to', dest='end', required=True)
    p.add_argument('--bucket', choices=WIDTHS, default='1m')
    args = p.parse_args()
    rows = evaluate(args.input, args.contract, args.query, parse_iso(args.begin), parse_iso(args.end), args.bucket)
    if rows:
        w = csv.DictWriter(sys.stdout, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


if __name__ == '__main__':
    main()
