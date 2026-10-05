"""Bounded deterministic CLI mutations; useful with ASan, not coverage-guided fuzzing."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import subprocess
import tempfile
import zlib

from generate_ticks import generate
from import_dataset import fingerprint, publish, publish_rows


def repair(data, payload=False):
    directory = int.from_bytes(data[64:72], 'little')
    stride = 48 if int.from_bytes(data[12:16], 'little') == 3 else 168
    if payload:
        for descriptor in range(directory+24, directory+stride, 24):
            offset = int.from_bytes(data[descriptor:descriptor+8], 'little')
            length = int.from_bytes(data[descriptor+8:descriptor+16], 'little')
            data[descriptor+16:descriptor+20] = zlib.crc32(data[offset:offset+length]).to_bytes(4, 'little')
    data[88:92] = zlib.crc32(data[directory:]).to_bytes(4, 'little')
    data[92:96] = bytes(4)
    data[92:96] = zlib.crc32(data[:128]).to_bytes(4, 'little')


def campaign(lattice, cases, seed):
    rng = random.Random(seed)
    counts = {'accepted': 0, 'rejected': 0}
    by_kind = {}
    with tempfile.TemporaryDirectory(prefix='lattice-mutations-') as temporary:
        root = Path(temporary)
        source = generate(root/'seed.txt', 64)
        publish(source, 'ESZ26', root/'column', lattice)
        publish_rows(root/'column', 'ESZ26', root/'row', lattice)
        seeds = {}
        for layout in ('column', 'row'):
            partition = next((root/layout/'ESZ26').glob('*.lmc'))
            manifest = partition.parent/'manifest.json'
            seeds[layout] = (partition, partition.read_bytes(), manifest, manifest.read_bytes())
            result = subprocess.run([str(lattice), 'verify', '--data', str(root/layout), '--contract', 'ESZ26'],
                                    capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                raise RuntimeError(f'Valid seed failed: {result.stderr}')
        for case in range(cases):
            layout = ('column', 'row')[case % 2]
            partition, original, manifest, original_manifest = seeds[layout]
            data = bytearray(original)
            directory = int.from_bytes(data[64:72], 'little')
            kind = ('raw', 'metadata', 'payload', 'manifest')[(case//2) % 4]
            if kind == 'raw':
                if rng.randrange(2):
                    data = data[:rng.randrange(len(data))]
                else:
                    data[rng.randrange(len(data))] ^= 1 << rng.randrange(8)
            elif kind == 'metadata':
                offset, width = rng.choice([(48,8), (56,8), (64,8), (72,8), (80,8),
                                           (96,8), (directory,8), (directory+16,4),
                                           (directory+24,8), (directory+32,8), (directory+44,4)])
                data[offset:offset+width] = rng.choice([0, 1, (1 << (width*8))-1, 65537]).to_bytes(width, 'little')
                # Keep the original directory position for CRC repair, even if its header pointer changed.
                data[88:92] = zlib.crc32(data[directory:]).to_bytes(4, 'little')
                data[92:96] = bytes(4)
                data[92:96] = zlib.crc32(data[:128]).to_bytes(4, 'little')
            elif kind == 'payload':
                offset = rng.randrange(128, directory)
                data[offset] ^= 1 << rng.randrange(8)
                repair(data, payload=True)
            else:
                changed = bytearray(original_manifest)
                if rng.randrange(2):
                    changed = changed[:rng.randrange(len(changed))]
                else:
                    changed[rng.randrange(len(changed))] ^= 1 << rng.randrange(8)
                manifest.write_bytes(changed)
            partition.write_bytes(data)
            result = subprocess.run([str(lattice), 'verify', '--data', str(root/layout), '--contract', 'ESZ26'],
                                    capture_output=True, text=True, timeout=10)
            if result.returncode not in (0, 3) or 'AddressSanitizer' in result.stderr or 'runtime error:' in result.stderr:
                raise RuntimeError(f'Case {case}, {layout}/{kind}, exit {result.returncode}: {result.stderr}')
            outcome = 'accepted' if result.returncode == 0 else 'rejected'
            counts[outcome] += 1
            by_kind.setdefault(f'{layout}/{kind}', {'accepted': 0, 'rejected': 0})[outcome] += 1
            partition.write_bytes(original)
            manifest.write_bytes(original_manifest)
    return dict(status='passed', cases=cases, seed=seed, outcomes=counts, by_kind=by_kind,
                binary_sha256=fingerprint(lattice)[0], tool_sha256=fingerprint(Path(__file__))[0],
                engine_version=subprocess.check_output([str(lattice), '--version'], text=True).strip(),
                executed_at_utc=datetime.now(timezone.utc).isoformat(),
                limitation='Small CLI seeds; no coverage guidance, exhaustive safety proof or throughput claim')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--lattice', type=Path, required=True)
    p.add_argument('--cases', type=int, default=256)
    p.add_argument('--seed', type=int, default=731)
    p.add_argument('--report', type=Path)
    args = p.parse_args()
    if not 8 <= args.cases <= 10000:
        p.error('Require 8..10,000 bounded cases')
    if args.report and args.report.exists():
        p.error('Report already exists')
    report = campaign(args.lattice.resolve(), args.cases, args.seed)
    text = json.dumps(report, indent=2)+'\n'
    if args.report:
        with args.report.open('x', encoding='utf-8') as output:
            output.write(text)
    print(text)
