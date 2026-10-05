"""End-to-end differential checks, trust boundary checks and corruption cases."""
import csv
from decimal import Decimal
import io
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from import_dataset import publish, publish_rows
from make_fuzz_corpus import short_blocks
from reference import compare_rows, evaluate, parse_iso

LATTICE = Path(sys.argv.pop(1)).resolve()


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.txt'
        self.data = self.root / 'data'
        self.row_data = self.root / 'data-rows'

    def run_engine(self, *args, code=0):
        result = subprocess.run([str(LATTICE), *map(str, args)], capture_output=True, text=True)
        self.assertEqual(result.returncode, code, result.stderr)
        return result

    def load(self, lines):
        self.source.write_bytes(lines.encode('ascii'))
        manifest = publish(self.source, 'ESZ26', self.data, LATTICE)
        publish_rows(self.data, 'ESZ26', self.row_data, LATTICE)
        return manifest

    def query(self, kind, begin, end, mode='pruned', bucket='1m', extra=()):
        args = ['query', kind, '--data', self.row_data if mode=='row' else self.data, '--contract', 'ESZ26', '--from', begin,
                '--to', end, '--scan-mode', mode]
        if kind == 'bars':
            args += ['--bucket', bucket]
        result = self.run_engine(*args, *extra)
        return list(csv.DictReader(io.StringIO(result.stdout)))

    def compare(self, kind, begin, end, bucket='1m'):
        expected = evaluate(self.source, 'ESZ26', kind, parse_iso(begin), parse_iso(end), bucket)
        for mode in ['row', 'column', 'pruned']:
            actual = self.query(kind, begin, end, mode, bucket)
            self.assertEqual(len(actual), len(expected))
            for a, e in zip(actual, expected):
                self.assertEqual(set(a), set(e))
                for key, value in e.items():
                    if key == 'vwap' or key.endswith('_fraction'):
                        if value == '':
                            self.assertEqual(a[key], '')
                        else:
                            tolerance = max(Decimal('1e-8') if key == 'vwap' else Decimal('1e-12'), abs(value) * Decimal('1e-12'))
                            self.assertLessEqual(abs(Decimal(a[key]) - value), tolerance)
                    elif isinstance(value, (int, Decimal)):
                        self.assertEqual(Decimal(a[key]), value, key)
                    else:
                        self.assertEqual(a[key], value, key)

    def test_reference_precision_duplicates_quotes_and_day_boundaries(self):
        self.load('20260909 235959 9999999;100.25;100;100.25;2\r\n'
                  '20260909 235959 9999999;100.25;100;100.25;2\r\n'
                  '20260910 000000 0000000;100;100;100.25;3\n'
                  '20260910 000000 0000001;100.5;100.25;100.5;4\n'
                  '20260910 000000 0000002;100.25;100.25;100.25;5\n'
                  '20260910 000000 0000003;100.25;101;100;6\n'
                  '20260910 000000 0000004;100.25;-1;0;7\n'
                  '20260910 000000 0000005;100.25;100;100.5;8')
        for begin, end in [('2026-09-09T23:59:59Z', '2026-09-10T00:01:00Z'),
                           ('2026-09-10T00:00:00.000000100Z', '2026-09-10T00:00:00.000000200Z'),
                           ('2026-09-11T00:00:00Z', '2026-09-11T01:00:00Z')]:
            for kind in ['summary', 'bars', 'flow']:
                self.compare(kind, begin, end)
        self.run_engine('verify', '--data', self.data, '--contract', 'ESZ26')
        with self.assertRaises(FileExistsError):
            publish(self.source, 'ESZ26', self.data, LATTICE)

    def test_randomized_cross_block_bars(self):
        rng = random.Random(731)
        lines = []
        for i in range(65540):
            ticks = 400 + rng.randrange(8)
            price = Decimal(ticks) / 4
            bid, ask = Decimal(ticks - (i % 3)) / 4, Decimal(ticks + 1) / 4
            lines.append(f'20260909 220000 {i:07};{price};{bid};{ask};{rng.randrange(1,11)}\n')
        self.load(''.join(lines))
        for kind in ['summary', 'bars', 'flow']:
            self.compare(kind, '2026-09-09T22:00:00Z', '2026-09-09T22:00:01Z', '1s')
        stats = self.root / 'stats.json'
        self.query('summary', '2026-09-09T22:00:00.006553600Z', '2026-09-09T22:00:01Z', extra=['--stats', stats])
        self.assertEqual(json.loads(stats.read_text())['blocks_skipped'], 1)

    def test_invalid_source_never_published(self):
        cases = ['20260230 220000 0000000;100;100;100.25;1',
                 '20260909 246000 0000000;100;100;100.25;1',
                 '20260909 220000 0000000;100.1;100;100.25;1',
                 '20260909 220000 0000000;100;100;100.25;0',
                 '20260909 220000 0000000;100;100;100.25;18446744073709551616',
                 '20260909 220000 0000000;nan;100;100.25;1',
                 '20260909 220000 0000000;100;100;100.25;1;extra', 'x' * 4097,
                 '20260909 220001 0000000;100;100;100.25;1\n20260909 220000 0000000;100;100;100.25;1']
        for source in cases:
            with self.subTest(source=source[:70]):
                self.source.write_text(source, encoding='ascii')
                with self.assertRaises(subprocess.CalledProcessError):
                    publish(self.source, 'ESZ26', self.data, LATTICE)
                self.assertFalse((self.data / 'ESZ26').exists())
                self.assertEqual(list(self.data.iterdir()), [])

    def test_corruption(self):
        self.load('20260909 220000 0000000;100;100;100.25;1\n')
        partition = next((self.data / 'ESZ26').glob('*.lmc'))
        original = partition.read_bytes()
        mutations = [original[:60], original[:-1]]
        for offset in [0, 8, 128, 136, len(original) - 1]:
            damaged = bytearray(original)
            damaged[offset] ^= 1
            mutations.append(damaged)
        # Bounds with valid checksums: metadata cannot trick the reader into huge allocations.
        damaged = bytearray(original)
        directory = int.from_bytes(damaged[64:72], 'little')
        damaged[directory + 24:directory + 32] = (2**64-1).to_bytes(8, 'little')
        damaged[88:92] = zlib.crc32(damaged[directory:]).to_bytes(4, 'little')
        damaged[92:96] = bytes(4)
        damaged[92:96] = zlib.crc32(damaged[:128]).to_bytes(4, 'little')
        mutations.append(damaged)
        for damaged in mutations:
            partition.write_bytes(damaged)
            self.run_engine('verify', '--data', self.data, '--contract', 'ESZ26', code=3)
        partition.write_bytes(original)

    def test_cli_errors_atomic_output_and_overflow(self):
        self.load('20260909 220000 0000000;100;100;100.25;18446744073709551615\n'
                  '20260909 220001 0000000;100;100;100.25;1\n')
        self.run_engine('query', 'summary', '--wat', 'x', code=2)
        self.run_engine('verify', '--data', self.data, '--contract', 'INVALID', code=2)
        output = self.root / 'output.csv'
        args = ['query', 'summary', '--data', self.data, '--contract', 'ESZ26',
                '--from', '2026-09-09T22:00:00Z', '--to', '2026-09-09T22:01:00Z', '--output', output]
        self.run_engine(*args, code=5)
        self.assertFalse(output.exists())
        self.assertFalse(Path(str(output) + '.tmp').exists())
        stats = self.root / 'stats.json'
        args[args.index('--to') + 1] = '2026-09-09T22:00:00.1Z'
        self.run_engine(*args, '--stats', stats)
        original = output.read_bytes()
        self.assertIn(b'18446744073709551615', original)
        measured = json.loads(stats.read_text())
        self.assertEqual(measured['rows_selected'], 1)
        self.assertEqual(measured['scan_mode'], 'pruned')
        self.assertFalse(Path(str(stats) + '.tmp').exists())
        self.run_engine(*args, code=4)
        self.assertEqual(output.read_bytes(), original)

    def test_manifest_inventory_and_count(self):
        self.load('20260909 235959 0000000;100;100;100.25;1\n'
                  '20260910 000000 0000000;100;100;100.25;1\n')
        contract = self.data / 'ESZ26'
        partition = sorted(contract.glob('*.lmc'))[-1]
        backup = partition.read_bytes()
        partition.unlink()
        self.run_engine('verify', '--data', self.data, '--contract', 'ESZ26', code=3)
        partition.write_bytes(backup)
        manifest = contract / 'manifest.json'
        original = manifest.read_text()
        for content in ['{"schema_version":1', 'null', original.replace('"record_count": 2', '"record_count": 3')]:
            manifest.write_text(content)
            self.run_engine('verify', '--data', self.data, '--contract', 'ESZ26', code=3)
        manifest.write_text(original)

    def test_changed_source_refuses_publication(self):
        import import_dataset
        self.source.write_text('20260909 220000 0000000;100;100;100.25;1\n')
        fingerprint = import_dataset.fingerprint
        calls = 0

        def changing(path):
            nonlocal calls
            calls += 1
            if calls == 2:
                path.write_text('20260909 220000 0000000;100;100;100.25;2\n')
            return fingerprint(path)

        with patch.object(import_dataset, 'fingerprint', side_effect=changing):
            with self.assertRaisesRegex(RuntimeError, 'changed during import'):
                publish(self.source, 'ESZ26', self.data, LATTICE)
        self.assertFalse((self.data / 'ESZ26').exists())
        self.assertEqual(list(self.data.iterdir()), [])

    def test_row_integrity_layout_and_payload_counts(self):
        self.load('20260909 220000 0000000;100;100;100.25;1\n'
                  '20260909 220000 0000000;100.25;100;100.25;2\n')
        stats = self.root / 'row-stats.json'
        self.query('summary', '2026-09-09T22:00:00Z', '2026-09-09T22:00:01Z', mode='row', extra=['--stats', stats])
        measured = json.loads(stats.read_text())
        self.assertEqual(measured['bytes_requested'], 128+48*2+48*2)
        self.assertEqual(measured['blocks_skipped'], 0)
        self.run_engine('verify', '--data', self.row_data, '--contract', 'ESZ26')
        with self.assertRaises(FileExistsError):
            publish_rows(self.data, 'ESZ26', self.row_data, LATTICE)
        for data, mode in [(self.data, 'row'), (self.row_data, 'pruned')]:
            self.run_engine('query', 'summary', '--data', data, '--contract', 'ESZ26', '--scan-mode', mode,
                            '--from', '2026-09-09T22:00:00Z', '--to', '2026-09-09T22:01:00Z', code=2)
        partition = next((self.row_data / 'ESZ26').glob('*.lmc'))
        original = partition.read_bytes()
        directory = int.from_bytes(original[64:72], 'little')
        for offset, value, size in [(128, 0, 1), (directory+24, 2**64-1, 8),
                                    (directory+32, 97, 8), (directory+44, 1, 4)]:
            damaged = bytearray(original)
            damaged[offset:offset+size] = value.to_bytes(size, 'little')
            if offset >= directory:
                damaged[88:92] = zlib.crc32(damaged[directory:]).to_bytes(4,'little')
                damaged[92:96] = bytes(4)
                damaged[92:96] = zlib.crc32(damaged[:128]).to_bytes(4,'little')
            partition.write_bytes(damaged)
            self.run_engine('verify', '--data', self.row_data, '--contract', 'ESZ26', code=3)
        partition.write_bytes(original)
        # A row payload swapped into a column manifest must be rejected.
        column = next((self.data / 'ESZ26').glob('*.lmc'))
        column.write_bytes(original)
        self.run_engine('verify', '--data', self.data, '--contract', 'ESZ26', code=3)
        with self.assertRaises(subprocess.CalledProcessError):
            publish_rows(self.data, 'ESZ26', self.root/'failed-row', LATTICE)
        self.assertEqual(list((self.root/'failed-row').iterdir()), [])

    def test_short_source_blocks_preserved(self):
        self.source.write_text('20260909 235959 9999999;100;100;100.25;1\n'
                               '20260909 235959 9999999;100.25;100;100.25;2\n')
        publish(self.source, 'ESZ26', self.data, LATTICE)
        partition = next((self.data / 'ESZ26').glob('*.lmc'))
        partition.write_bytes(short_blocks(partition.read_bytes()))
        manifest = publish_rows(self.data, 'ESZ26', self.row_data, LATTICE)
        self.assertEqual(manifest['conversion_block_count'], 2)
        for kind in ('summary','bars','flow'):
            self.compare(kind, '2026-09-09T23:59:59Z', '2026-09-10T00:00:00Z')


if __name__ == '__main__':
    unittest.main()
