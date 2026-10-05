# Design decisions and current limits

The first milestone deliberately keeps the C++ engine in one translation unit:
parser, binary writer/reader and three aggregations. There is no plugin interface
or general SQL planner. Split concrete modules when upcoming baselines make the
file difficult to change, rather than create an abstraction per function now.

## Streaming and memory

Imports buffer at most 65,536 ticks and one serialized column at a time. Row
conversion buffers the same ticks and one 48-byte-per-record serialized block.
Readers load at most six columns or one interleaved row block (3 MiB) at a time.
Query state for summary, flow and current
bar is constant size. The writer retains one day's directory in memory
(168 bytes/block for columns, 48 for rows). This intentionally differs from the plan's disk-spooled
directory: on the supplied dataset metadata is tiny. Exceptionally large daily
metadata should trigger the planned spool implementation. Directory validation
on read is streamed and never allocates from unchecked file counts.

The reader currently validates all partition headers/directories even when
entire days lie outside a query; block payloads are pruned. It reads each
directory entry twice: validation and scanning. This makes correctness simple
and the cost is included in `bytes_requested`. A partition catalogue optimization
must preserve validation before pruning and account for its reads fairly.

## Publication and files

Python owns publication. C++ only writes within an existing empty staging
directory and verifies it before returning. Sources are hashed before/after,
and size, timestamps and file identity must match. This detects observed source
changes; it cannot defeat a privileged process that changes bytes temporarily
and restores all metadata. Imports require a stable source file.

The wrapper publishes by renaming a unique sibling staging directory. Existing
contract directories are immutable. Use one publishing process per contract;
the existence check and rename are not a portable multi-writer lock. A process
crash can leave a hidden staging directory, which must be inspected before
manual removal. Rename provides visibility atomicity on the local filesystem,
not an `fsync` power-loss guarantee or a promise for OneDrive synchronization.

Queries validate the manifest's schema, contract, exact partition inventory and
total record count against binary metadata. Malformed JSON, missing/extra daily
partitions and count mismatches are rejected. Manifest input is bounded to 1 MiB
and 16 levels of nesting. Binary headers must agree with the manifest layout.
The Python manifest carries source provenance; the reader does not rehash the
original export, which is not needed to use a published dataset.

Row conversion verifies all source/output fields and preserves each block. The
wrapper fingerprints the source manifest and every column partition before/after
conversion, then records those parent hashes in a separate immutable row store.
The original text-import provenance remains intact, with conversion command,
engine version and timestamp recorded separately. Conversion is offline and
excluded from query measurements.

Windows memory evidence uses a native Job Object limit on committed process
memory, assigned before resuming the child. Peak working set is a separate
resident-memory counter; neither is Linux RSS. A denied-allocation canary checks
enforcement. See [memory evidence](validation-memory-windows.json) for the bounded
24M-record fixture. This covers query readers, not importer peak memory or an
arbitrarily large daily writer directory.

Named query output is written to a temporary sibling and renamed after success.
Files are not overwritten. Stats are separately published; a failure publishing
stats can leave an already completed CSV. They are not a joint transaction.
Standard output can be partial on a later error; consumers must check exit code.
One writer per output pathname is required.

## Numerical rules

Dates range from 1970 through 2100. Leap seconds are rejected. Seven NinjaTrader
fraction digits are multiplied by 100 exactly. ISO ranges accept 1–9 digits and
require `Z`. Decimal source prices use integer parsing, no intermediate float,
and a quarter-point grid. Zero/negative numeric quotes are retained and become
unknown in flow. NaN, scientific notation and off-grid prices are invalid.

All count/volume additions check uint64 overflow. VWAP converts operands to
double before multiplication, uses compensated summation, checks finiteness,
and divides by the final total volume. It is approximate, not arbitrary precision.

Exit codes: 0 success, 1 unexpected internal error, 2 invalid command/options,
3 invalid source or corrupt data, 4 filesystem/I/O failure, 5 arithmetic overflow.
