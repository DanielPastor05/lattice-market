"""Verify imported real data and compare a one-minute window to text/Decimal.

This is a correctness check, not a performance benchmark. Run after both imports.
"""
import argparse
import csv
from decimal import Decimal
import io
import json
from pathlib import Path
import subprocess
from reference import evaluate, parse_iso, parse_lines


def check(lattice, data, source_dir):
    report = {'window_from_utc': '2026-09-10T14:00:00Z',
              'window_to_utc': '2026-09-10T14:01:00Z', 'contracts': {}}
    begin, end = report['window_from_utc'], report['window_to_utc']
    for contract, label in [('ESZ26', 'ES'), ('NQZ26', 'NQ')]:
        source = source_dir / f'{label} 12-26.Last.txt'
        with source.open(encoding='ascii') as lines:
            # Fixed-width source timestamps have chronological lexical order.
            # Read the entire text source; only the selected window is retained.
            events = list(parse_lines(line for line in lines
                                     if '20260910 140000 0000000' <= line[:23] < '20260910 140100 0000000'))
        if not events:
            raise AssertionError(f'No reference records for {contract}')
        verify = json.loads(subprocess.check_output([
            str(lattice), 'verify', '--data', str(data), '--contract', contract], text=True))
        checked = {}
        for kind in ['summary', 'bars', 'flow']:
            expected = evaluate(source, contract, kind, parse_iso(begin), parse_iso(end), '1s', events)
            for mode in ['column', 'pruned']:
                command = [str(lattice), 'query', kind, '--data', str(data), '--contract', contract,
                           '--from', begin, '--to', end, '--scan-mode', mode]
                if kind == 'bars':
                    command += ['--bucket', '1s']
                actual = list(csv.DictReader(io.StringIO(subprocess.check_output(command, text=True))))
                if len(actual) != len(expected):
                    raise AssertionError(f'{contract}/{kind}/{mode}: row count mismatch')
                for observed, wanted in zip(actual, expected):
                    for key, value in wanted.items():
                        if key == 'vwap' or key.endswith('_fraction'):
                            if value == '':
                                if observed[key] != '':
                                    raise AssertionError(f'{key}: expected empty')
                            else:
                                tolerance = max(Decimal('1e-8') if key == 'vwap' else Decimal('1e-12'), abs(value) * Decimal('1e-12'))
                                if abs(Decimal(observed[key]) - value) > tolerance:
                                    raise AssertionError(f'{contract}/{kind}/{mode}: {key} mismatch')
                        elif isinstance(value, (int, Decimal)):
                            if Decimal(observed[key]) != value:
                                raise AssertionError(f'{key}: exact mismatch')
                        elif observed[key] != value:
                            raise AssertionError(f'{key}: string mismatch')
                checked[f'{kind}/{mode}'] = 'passed'
        manifest = json.loads((data / contract / 'manifest.json').read_text())
        if verify['verified_records'] != manifest['record_count']:
            raise AssertionError('Manifest count mismatch')
        report['contracts'][contract] = {
            'verified_records': verify['verified_records'], 'reference_window_records': len(events),
            'source_sha256': manifest['source_sha256'], 'comparisons': checked,
        }
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--lattice', type=Path, required=True)
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--source-dir', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    args = p.parse_args()
    report = check(args.lattice.resolve(), args.data.resolve(), args.source_dir.resolve())
    with args.report.open('x', encoding='utf-8') as result:
        json.dump(report, result, indent=2)
        result.write('\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
