"""Correctness-gated, rotating fresh-process CLI benchmark; OS cache uncontrolled."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import random
import re
import statistics
import subprocess
import sys
import time

from duck_baseline import CONTRACTS, VERSION, connect, query
from import_dataset import fingerprint
from reference import compare_rows, evaluate, parse_iso

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = ('row', 'column', 'pruned', 'duckdb')


def workloads(path):
    document = json.loads(path.read_text(encoding='utf-8'))
    if document['schema_version'] != 1:
        raise ValueError('Unsupported workloads schema')
    result = []
    for contract in document['contracts']:
        if contract not in CONTRACTS:
            raise ValueError('Unsupported contract')
        for window in document['windows']:
            if parse_iso(window['from']) >= parse_iso(window['to']):
                raise ValueError('Invalid workload range')
            for spec in document['queries']:
                if spec['kind'] not in ('summary', 'bars', 'flow'):
                    raise ValueError('Invalid workload query')
                identifier = f"{contract}/{window['name']}/{spec['kind']}/{spec.get('bucket','-')}"
                result.append(dict(id=identifier, contract=contract, window=window['name'],
                                   begin=window['from'], end=window['to'], **spec))
    if not result or len({w['id'] for w in result}) != len(result):
        raise ValueError('Empty/duplicate workload IDs')
    return result


def run(args, spec, variant, stats_path):
    if variant == 'duckdb':
        command = [sys.executable, str(ROOT/'tools/duck_baseline.py'), 'query', spec['kind'],
                   '--database', str(args.database)]
    else:
        command = [str(args.lattice), 'query', spec['kind'], '--data', str(args.row_data if variant=='row' else args.data), '--scan-mode', variant]
    command += ['--contract', spec['contract'], '--from', spec['begin'], '--to', spec['end'], '--stats', str(stats_path)]
    if spec['kind'] == 'bars':
        command += ['--bucket', spec['bucket']]
    start = time.perf_counter()
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    wall = time.perf_counter()-start
    observed = list(csv.DictReader(io.StringIO(completed.stdout)))
    stats = json.loads(stats_path.read_text())
    return wall, stats, observed, hashlib.sha256(completed.stdout.encode('utf-8')).hexdigest()


def environment(lattice):
    result = {'platform': platform.platform(), 'processor': platform.processor(),
              'logical_cpus': os.cpu_count(), 'python': sys.version,
              'duckdb': VERSION, 'duckdb_threads': 1, 'duckdb_memory_limit': '512MB',
              'lattice_version': subprocess.check_output([str(lattice), '--version'], text=True).strip(),
              'lattice_sha256': fingerprint(lattice)[0], 'compiler': {}}
    cache_root = lattice.parent.parent if lattice.parent.name == 'Release' else lattice.parent
    for file in cache_root.glob('CMakeFiles/*/CMakeCXXCompiler.cmake'):
        text = file.read_text(encoding='utf-8')
        for key in ('CMAKE_CXX_COMPILER', 'CMAKE_CXX_COMPILER_ID', 'CMAKE_CXX_COMPILER_VERSION'):
            match = re.search(r'set\('+key+r' "([^"]*)"\)', text)
            if match:
                result['compiler'][key] = match.group(1)
    cache = cache_root / 'CMakeCache.txt'
    if cache.exists():
        result['cmake_cache_sha256'] = fingerprint(cache)[0]
        result['release_flags'] = [line for line in cache.read_text().splitlines()
                                   if line.startswith('CMAKE_CXX_FLAGS_RELEASE:') or line.startswith('CMAKE_GENERATOR:')]
    result['source_files_sha256'] = {
        str(p.relative_to(ROOT)).replace('\\', '/'): fingerprint(p)[0]
        for p in [ROOT/'src/main.cpp', ROOT/'CMakeLists.txt', ROOT/'tools/benchmark.py',
                  ROOT/'tools/duck_baseline.py', ROOT/'tools/reference.py', ROOT/'tools/requirements-bench.txt']}
    result['source_files_sha256']['tools/import_dataset.py'] = fingerprint(ROOT/'tools/import_dataset.py')[0]
    revision = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--verify', 'HEAD'], capture_output=True, text=True)
    dirty = subprocess.run(['git', '-C', str(ROOT), 'status', '--porcelain'], capture_output=True, text=True)
    result['git_revision'] = revision.stdout.strip() if revision.returncode == 0 else None
    result['git_dirty'] = bool(dirty.stdout.strip()) if dirty.returncode == 0 else None
    result['git_note'] = 'Working tree source hashes identify this run; repository has no commit' if revision.returncode else 'Revision plus working tree source hashes identify this run'
    if os.name == 'nt':
        import ctypes
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
            result['cpu_model'] = winreg.QueryValueEx(key, 'ProcessorNameString')[0].strip()
        installed_kib = ctypes.c_ulonglong()
        if ctypes.windll.kernel32.GetPhysicallyInstalledSystemMemory(ctypes.byref(installed_kib)):
            result['installed_ram_bytes'] = installed_kib.value*1024
    return result


def execute(args):
    specs = workloads(args.workloads)
    args.output.mkdir(parents=True, exist_ok=False)
    stat_dir = args.output / 'query-stats'
    stat_dir.mkdir()
    metadata = {'schema_version': 1, 'status': 'running', 'started_at_utc': datetime.now(timezone.utc).isoformat(),
                'environment': environment(args.lattice), 'warmups': args.warmups, 'repetitions': args.repetitions,
                'seed': 731, 'cache_policy': 'Two or more warmups; OS cache uncontrolled and not cleared; no hot/cold guarantee',
                'lifecycle': 'fresh process and DuckDB connection per invocation',
                'headline_clock': 'subprocess start through exit, stdout capture/decoding and stats publication',
                'internal_clocks': {'lattice': 'manifest/metadata validation, payload scan/CRC, aggregation, CSV flush',
                                    'duckdb': 'SQL planning/execution, fetch, Python CSV serialization/flush; excludes connection/settings'},
                'workloads_sha256': fingerprint(args.workloads)[0], 'database_sha256': fingerprint(args.database)[0],
                'database_bytes': args.database.stat().st_size, 'duckdb_compression': 'default automatic',
                'contracts': {}, 'workloads': specs}
    metadata_file = args.output / 'metadata.json'
    metadata_file.write_text(json.dumps(metadata, indent=2)+'\n', encoding='utf-8')
    print('Checking provenance and full-text Decimal summaries...', flush=True)
    # All preflight work is excluded from timing. Source files are required for this correctness gate.
    with connect(args.database) as connection:
        for contract in sorted({w['contract'] for w in specs}):
            manifest = json.loads((args.data/contract/'manifest.json').read_text())
            provenance = connection.execute('SELECT metadata FROM provenance WHERE contract=?', [contract]).fetchone()
            if not provenance:
                raise ValueError('Missing DuckDB provenance')
            provenance = json.loads(provenance[0])
            source = args.source_dir / manifest['source_name']
            digest, identity = fingerprint(source)
            if digest != manifest['source_sha256'] or digest != provenance['source_sha256']:
                raise ValueError('Source hashes differ across engines')
            subprocess.run([str(args.lattice), 'verify', '--data', str(args.data), '--contract', contract],
                           check=True, capture_output=True, text=True)
            row_manifest = json.loads((args.row_data/contract/'manifest.json').read_text())
            if (row_manifest['storage_layout']!='row' or row_manifest['source_sha256']!=digest or
                    row_manifest['record_count']!=manifest['record_count'] or
                    row_manifest['parent_manifest_sha256']!=fingerprint(args.data/contract/'manifest.json')[0] or
                    set(row_manifest['parent_partition_sha256'])!=set(manifest['partitions'])):
                raise ValueError('Row store provenance differs from column source')
            for name, checksum in row_manifest['parent_partition_sha256'].items():
                if fingerprint(args.data/contract/name)[0]!=checksum:
                    raise ValueError('Row parent partition fingerprint differs')
            subprocess.run([str(args.lattice), 'verify', '--data', str(args.row_data), '--contract', contract],
                           check=True, capture_output=True, text=True)
            begin, end = parse_iso('1970-01-01T00:00:00Z'), parse_iso('2100-12-31T23:59:59.999999999Z')
            reference = evaluate(source, contract, 'summary', begin, end)
            compare_rows(query(connection, contract, 'summary', begin, end), reference)
            if fingerprint(source) != (digest, identity):
                raise ValueError('Source changed during full-text reference check')
            metadata['contracts'][contract] = {'source_sha256': digest, 'source_bytes': identity[0],
                'full_text_decimal_summary': reference,
                'lattice_payload_bytes': sum(p.stat().st_size for p in (args.data/contract).glob('*.lmc')),
                'lattice_row_payload_bytes': sum(p.stat().st_size for p in (args.row_data/contract).glob('*.lmc')),
                'row_parent_manifest_sha256': row_manifest['parent_manifest_sha256'],
                'row_parent_partition_sha256': row_manifest['parent_partition_sha256'],
                'row_manifest_sha256': fingerprint(args.row_data/contract/'manifest.json')[0],
                'row_partition_sha256': {p.name: fingerprint(p)[0] for p in (args.row_data/contract).glob('*.lmc')},
                'duckdb_table_schema': connection.execute(f'DESCRIBE {contract}').fetchall()}
            print(f'Full-text Decimal reference passed: {contract}', flush=True)
        expected = {}
        for index, spec in enumerate(specs):
            rows = query(connection, spec['contract'], spec['kind'], parse_iso(spec['begin']), parse_iso(spec['end']), spec.get('bucket'))
            expected[spec['id']] = rows
            count = connection.execute(f"SELECT count(*) FROM {spec['contract']} WHERE timestamp_ns>=? AND timestamp_ns<?",
                                       [parse_iso(spec['begin']), parse_iso(spec['end'])]).fetchone()[0]
            spec['selected_records'] = count
            spec['output_rows'] = len(rows)
            for variant in ('row', 'column', 'pruned'):
                _, stats, observed, _ = run(args, spec, variant, stat_dir/f'gate-{index}-{variant}.json')
                compare_rows(observed, rows)
                if stats['rows_selected'] != count:
                    raise AssertionError('Selected record count differs')
            print(f'Correctness {index+1}/{len(specs)}: {spec["id"]} ({count:,} records)', flush=True)
    metadata_file.write_text(json.dumps(metadata, indent=2, default=str)+'\n', encoding='utf-8')
    fields = ['workload', 'variant', 'phase', 'round', 'position', 'process_seconds', 'query_seconds',
              'bytes_requested', 'blocks_examined', 'blocks_skipped', 'rows_decoded', 'rows_selected', 'output_rows', 'output_sha256']
    measured = {}
    position = 0
    with (args.output/'runs.csv').open('x', newline='', encoding='utf-8') as raw:
        writer = csv.DictWriter(raw, fieldnames=fields)
        writer.writeheader()
        for round_number in range(args.warmups+args.repetitions):
            phase = 'warmup' if round_number<args.warmups else 'measured'
            order = list(enumerate(specs))
            random.Random(731+round_number).shuffle(order)
            for index, spec in order:
                shift = (round_number+index)%len(VARIANTS)
                variants = VARIANTS[shift:]+VARIANTS[:shift]
                for variant in variants:
                    position += 1
                    wall, stats, rows, digest = run(args, spec, variant, stat_dir/f'run-{position}.json')
                    compare_rows(rows, expected[spec['id']])  # Outside both recorded clocks.
                    record = {'workload': spec['id'], 'variant': variant, 'phase': phase,
                              'round': round_number+1, 'position': position, 'process_seconds': wall,
                              'query_seconds': stats.get('query_seconds', stats.get('elapsed_seconds')),
                              'output_rows': len(rows), 'output_sha256': digest}
                    for key in fields[7:12]:
                        record[key] = stats.get(key, '')
                    writer.writerow(record)
                    raw.flush()
                    if phase == 'measured':
                        measured.setdefault((spec['id'], variant), []).append(record)
            print(f'{phase} round {round_number+1}/{args.warmups+args.repetitions} complete ({position} invocations)', flush=True)
    summaries = []
    for (workload, variant), records in measured.items():
        values = [r['process_seconds'] for r in records]
        quartiles = statistics.quantiles(values, n=4, method='inclusive')
        summaries.append({'workload': workload, 'variant': variant, 'samples': len(values),
                          'process_median_seconds': statistics.median(values), 'process_min_seconds': min(values),
                          'process_max_seconds': max(values), 'process_iqr_seconds': quartiles[2]-quartiles[0],
                          'query_median_seconds': statistics.median(r['query_seconds'] for r in records),
                          'bytes_requested': records[0]['bytes_requested'], 'blocks_skipped': records[0]['blocks_skipped']})
    with (args.output/'summary.csv').open('x', newline='', encoding='utf-8') as summary:
        writer = csv.DictWriter(summary, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(sorted(summaries, key=lambda row: (row['workload'], row['variant'])))
    metadata['status'] = 'complete'
    metadata['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    metadata['measured_invocations'] = len(specs)*len(VARIANTS)*args.repetitions
    metadata_file.write_text(json.dumps(metadata, indent=2, default=str)+'\n', encoding='utf-8')
    print(f'Complete: {args.output}', flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--lattice', type=Path, required=True)
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--row-data', type=Path, required=True)
    p.add_argument('--database', type=Path, required=True)
    p.add_argument('--source-dir', type=Path, required=True)
    p.add_argument('--workloads', type=Path, default=ROOT/'bench/workloads.json')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--warmups', type=int, default=2)
    p.add_argument('--repetitions', type=int, default=7)
    args = p.parse_args()
    if args.warmups<2 or args.repetitions<3:
        p.error('Require at least two warmups and three measured repetitions')
    for name in ('lattice', 'data', 'row_data', 'database', 'source_dir', 'workloads', 'output'):
        setattr(args, name, getattr(args, name).resolve())
    execute(args)


if __name__ == '__main__':
    main()
