"""Pinned, single-thread DuckDB baseline over independently parsed source text."""
import argparse
import csv
from decimal import Decimal
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import duckdb
from import_dataset import fingerprint
from reference import WIDTHS, iso, parse_iso

VERSION = '1.5.6'
CONTRACTS = ('ESZ26', 'NQZ26')
UINT64_MAX = 2**64 - 1


def connect(path, read_only=True):
    if duckdb.__version__ != VERSION:
        raise RuntimeError(f'Expected DuckDB {VERSION}, found {duckdb.__version__}')
    connection = duckdb.connect(str(path), read_only=read_only)
    connection.execute("SET threads=1")
    connection.execute("SET memory_limit='512MB'")
    connection.execute("SET preserve_insertion_order=true")
    return connection


def prepare(database, source_dir, data, lattice, contracts=CONTRACTS):
    database = Path(database).resolve()
    if database.exists():
        raise FileExistsError(f'Database exists: {database}')
    database.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.duckdb-staging-', dir=database.parent))
    temporary = staging / 'baseline.duckdb'
    metadata = {}
    try:
        with connect(temporary, read_only=False) as connection:
            connection.execute('CREATE TABLE provenance(contract VARCHAR PRIMARY KEY, metadata VARCHAR)')
            for contract in contracts:
                if contract not in CONTRACTS:
                    raise ValueError('Unsupported contract')
                manifest = json.loads((Path(data) / contract / 'manifest.json').read_text(encoding='utf-8'))
                subprocess.run([str(lattice), 'verify', '--data', str(data), '--contract', contract],
                               check=True, capture_output=True, text=True)
                source = Path(source_dir) / manifest['source_name']
                if source.resolve().parent != Path(source_dir).resolve():
                    raise ValueError('Source name escapes source directory')
                digest, identity = fingerprint(source)
                if digest != manifest['source_sha256'] or identity[0] != manifest['source_bytes']:
                    raise ValueError('Source does not match published lattice manifest')
                start = time.perf_counter()
                connection.execute(f'''CREATE TABLE {contract}(
                    source_row UBIGINT, timestamp_ns BIGINT, last_ticks BIGINT CHECK(last_ticks>0),
                    bid_ticks BIGINT, ask_ticks BIGINT,
                    volume_contracts UBIGINT CHECK(volume_contracts>0))''')
                # Serial CSV input + row_number preserves physical line order.
                # Parse only whole seconds with strptime, then add the exact 100ns fraction.
                connection.execute(f'''INSERT INTO {contract}
                    SELECT row_number() OVER ()::UBIGINT,
                        epoch_ns(strptime(substr(ts,1,15),'%Y%m%d %H%M%S'))
                          + substr(ts,17,7)::BIGINT * 100,
                        (last::DECIMAL(38,18)*4)::BIGINT,
                        (bid::DECIMAL(38,18)*4)::BIGINT,
                        (ask::DECIMAL(38,18)*4)::BIGINT, volume::UBIGINT
                    FROM read_csv(?, delim=';', header=false, auto_detect=false,
                        parallel=false, columns={{'ts':'VARCHAR','last':'VARCHAR',
                        'bid':'VARCHAR','ask':'VARCHAR','volume':'VARCHAR'}})''', [str(source)])
                count, first, last = connection.execute(
                    f'SELECT count(*), min(timestamp_ns), max(timestamp_ns) FROM {contract}').fetchone()
                if (count != manifest['record_count'] or first != parse_iso(manifest['first_timestamp_utc'])
                        or last != parse_iso(manifest['last_timestamp_utc'])):
                    raise ValueError('Independent import count/timestamps differ from lattice')
                final_digest, final_identity = fingerprint(source)
                if digest != final_digest or identity != final_identity:
                    raise ValueError('Source changed during DuckDB import')
                metadata[contract] = {'schema_version': 1, 'duckdb_version': VERSION,
                                      'source_sha256': digest, 'record_count': count,
                                      'first_timestamp_utc': iso(first), 'last_timestamp_utc': iso(last),
                                      'import_seconds': time.perf_counter() - start,
                                      'input': 'original NinjaTrader text', 'threads': 1}
                connection.execute('INSERT INTO provenance VALUES (?,?)',
                                   [contract, json.dumps(metadata[contract])])
                print(f'Prepared {contract}: {count:,} records', file=sys.stderr, flush=True)
            connection.execute('CHECKPOINT')
        if database.exists():
            raise FileExistsError('Destination appeared during preparation')
        temporary.rename(database)
        return metadata
    finally:
        shutil.rmtree(staging)


def checked_volume(value):
    if not 0 <= value <= UINT64_MAX:
        raise OverflowError('uint64 volume overflow')
    return value


def vwap(numerator, volume):
    if not volume:
        return ''
    result = numerator / float(volume)
    if not math.isfinite(result):
        raise OverflowError('VWAP overflow')
    return format(result, '.17g')


def price(ticks):
    return '' if ticks is None else f'{Decimal(ticks)/4:.2f}'


def query(connection, contract, kind, begin, end, bucket=None):
    if contract not in CONTRACTS or kind not in ('summary', 'bars', 'flow'):
        raise ValueError('Unsupported contract/query')
    if begin >= end:
        raise ValueError('Begin must precede end')
    if (kind == 'bars' and bucket not in WIDTHS) or (kind != 'bars' and bucket is not None):
        raise ValueError('Invalid bucket for query')
    selection = f'FROM {contract} WHERE timestamp_ns>=? AND timestamp_ns<?'
    parameters = [begin, end]
    numerator = 'fsum((last_ticks::DOUBLE/4.0)*volume_contracts::DOUBLE)'
    if kind == 'summary':
        count, volume, low, high, total = connection.execute(
            f'SELECT count(*), coalesce(sum(volume_contracts),0), min(last_ticks), max(last_ticks), {numerator} {selection}',
            parameters).fetchone()
        checked_volume(volume)
        return [dict(contract=contract, record_count=count, volume_contracts=volume,
                     min_price=price(low), max_price=price(high), vwap=vwap(total, volume))]
    if kind == 'bars':
        # The pair guarantees OHLC tie-breaking by the original source line.
        values = connection.execute(f'''SELECT (timestamp_ns // {WIDTHS[bucket]}) * {WIDTHS[bucket]} AS bucket,
            arg_min(last_ticks, (timestamp_ns,source_row)), max(last_ticks), min(last_ticks),
            arg_max(last_ticks, (timestamp_ns,source_row)), sum(volume_contracts), count(*), {numerator}
            {selection} GROUP BY bucket ORDER BY bucket''', parameters).fetchall()
        result = []
        for start, opening, high, low, close, volume, count, total in values:
            # lattice accumulates each bucket independently, not a whole-query volume.
            checked_volume(volume)
            result.append(dict(bucket_start_utc=iso(start), open=price(opening), high=price(high),
                               low=price(low), close=price(close), volume_contracts=volume,
                               record_count=count, vwap=vwap(total, volume)))
        return result
    values = connection.execute(f'''WITH selected AS (
        SELECT volume_contracts, CASE WHEN bid_ticks>0 AND bid_ticks<ask_ticks AND last_ticks=ask_ticks THEN 0
            WHEN bid_ticks>0 AND bid_ticks<ask_ticks AND last_ticks=bid_ticks THEN 1 ELSE 2 END AS side
        {selection}) SELECT count(*) FILTER(WHERE side=0), count(*) FILTER(WHERE side=1), count(*) FILTER(WHERE side=2),
        coalesce(sum(volume_contracts) FILTER(WHERE side=0),0),
        coalesce(sum(volume_contracts) FILTER(WHERE side=1),0),
        coalesce(sum(volume_contracts) FILTER(WHERE side=2),0) FROM selected''', parameters).fetchone()
    buy, sell, unknown, buy_v, sell_v, unknown_v = values
    records = buy + sell + unknown
    volume = checked_volume(buy_v + sell_v + unknown_v)
    return [dict(contract=contract, estimated_buy_records=buy, estimated_sell_records=sell,
                 unknown_records=unknown, estimated_buy_volume=buy_v, estimated_sell_volume=sell_v,
                 unknown_volume=unknown_v,
                 classified_record_fraction=format((buy+sell)/records, '.17g') if records else '',
                 classified_volume_fraction=format((buy_v+sell_v)/volume, '.17g') if volume else '')]


BAR_FIELDS = ['bucket_start_utc', 'open', 'high', 'low', 'close', 'volume_contracts', 'record_count', 'vwap']


def write_csv(rows, kind, stream):
    fields = list(rows[0]) if rows else BAR_FIELDS
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    stream.flush()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest='command', required=True)
    build = commands.add_parser('prepare')
    build.add_argument('--database', type=Path, required=True)
    build.add_argument('--source-dir', type=Path, required=True)
    build.add_argument('--data', type=Path, required=True)
    build.add_argument('--lattice', type=Path, required=True)
    scan = commands.add_parser('query')
    scan.add_argument('kind', choices=('summary', 'bars', 'flow'))
    scan.add_argument('--database', type=Path, required=True)
    scan.add_argument('--contract', choices=CONTRACTS, required=True)
    scan.add_argument('--from', dest='begin', required=True)
    scan.add_argument('--to', dest='end', required=True)
    scan.add_argument('--bucket', choices=WIDTHS)
    scan.add_argument('--stats', type=Path)
    args = p.parse_args()
    try:
        if args.command == 'prepare':
            print(json.dumps(prepare(args.database, args.source_dir, args.data, args.lattice.resolve()), indent=2))
        else:
            if args.stats and args.stats.exists():
                raise FileExistsError('Stats destination exists')
            begin, end = parse_iso(args.begin), parse_iso(args.end)
            with connect(args.database) as connection:
                start = time.perf_counter()
                rows = query(connection, args.contract, args.kind, begin, end, args.bucket)
                write_csv(rows, args.kind, sys.stdout)
                elapsed = time.perf_counter() - start
            if args.stats:
                temporary = Path(str(args.stats)+'.tmp')
                created = False
                try:
                    with temporary.open('x', encoding='utf-8') as result:
                        created = True
                        json.dump({'query_seconds': elapsed, 'threads': 1, 'duckdb_version': VERSION}, result)
                        result.write('\n')
                    temporary.rename(args.stats)
                finally:
                    if created:
                        temporary.unlink(missing_ok=True)
    except OverflowError as error:
        p.exit(5, f'{error}\n')
    except (OSError, ValueError, RuntimeError, duckdb.Error, subprocess.CalledProcessError) as error:
        p.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
