# Data provenance

Scope: ESZ26 and NQZ26, fixed December 2026 futures contracts. Continuous
contracts and rollover logic are outside this implementation.

The supplied sources are NinjaTrader exports from
`C:/Users/pasto/OneDrive/Documents/Proyectolattice`. The format is documented by
[NinjaTrader's import guide](https://static.ninjatrader.com/es/support/helpGuides/nt8/importing.htm):
`yyyyMMdd HHmmss fffffff;last;bid;ask;volume`, export timestamps in UTC.

Initial source inspection:

| Source | Bytes | Records | Volume |
| --- | --- | --- | --- |
| ES 12-26.Last.txt | 821014523 | 17111923 | 22521586 |
| NQ 12-26.Last.txt | 391693763 | 7692171 | 8281192 |

The inspected date range is September 9 through October 5, 2026. Inspection found
no malformed rows or decreasing timestamps in these files; this does not prove
complete feed coverage. Consecutive identical source rows are preserved: without
exchange execution IDs they cannot safely be deduplicated.

Each locally generated manifest contains the source SHA-256, byte size,
record count, original filename, unit conventions, engine version, exact import
command, hash/import timings and partition names. Do not redistribute the input
or derived data without checking the applicable provider terms. Public examples
and CI use invented synthetic prices, not extracts from these files.

The row baseline is a lossless conversion of the immutable column store. Its
manifest retains the original source/import fields and adds the parent manifest
and partition SHA-256 values, conversion command/version/time and block count.
The benchmark checks the full parent partition inventory and fingerprints before
using that row store. Both layouts represent the same supplied export records.

`tools/generate_ticks.py` creates synthetic exports independently of the real
ES/NQ texts. The memory fixture uses the supported ESZ26 schema identifier for
compatibility, but its invented prices and timestamps are not market data. Its
report records the generator and generated-source hashes. Stress inputs and
derived partitions stay under the ignored `data-stress/` directory.
