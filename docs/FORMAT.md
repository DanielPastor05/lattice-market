# Binary format v1

All fields use little-endian serialization; C++ struct layouts are never written.
Signed values use 64-bit two's complement. Each `<UTC day>.lmc` contains a
128-byte header, block payloads, and a directory at the end. No compression.

| Header offset | Bytes | Meaning |
| --- | --- | --- |
| 0 | 8 | ASCII `LATTICE1` |
| 8 | 2 | Version: 1 |
| 10 | 2 | Header length: 128 |
| 12 | 4 | Flags: 1 (column layout) or 3 (nanoseconds + row layout bit 2) |
| 16 | 16 | ASCII contract with zero padding |
| 32 | 4 | UTC days since 1970-01-01 |
| 36 | 4 | Price denominator: 4 |
| 40 | 4 | Volume denominator: 1 |
| 44 | 4 | Block capacity: 65536 |
| 48 | 8 | Total record count |
| 56 | 8 | Number of blocks |
| 64 | 8 | Directory offset |
| 72 | 8 | Directory length |
| 80 | 8 | Total file size |
| 88 | 4 | Directory IEEE CRC32 |
| 92 | 4 | Header IEEE CRC32 (this field zero while calculating) |
| 96 | 32 | Reserved, must be zero |

For column layout, each directory entry is 168 bytes: minimum timestamp (8), maximum timestamp (8),
row count (4), zero reserved (4), then six descriptors of 24 bytes. Each
descriptor is offset (8), byte length (8), payload CRC32 (4), zero reserved (4).
Descriptors follow the six column order below. The file payload is contiguous:
block 0's six columns, then block 1's six columns, etc. The last column ends
exactly at the directory offset. Each column length equals row count times 8.

| Column | Type | Unit |
| --- | --- | --- |
| source_row | uint64 | One-based physical source line |
| timestamp_ns | int64 | UTC nanoseconds since epoch |
| last_ticks | int64 | Price divided by 0.25 |
| bid_ticks | int64 | Price divided by 0.25 |
| ask_ticks | int64 | Price divided by 0.25 |
| volume_contracts | uint64 | Contracts |

For row layout, each directory entry is 48 bytes: the same 24-byte timestamp/count
prefix followed by one 24-byte descriptor. A payload contains `row count * 48`
bytes, with the six fields above interleaved in that order. One IEEE CRC32 covers
the whole block. Row queries read/check all fields' bytes but decode only the
fields required by the query. Column queries can read/check individual columns.
Flags other than 1 and 3 are rejected; flags 1 files remain compatible.

`convert-row` preserves every source block boundary, including short interior
blocks, and all record values/order. Published manifests declare `storage_layout`;
older manifests without it default to column. Every partition must agree with
the declared layout. Use row scan mode only with row stores, and column/pruned
modes only with column stores.

Readers check the entire directory before using min/max for pruning, with
bounded entry reads. Required column payload CRCs are checked before decoding.
`verify` reads all columns and validates order, positive last/volume and source
rows. CRC32 detects accidental damage; it is not authentication against an
attacker who can recompute checksums. A query does not check skipped payloads.
