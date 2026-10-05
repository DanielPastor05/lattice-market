"""Run a reproducible synthetic demo without account credentials or market data."""
import argparse
from pathlib import Path
import subprocess
import tempfile
from import_dataset import publish


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--lattice', type=Path, required=True)
    args = p.parse_args()
    lattice = args.lattice.resolve()
    with tempfile.TemporaryDirectory(prefix='lattice-demo-') as directory:
        root = Path(directory)
        source = root / 'synthetic.txt'
        source.write_text(
            '20260909 220000 0000001;100.25;100;100.25;2\n'
            '20260909 220000 0000001;100.25;100;100.25;2\n'
            '20260909 220030 0000000;100;100;100.25;3\n'
            '20260909 220100 0000000;100.25;100.25;100.25;1\n', encoding='ascii')
        publish(source, 'ESZ26', root / 'data', lattice)
        for kind in ['summary', 'bars', 'flow']:
            print(f'\n{kind}:', flush=True)
            command = [str(lattice), 'query', kind, '--data', str(root / 'data'), '--contract', 'ESZ26',
                       '--from', '2026-09-09T22:00:00Z', '--to', '2026-09-09T22:02:00Z']
            if kind == 'bars':
                command += ['--bucket', '1m']
            subprocess.run(command, check=True)


if __name__ == '__main__':
    main()
