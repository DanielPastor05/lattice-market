"""Hash, stage, verify and publish immutable NinjaTrader imports (stdlib only)."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone


def fingerprint(path):
    before = path.stat()
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    after = path.stat()
    identity = lambda s: (s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_dev, s.st_ino)
    if identity(before) != identity(after):
        raise RuntimeError('Source changed while hashing')
    return digest.hexdigest(), identity(after)


def publish(source, contract, data, lattice):
    source, data, lattice = Path(source).resolve(), Path(data).resolve(), Path(lattice).resolve()
    if contract not in ('ESZ26', 'NQZ26'):
        raise ValueError('Supported contracts: ESZ26, NQZ26')
    data.mkdir(parents=True, exist_ok=True)
    target = data / contract
    if target.exists():
        raise FileExistsError(f'Immutable destination already exists: {target}')
    started = time.perf_counter()
    digest, identity = fingerprint(source)
    first_hash_seconds = time.perf_counter() - started
    staging = Path(tempfile.mkdtemp(prefix=f'.{contract}.staging-', dir=data))
    try:
        command = [str(lattice), 'import', '--input', str(source), '--contract', contract,
                   '--output', str(staging)]
        started = time.perf_counter()
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        imported = json.loads(completed.stdout)
        import_seconds = time.perf_counter() - started
        started = time.perf_counter()
        final_digest, final_identity = fingerprint(source)
        final_hash_seconds = time.perf_counter() - started
        if digest != final_digest or identity != final_identity:
            raise RuntimeError('Source changed during import; publication refused')
        version = subprocess.check_output([str(lattice), '--version'], text=True).strip()
        manifest = {
            'schema_version': 1, 'contract': contract,
            'storage_layout': 'column',
            'source_label': 'ES 12-26' if contract == 'ESZ26' else 'NQ 12-26',
            'source_name': source.name, 'source_sha256': digest,
            'source_bytes': identity[0], 'source_mtime_ns': identity[1],
            'record_count': imported['record_count'],
            'first_timestamp_utc': imported['first_timestamp_utc'],
            'last_timestamp_utc': imported['last_timestamp_utc'],
            'timestamp_timezone': 'UTC', 'timestamp_unit': 'nanoseconds',
            'source_fraction_digits': 7, 'price_tick_size': '0.25',
            'volume_unit': 'contracts', 'duplicates': 'preserved',
            'engine_version': version, 'import_command': command,
            'imported_at_utc': datetime.now(timezone.utc).isoformat(),
            'first_hash_seconds': first_hash_seconds,
            'final_hash_seconds': final_hash_seconds, 'import_seconds': import_seconds,
            'partitions': sorted(p.name for p in staging.glob('*.lmc')),
        }
        (staging / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        # One publisher per contract. rename is same-filesystem; no power-loss durability claim.
        if target.exists():
            raise FileExistsError(f'Destination appeared during import: {target}')
        staging.rename(target)
        return manifest
    except BaseException:
        # Only this invocation's uniquely created staging directory is removed.
        shutil.rmtree(staging)
        raise


def publish_rows(source_data, contract, data, lattice):
    source_data, data, lattice = Path(source_data).resolve(), Path(data).resolve(), Path(lattice).resolve()
    if contract not in ('ESZ26', 'NQZ26'):
        raise ValueError('Unsupported contract')
    source = source_data / contract
    manifest_path = source / 'manifest.json'
    manifest_digest, _ = fingerprint(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('storage_layout', 'column') != 'column':
        raise ValueError('Row conversion requires column data')
    inputs = [manifest_path, *sorted(source.glob('*.lmc'))]
    before = {p.name: fingerprint(p) for p in inputs}
    data.mkdir(parents=True, exist_ok=True)
    target = data / contract
    if target.exists():
        raise FileExistsError(f'Immutable row destination exists: {target}')
    staging = Path(tempfile.mkdtemp(prefix=f'.{contract}.staging-', dir=data))
    try:
        command = [str(lattice), 'convert-row', '--data', str(source_data), '--contract', contract, '--output', str(staging)]
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        converted = json.loads(completed.stdout)
        after = {p.name: fingerprint(p) for p in [manifest_path, *sorted(source.glob('*.lmc'))]}
        if before != after or before['manifest.json'][0] != manifest_digest:
            raise RuntimeError('Column store changed during row conversion')
        if converted['record_count'] != manifest['record_count']:
            raise RuntimeError('Conversion count differs from source')
        manifest.update(storage_layout='row', parent_manifest_sha256=manifest_digest,
                        parent_partition_sha256={p: v[0] for p, v in before.items() if p.endswith('.lmc')},
                        conversion_command=command, conversion_block_count=converted['block_count'],
                        conversion_engine_version=subprocess.check_output([str(lattice), '--version'], text=True).strip(),
                        converted_at_utc=datetime.now(timezone.utc).isoformat())
        (staging / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
        if target.exists():
            raise FileExistsError('Row destination appeared during conversion')
        staging.rename(target)
        return manifest
    except BaseException:
        shutil.rmtree(staging)
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sources = p.add_mutually_exclusive_group(required=True)
    sources.add_argument('--input', type=Path)
    sources.add_argument('--row-source', type=Path, help='Convert a verified column store without reparsing text')
    p.add_argument('--contract', choices=('ESZ26', 'NQZ26'), required=True)
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--lattice', type=Path, required=True)
    args = p.parse_args()
    try:
        manifest = (publish_rows(args.row_source, args.contract, args.data, args.lattice) if args.row_source
                    else publish(args.input, args.contract, args.data, args.lattice))
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        detail = error.stderr if isinstance(error, subprocess.CalledProcessError) else str(error)
        p.exit(1, f'Import failed: {detail}\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
