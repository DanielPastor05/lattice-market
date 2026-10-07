"""Prepare synthetic size series and reuse the four-variant correctness-gated benchmark."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

from benchmark import execute
from duck_baseline import prepare
from generate_ticks import generate
from import_dataset import fingerprint, publish, publish_rows
from reference import iso, parse_iso

BEGIN = '2026-09-09T00:00:00Z'


def workload_document(rows):
    if not 1000 <= rows <= 100_000_000 or rows % 1000:
        raise ValueError('Size must be a multiple of 1000 in 1000..100M')
    return dict(schema_version=1, contracts=['ESZ26'], windows=[
        dict(name='full', expected_records=rows, **{'from': BEGIN, 'to': '2026-09-10T00:00:00Z'}),
        dict(name='prefix_1pct', expected_records=rows//100,
             **{'from': BEGIN, 'to': iso(parse_iso(BEGIN)+(rows//100//2)*100)})],
        queries=[dict(kind='summary'), dict(kind='flow'), dict(kind='bars', bucket='1m')])


def execute_series(args):
    if len(set(args.sizes)) != len(args.sizes) or not args.sizes:
        raise ValueError('Sizes must be nonempty and unique')
    documents = [workload_document(rows) for rows in args.sizes]
    args.work_dir.mkdir(parents=True, exist_ok=False)
    args.output.mkdir(parents=True, exist_ok=False)
    report = dict(schema_version=1, status='running', dataset_kind='synthetic',
        started_at_utc=datetime.now(timezone.utc).isoformat(),
        limitation='Representative ESZ26 schema only; duplicate timestamp pairs, periodic prices/volumes, all buy-side, one 1m bar; not realistic market history',
        generator_sha256=fingerprint(Path(__file__).with_name('generate_ticks.py'))[0],
        sizes=args.sizes, runs=[])
    report_file = args.output/'series.json'
    def save():
        report_file.write_text(json.dumps(report, indent=2, default=str)+'\n', encoding='utf-8')
    save()
    for rows, document in zip(args.sizes, documents):
        root = args.work_dir/str(rows)
        root.mkdir()
        durations = {}
        def stage(name, function):
            print(f'{rows:,}: {name}', flush=True)
            start = time.perf_counter()
            result = function()
            durations[name] = time.perf_counter()-start
            return result
        source = stage('generate', lambda: generate(root/'synthetic.txt', rows))
        manifest = stage('publish_column', lambda: publish(source, 'ESZ26', root/'column', args.lattice))
        if manifest['record_count'] != rows:
            raise AssertionError('Generated/imported row count differs from requested size')
        stage('publish_row', lambda: publish_rows(root/'column', 'ESZ26', root/'row', args.lattice))
        duck = stage('prepare_duckdb', lambda: prepare(root/'baseline.duckdb', root, root/'column', args.lattice, ('ESZ26',)))
        workload_file = root/'workloads.json'
        workload_file.write_text(json.dumps(document, indent=2)+'\n', encoding='utf-8')
        output = args.output/str(rows)
        stage('benchmark_and_correctness', lambda: execute(SimpleNamespace(
            lattice=args.lattice, data=root/'column', row_data=root/'row', database=root/'baseline.duckdb',
            source_dir=root, workloads=workload_file, output=output, warmups=args.warmups,
            repetitions=args.repetitions, measure_memory=True, probe=args.probe)))
        metadata = json.loads((output/'metadata.json').read_text())
        for spec in metadata['workloads']:
            if spec['selected_records'] != (rows if spec['window']=='full' else rows//100):
                raise AssertionError('Synthetic selection count differs from frozen window')
        report['runs'].append(dict(rows=rows, source_sha256=manifest['source_sha256'],
            source_bytes=manifest['source_bytes'], stage_process_seconds=durations,
            lattice_parser_import_seconds=manifest['import_seconds'],
            duckdb_contract_prepare_seconds=duck['ESZ26']['import_seconds'],
            benchmark_preflight_seconds=metadata['preflight_seconds'],
            metadata_sha256=fingerprint(output/'metadata.json')[0],
            runs_sha256=fingerprint(output/'runs.csv')[0], summary_sha256=fingerprint(output/'summary.csv')[0],
            workloads=document))
        save()
    report.update(status='complete', finished_at_utc=datetime.now(timezone.utc).isoformat())
    save()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lattice', type=Path, required=True)
    parser.add_argument('--probe', type=Path, required=os.name=='nt')
    parser.add_argument('--work-dir', type=Path, required=True, help='New scratch directory; generated inputs stay local')
    parser.add_argument('--output', type=Path, required=True, help='New report directory')
    parser.add_argument('--sizes', type=int, nargs='+', default=[1_000_000, 10_000_000, 100_000_000])
    parser.add_argument('--warmups', type=int, default=2)
    parser.add_argument('--repetitions', type=int, default=7)
    args = parser.parse_args()
    if args.warmups<2 or args.repetitions<3:
        parser.error('Require at least two warmups and three measured repetitions')
    for name in ('lattice', 'probe', 'work_dir', 'output'):
        if getattr(args, name) is not None:
            setattr(args, name, getattr(args, name).resolve())
    execute_series(args)
