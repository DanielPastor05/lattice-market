"""Windows committed-memory cap and peak working-set check on deterministic synthetic data."""
import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal
import io
import json
from pathlib import Path
import subprocess
import sys

from import_dataset import fingerprint
from reference import compare_rows, evaluate, parse_iso

BEGIN, END = '2026-09-09T00:00:00Z', '2026-09-10T00:00:00Z'


def probe(executable, command, limit):
    result = subprocess.run([str(executable), str(limit), subprocess.list2cmdline(command)],
                            capture_output=True, text=True, timeout=130)
    marker = next((line.removeprefix('memory_probe:') for line in result.stderr.splitlines()
                   if line.startswith('memory_probe:')), None)
    if marker is None:
        raise RuntimeError(f'No native memory counters: {result.stderr}')
    counters = json.loads(marker)
    if counters['child_exit_code'] != result.returncode or counters['peak_committed_bytes'] > limit:
        raise AssertionError('Native counters/limit inconsistent')
    return result, counters


def check(args):
    # The generator's prices and volumes repeat every 1000 records (also every 200).
    # All <=100M records land in the first minute, so each 1m bar contains all rows.
    events = []
    for i in range(1000):
        price = Decimal(40000+(i*1103515245+731)%1000)/4
        events.append((parse_iso(BEGIN)+i//2*100, price, price-Decimal('.25'), price, i%5+1))
    expected = {}
    for kind in ('summary', 'flow', 'bars'):
        rows = evaluate(None, 'ESZ26', kind, parse_iso(BEGIN), parse_iso(END), '1m', events=events)
        for row in rows:
            for key in row:
                if key == 'record_count' or key == 'volume_contracts' or key.endswith('_records') or key.endswith('_volume'):
                    row[key] *= args.rows//1000
        expected[kind] = rows
    limit = 256*1024*1024
    # Verify the helper really enforces a cap before trusting a successful query.
    denied, denied_counters = probe(args.probe, [sys.executable, '-c',
        'try:\n b=bytearray(128*1024*1024)\nexcept MemoryError:\n print("cap-enforced")\n raise SystemExit(17)'], 64*1024*1024)
    if denied.returncode != 17 or 'cap-enforced' not in denied.stdout:
        raise AssertionError(f'Cap enforcement canary failed: {denied.stderr}')
    report = dict(status='passed', rows=args.rows, limit_committed_bytes=limit,
                  source_sha256=args.source_sha256, record_payload_bytes=args.rows*48,
                  metric='Windows Job Object process committed memory; peak working set recorded separately',
                  limitation='Windows evidence only; no Linux RSS/rlimit or ASan-in-cap claim',
                  binary_sha256=fingerprint(args.lattice)[0], probe_sha256=fingerprint(args.probe)[0],
                  tool_sha256=fingerprint(Path(__file__))[0], cap_canary=denied_counters,
                  generator_sha256=fingerprint(Path(__file__).with_name('generate_ticks.py'))[0],
                  executed_at_utc=datetime.now(timezone.utc).isoformat(), runs=[])
    for mode in ('column', 'pruned', 'row'):
        data = args.row_data if mode=='row' else args.data
        manifest_path = data/'ESZ26'/'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        if manifest['record_count'] != args.rows or manifest['source_sha256'] != args.source_sha256:
            raise ValueError('Stress fixture count/source fingerprint differs')
        partition_file_bytes = sum(path.stat().st_size for path in (data/'ESZ26').glob('*.lmc'))
        if args.rows*48 <= 1024**3:
            raise ValueError('Require more than 1 GiB of uncompressed record payload')
        for kind in ('summary', 'flow', 'bars'):
            command = [str(args.lattice), 'query', kind, '--data', str(data), '--contract', 'ESZ26',
                       '--scan-mode', mode, '--from', BEGIN, '--to', END]
            if kind == 'bars':
                command += ['--bucket', '1m']
            result, counters = probe(args.probe, command, limit)
            if result.returncode:
                raise RuntimeError(result.stderr)
            observed = list(csv.DictReader(io.StringIO(result.stdout)))
            compare_rows(observed, expected[kind])
            report['runs'].append(dict(mode=mode, query=kind, partition_file_bytes=partition_file_bytes,
                manifest_sha256=fingerprint(manifest_path)[0], counters=counters, result=observed))
            print(f'{mode}/{kind}: peak commit {counters["peak_committed_bytes"]:,}, '
                  f'peak working set {counters["peak_working_set_bytes"]:,} bytes', flush=True)
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('lattice', 'probe', 'data', 'row-data', 'report'):
        p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--rows', type=int, required=True)
    p.add_argument('--source-sha256', required=True)
    args = p.parse_args()
    if not 1000 <= args.rows <= 100_000_000 or args.rows%1000:
        p.error('Rows must be a multiple of 1000, at most 100M')
    if args.report.exists():
        p.error('Report already exists')
    for name in ('lattice', 'probe', 'data', 'row_data'):
        setattr(args, name, getattr(args, name).resolve())
    report = check(args)
    with args.report.open('x', encoding='utf-8') as output:
        output.write(json.dumps(report, indent=2, default=str)+'\n')
