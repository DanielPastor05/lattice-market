"""Generate deterministic ordered synthetic exports; no licensed market data."""
import argparse
from pathlib import Path


def price(ticks):
    return f'{ticks//4}.{ticks%4*25:02}'


def generate(path, rows):
    if not 0 < rows <= 100_000_000:
        raise ValueError('Rows must be in 1..100,000,000')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='ascii', newline='\n') as output:
        for first in range(0, rows, 65536):
            lines=[]
            for i in range(first, min(first+65536, rows)):
                # Adjacent pairs share timestamps; prices/volumes have known deterministic periods.
                fractions=i//2
                ticks=40000+(i*1103515245+731)%1000
                lines.append(f'20260909 00000{fractions//10_000_000} {fractions%10_000_000:07};'
                             f'{price(ticks)};{price(ticks-1)};{price(ticks)};{i%5+1}\n')
            output.writelines(lines)
    return path


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--rows', type=int, required=True)
    args=p.parse_args()
    generate(args.output,args.rows)
    print(f'Generated {args.rows:,} synthetic records in {args.output}')
