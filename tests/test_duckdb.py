"""SQL differential checks, including precision/order and immutable preparation."""
import csv
from decimal import Decimal
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import duck_baseline
import benchmark
import scale_benchmark
from check_memory import probe
from generate_ticks import generate
from types import SimpleNamespace
from import_dataset import publish, publish_rows
from reference import evaluate, parse_iso

LATTICE = Path(sys.argv.pop(1)).resolve()


class DuckTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.txt'
        self.data = self.root / 'data'
        self.database = self.root / 'test.duckdb'

    def load(self, text):
        self.source.write_text(text, encoding='ascii')
        publish(self.source, 'ESZ26', self.data, LATTICE)
        duck_baseline.prepare(self.database, self.root, self.data, LATTICE, ('ESZ26',))

    def compare(self, kind, begin, end, bucket=None):
        expected = evaluate(self.source, 'ESZ26', kind, parse_iso(begin), parse_iso(end), bucket or '1m')
        with duck_baseline.connect(self.database) as connection:
            observed = duck_baseline.query(connection, 'ESZ26', kind, parse_iso(begin), parse_iso(end), bucket)
        args = [str(LATTICE), 'query', kind, '--data', str(self.data), '--contract', 'ESZ26',
                '--from', begin, '--to', end]
        if bucket:
            args += ['--bucket', bucket]
        lattice = list(csv.DictReader(io.StringIO(subprocess.check_output(args, text=True))))
        for actual in (observed, lattice):
            self.assertEqual(len(actual), len(expected))
            for a, e in zip(actual, expected):
                self.assertEqual(set(a), set(e))
                for key, value in e.items():
                    if value == '' or isinstance(value, str):
                        self.assertEqual(a[key], value)
                    elif key == 'vwap' or key.endswith('_fraction'):
                        tolerance = max(Decimal('1e-8') if key == 'vwap' else Decimal('1e-12'), abs(value)*Decimal('1e-12'))
                        self.assertLessEqual(abs(Decimal(str(a[key]))-value), tolerance)
                    else:
                        self.assertEqual(Decimal(str(a[key])), value)

    def test_ns_duplicates_day_order_and_unknown_quotes(self):
        self.load('20260909 235959 9999999;100.25;100;100.25;2\n'
                  '20260909 235959 9999999;100.25;100;100.25;2\n'
                  '20260910 000000 0000000;100;100;100.25;3\n'
                  '20260910 000000 0000000;101;100.75;101;4\n'
                  '20260910 000000 0000001;100.25;100.25;100.25;5\n'
                  '20260910 000000 0000002;100.25;101;100;6\n'
                  '20260910 000000 0000003;100.25;-1;0;7\n')
        for begin, end in [('2026-09-09T23:59:59Z', '2026-09-10T00:01:00Z'),
                           ('2026-09-10T00:00:00.000000100Z', '2026-09-10T00:00:00.000000200Z'),
                           ('2026-09-11T00:00:00Z', '2026-09-11T01:00:00Z')]:
            for kind in ('summary', 'flow', 'bars'):
                self.compare(kind, begin, end, '1m' if kind == 'bars' else None)
        with self.assertRaises(FileExistsError):
            duck_baseline.prepare(self.database, self.root, self.data, LATTICE, ('ESZ26',))

    def test_extreme_integer_reference_and_overflow(self):
        self.load('20260909 220000 0000000;2305843009213693951.75;2305843009213693951.5;2305843009213693951.75;18446744073709551615\n'
                  '20260909 220001 0000000;100;100;100.25;1\n')
        for kind in ('summary', 'flow', 'bars'):
            self.compare(kind, '2026-09-09T22:00:00Z', '2026-09-09T22:00:00.1Z', '1s' if kind == 'bars' else None)
        with duck_baseline.connect(self.database) as connection:
            for kind in ('summary', 'flow', 'bars'):
                with self.assertRaises(OverflowError):
                    duck_baseline.query(connection, 'ESZ26', kind, parse_iso('2026-09-09T22:00:00Z'),
                                        parse_iso('2026-09-09T22:01:00Z'), '1m' if kind == 'bars' else None)

    def test_changed_source_cleans_only_staging(self):
        self.source.write_text('20260909 220000 0000000;100;100;100.25;1\n')
        publish(self.source, 'ESZ26', self.data, LATTICE)
        original = duck_baseline.fingerprint
        calls = 0

        def changing(path):
            nonlocal calls
            calls += 1
            if calls == 2:
                path.write_text('20260909 220000 0000000;100;100;100.25;2\n')
            return original(path)

        with patch.object(duck_baseline, 'fingerprint', side_effect=changing):
            with self.assertRaisesRegex(ValueError, 'changed during'):
                duck_baseline.prepare(self.database, self.root, self.data, LATTICE, ('ESZ26',))
        self.assertFalse(self.database.exists())
        self.assertEqual(list(self.root.glob('.duckdb-staging-*')), [])
        self.assertTrue((self.data / 'ESZ26').is_dir())

    def test_benchmark_smoke_and_frozen_counts(self):
        self.load('20260909 220000 0000000;100;100;100.25;1\n'
                  '20260909 220000 0000001;100.25;100;100.25;2\n')
        row_data = self.root/'data-rows'
        publish_rows(self.data, 'ESZ26', row_data, LATTICE)
        workloads = self.root / 'workloads.json'
        workloads.write_text(json.dumps({'schema_version': 1, 'contracts': ['ESZ26'],
            'windows': [{'name': 'fixture', 'from': '2026-09-09T22:00:00Z', 'to': '2026-09-09T22:01:00Z'}],
            'queries': [{'kind': 'summary'}, {'kind': 'bars', 'bucket': '1m'}, {'kind': 'flow'}]}))
        output = self.root / 'benchmark'
        benchmark.execute(SimpleNamespace(lattice=LATTICE, data=self.data, row_data=row_data, database=self.database,
            source_dir=self.root, workloads=workloads, output=output, warmups=2, repetitions=3))
        metadata = json.loads((output / 'metadata.json').read_text())
        self.assertEqual(metadata['status'], 'complete')
        self.assertEqual(metadata['measured_invocations'], 36)
        self.assertTrue(all(w['selected_records'] == 2 for w in metadata['workloads']))
        with (output / 'runs.csv').open() as raw:
            rows = list(csv.DictReader(raw))
        self.assertEqual(len(rows), 60)
        self.assertEqual(sum(row['phase'] == 'measured' for row in rows), 36)
        with (output / 'summary.csv').open() as summary:
            summaries = list(csv.DictReader(summary))
        self.assertEqual(len(summaries), 12)
        for row in summaries:
            self.assertLessEqual(float(row['process_min_seconds']), float(row['process_median_seconds']))
            self.assertLessEqual(float(row['process_median_seconds']), float(row['process_max_seconds']))

    def test_sql_source_order_across_blocks(self):
        self.load(''.join(f'20260909 220000 {i//2:07};{Decimal(400+i%13)/4};100;103.25;1\n'
                          for i in range(65540)))
        for kind in ('summary', 'flow', 'bars'):
            self.compare(kind, '2026-09-09T22:00:00Z', '2026-09-09T22:00:01Z', '1s' if kind == 'bars' else None)
            self.compare(kind, '2026-09-09T22:00:00.003276700Z', '2026-09-09T22:00:00.003276900Z', '1s' if kind == 'bars' else None)

    def test_scale_boundaries_and_uncapped_memory(self):
        for rows in (1000, 1_000_000, 10_000_000, 100_000_000):
            document = scale_benchmark.workload_document(rows)
            prefix = document['windows'][1]
            end = parse_iso(prefix['to'])-parse_iso(prefix['from'])
            count = rows//100
            self.assertLess((count-1)//2*100, end)
            self.assertEqual(count//2*100, end)  # Both ticks at the boundary are excluded.
        for rows in (0, 999, 1001, 100_001_000):
            with self.assertRaises(ValueError):
                scale_benchmark.workload_document(rows)
        native = LATTICE.with_name('memory_probe.exe') if sys.platform=='win32' else None
        command = [sys.executable, '-c', 'import sys,duckdb;b=bytearray(128*1024*1024);b[::4096]=b"1"*(len(b)//4096);print(sys.prefix);print(duckdb.__version__)']
        completed, counters = probe(native, command, None)
        self.assertEqual(completed.returncode, 0)
        self.assertIn(sys.prefix, completed.stdout)
        self.assertIn(duck_baseline.VERSION, completed.stdout)
        resident = counters['peak_working_set_bytes' if sys.platform=='win32' else 'peak_rss_bytes']
        self.assertGreater(resident, 64*1024*1024)
        denied, _ = probe(native, [sys.executable, '-c',
            'try:\n b=bytearray(128*1024*1024)\nexcept MemoryError:\n raise SystemExit(17)'], 64*1024*1024)
        self.assertEqual(denied.returncode, 17)
        output = self.root/'series'
        scale_benchmark.execute_series(SimpleNamespace(lattice=LATTICE, probe=native,
            work_dir=self.root/'scratch', output=output, sizes=[1000], warmups=2, repetitions=3))
        series = json.loads((output/'series.json').read_text())
        self.assertEqual(series['status'], 'complete')
        self.assertEqual(series['runs'][0]['rows'], 1000)
        metadata = json.loads((output/'1000'/'metadata.json').read_text())
        self.assertEqual(metadata['measured_invocations'], 72)
        with (output/'1000'/'runs.csv').open() as source:
            measured = [r for r in csv.DictReader(source) if r['phase']=='measured']
        self.assertEqual(len(measured), 72)
        self.assertTrue(all(int(r['peak_resident_bytes'])>0 for r in measured))
        self.assertTrue(all(w['selected_records']==(1000 if w['window']=='full' else 10)
                            for w in metadata['workloads']))


if __name__ == '__main__':
    unittest.main()
