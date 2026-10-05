"""Create small synthetic, structurally valid parser seeds; refuses an existing corpus."""
import argparse
from pathlib import Path
import tempfile
import zlib

from generate_ticks import generate
from import_dataset import publish, publish_rows


def short_blocks(original):
    """Split a two-record column block into two independently checksummed blocks."""
    header=bytearray(original[:128])
    payload,directory=bytearray(),bytearray()
    for row in range(2):
        entry=bytearray(168)
        ns=original[144+row*8:152+row*8]
        entry[:8],entry[8:16],entry[16:20]=ns,ns,(1).to_bytes(4,'little')
        for field in range(6):
            values=original[128+field*16+row*8:136+field*16+row*8]
            at=24+field*24
            entry[at:at+8]=(128+len(payload)).to_bytes(8,'little')
            entry[at+8:at+16]=(8).to_bytes(8,'little')
            entry[at+16:at+20]=zlib.crc32(values).to_bytes(4,'little')
            payload.extend(values)
        directory.extend(entry)
    for at,value in [(56,2),(64,128+len(payload)),(72,len(directory)),(80,128+len(payload)+len(directory))]:
        header[at:at+8]=value.to_bytes(8,'little')
    header[88:92],header[92:96]=zlib.crc32(directory).to_bytes(4,'little'),bytes(4)
    header[92:96]=zlib.crc32(header).to_bytes(4,'little')
    return header+payload+directory


def make(lattice,output):
    output.mkdir(parents=True,exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='lattice-corpus-') as temporary:
        root=Path(temporary)
        source=generate(root/'seed.txt',64)
        publish(source,'ESZ26',root/'column',lattice)
        publish_rows(root/'column','ESZ26',root/'row',lattice)
        for layout in ('column','row'):
            (output/layout).write_bytes(next((root/layout/'ESZ26').glob('*.lmc')).read_bytes())
        (output/'manifest').write_bytes((root/'column/ESZ26/manifest.json').read_bytes())
        (output/'source-line').write_bytes(source.read_bytes().splitlines()[0])
        source=generate(root/'short.txt',2)
        publish(source,'ESZ26',root/'short',lattice)
        partition=next((root/'short/ESZ26').glob('*.lmc'))
        partition.write_bytes(short_blocks(partition.read_bytes()))
        publish_rows(root/'short','ESZ26',root/'short-row',lattice) # Verifies both short source blocks.
        (output/'column-short-blocks').write_bytes(partition.read_bytes())
        (output/'row-short-blocks').write_bytes(next((root/'short-row/ESZ26').glob('*.lmc')).read_bytes())
    print(f'Created six verified synthetic seeds: {output}')


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--lattice',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    make(args.lattice.resolve(),args.output)
