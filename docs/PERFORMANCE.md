# Reproducible performance experiment

The first study below is the preserved engine 0.1.0 three-variant experiment.
Its source hashes identify that historical implementation. The recorded 0.2.0
study adds row storage and is recorded separately; do not combine samples across
studies or compare historical source hashes with the current working tree.

## Scope and methodology

Compare the complete `lattice --scan-mode column`, `lattice --scan-mode pruned`
and single-thread DuckDB CLI implementations. This does not isolate storage
layout, language, parser or aggregation algorithm. This first study has no row baseline;
column-versus-pruned isolates the effect of block pruning with the same format.

The fixed [workloads](../bench/workloads.json) contain both contracts, full
history, one UTC day, and ten contained minutes. Each interval runs summary,
estimated flow, one-minute bars and one-hour bars: 24 workloads in all. Input
selection counts and output row counts are frozen in run metadata before timing.

Every workload has two warmup rounds and seven measured rounds. Engine order
rotates across all three variants; workload order is deterministically shuffled
per round using seed 731. Each invocation uses a new process. DuckDB opens a
new read-only connection to its persistent table and sets one thread. Two
warmups do not prove cache residency: OS cache is uncontrolled and not cleared.
These are not cold-cache results.

The primary clock is the parent's `perf_counter` around `subprocess.run`, including
process launch, connection/startup, complete CSV capture/decoding, stats publication
and process exit. Both variants write all CSV output to the same kind of pipe.
Output comparisons take place outside the recorded clocks after each invocation.
Do not compare this process time to a persistent in-process database connection.

The secondary clocks have different boundaries and must be labeled accordingly:

- Lattice: manifest parsing, validation of all partition headers/directories,
  required payload reads/CRCs, filtering, aggregation, CSV serialization/flush.
- DuckDB: SQL planning/execution, fetch of all results, Python CSV formatting/flush.
  It excludes import, connection creation and connection settings.

Integrity checks remain enabled. Lattice verifies accessed column CRCs on every
query; even a pruned query checks every partition directory. DuckDB uses its own
native storage, automatic compression, zonemaps and integrity behavior. Its
512 MB memory setting is an experiment configuration chosen to keep the baseline
bounded on this laptop, not DuckDB's default or a process RSS limit. No peak RSS,
hardware performance counters or constrained-memory proof is claimed.

Lattice's `bytes_requested` counter counts binary partition header, directory
and column reads. It excludes manifest JSON and filesystem metadata calls;
it is not physical disk traffic. DuckDB byte/CRC counters are not equated with
this value. Both internal clocks still include their respective validation costs.

## Correctness and independent input

DuckDB 1.5.6 is pinned in the requirements file. It reads the original NinjaTrader
text directly with serial CSV input, then assigns a source row before storage.
SQL parses whole seconds and adds the seven-digit fraction using BIGINT
arithmetic. It does not cast fractional timestamps to microsecond types.
Prices are converted through DECIMAL to quarter-point BIGINT values; volume
and source row are UBIGINT. The source hash and file identity must match before
and after preparation, and the hash must agree with the lattice manifest.
Preparation validates the entire lattice dataset, then publishes the completed
database via a sibling temporary directory. Existing databases are immutable.

Bars use integer division for bucket alignment and `arg_min`/`arg_max` ordered
by `(timestamp_ns, source_row)` for OHLC. Floating operands are converted before
VWAP multiplication; `fsum` provides compensated accumulation. SQL flow follows
the same valid-quote rule. Aggregate volume overflow beyond uint64 is rejected.
These choices follow DuckDB's [order preservation documentation](https://duckdb.org/docs/stable/sql/dialect/order_preservation)
and [aggregate function semantics](https://duckdb.org/docs/stable/sql/functions/aggregates).

The benchmark first checks both full-text summary oracles with 80-digit Decimal
arithmetic, then compares lattice outputs to independently parsed DuckDB SQL
for all workloads. Summary thus has the full-text Decimal check; full-range
bars and flow have the independent SQL check. Existing synthetic tests cover
100 ns boundaries, equal timestamps with differing prices, midnight, a 65,540-row
block/batch transition, signed/unsigned integer extremes, overflow and source
mutation. VWAP tolerance is `max(1e-8, abs(expected)*1e-12)`; fractions allow
`1e-12`. Integer/string fields must match exactly.

Database import/hashing is excluded from query timing. Recorded preparation
`import_seconds` includes table creation, text parsing/insertion, metadata
checks and the second source hash; it excludes the first hash and final
checkpoint. It is provenance, not a fair importer throughput comparison.

## Recorded results

Completed locally on 2026-10-05: **24 workloads × 3 variants × 7 measured
repetitions = 504 measured invocations**, plus 144 warmup invocations.
All correctness gates and every invocation comparison passed.

Host: AMD Ryzen 7 5700U with Radeon Graphics, 16 GiB installed RAM,
Windows 11 x64, MSVC 19.43.34809.0 Release (`/O2 /Ob2 /DNDEBUG`),
Python 3.14.4, DuckDB 1.5.6, one query thread. Background activity, CPU
frequency and filesystem cache were not controlled; this is one laptop run.

### Median complete process latency (milliseconds)

Each cell is the median of seven samples. All samples, min/max/IQR and the
execution order are in the linked CSV files. Query output is fully materialized.

**ESZ26**

| Window | Query | Selected records | Column | Pruned | DuckDB |
| --- | --- | ---: | ---: | ---: | ---: |
| full | summary | 17,111,923 | 1668.58 | 1654.93 | 932.63 |
| full | flow | 17,111,923 | 2520.07 | 2513.94 | 1255.68 |
| full | bars 1m | 17,111,923 | 1940.10 | 1905.06 | 3115.35 |
| full | bars 1h | 17,111,923 | 1725.96 | 1689.88 | 2737.53 |
| day | summary | 1,663,822 | 1467.82 | 174.05 | 380.18 |
| day | flow | 1,663,822 | 2364.46 | 259.99 | 413.14 |
| day | bars 1m | 1,663,822 | 1509.00 | 192.13 | 594.68 |
| day | bars 1h | 1,663,822 | 1496.22 | 177.46 | 590.27 |
| ten_minutes | summary | 46,082 | 1479.59 | 31.48 | 328.75 |
| ten_minutes | flow | 46,082 | 2471.79 | 38.91 | 330.22 |
| ten_minutes | bars 1m | 46,082 | 1470.83 | 32.19 | 328.96 |
| ten_minutes | bars 1h | 46,082 | 1445.72 | 32.41 | 343.99 |

**NQZ26**

| Window | Query | Selected records | Column | Pruned | DuckDB |
| --- | --- | ---: | ---: | ---: | ---: |
| full | summary | 7,692,171 | 736.26 | 738.76 | 610.29 |
| full | flow | 7,692,171 | 1157.48 | 1152.04 | 781.37 |
| full | bars 1m | 7,692,171 | 982.48 | 976.76 | 1819.68 |
| full | bars 1h | 7,692,171 | 752.98 | 751.49 | 1445.83 |
| day | summary | 731,731 | 644.77 | 87.10 | 348.22 |
| day | flow | 731,731 | 1069.02 | 124.71 | 366.27 |
| day | bars 1m | 731,731 | 666.50 | 101.57 | 452.42 |
| day | bars 1h | 731,731 | 655.40 | 87.71 | 427.32 |
| ten_minutes | summary | 19,858 | 637.73 | 34.04 | 326.14 |
| ten_minutes | flow | 19,858 | 1062.18 | 37.39 | 320.65 |
| ten_minutes | bars 1m | 19,858 | 631.60 | 30.21 | 322.62 |
| ten_minutes | bars 1h | 19,858 | 643.74 | 30.84 | 329.73 |

### Secondary query clocks (summary only, milliseconds)

These clocks have the different boundaries stated above. In particular,
DuckDB connection/startup is excluded here and included in process latency.

| Contract | Window | Lattice column query | Lattice pruned query | DuckDB SQL + CSV |
| --- | --- | ---: | ---: | ---: |
| ESZ26 | full | 1652.19 | 1639.10 | 598.24 |
| ESZ26 | day | 1452.15 | 157.51 | 63.02 |
| ESZ26 | ten_minutes | 1463.87 | 15.59 | 6.93 |
| NQZ26 | full | 721.58 | 716.21 | 277.37 |
| NQZ26 | day | 629.03 | 71.28 | 30.58 |
| NQZ26 | ten_minutes | 620.15 | 14.46 | 5.53 |

### Observations and next work

For full-history summaries, DuckDB has lower median process latency on
both contracts. For the contained ten-minute summary, the pruned lattice
CLI has lower median process latency on both contracts. This comparison
includes DuckDB Python/connection startup; the secondary clocks prevent
mistaking CLI startup differences for aggregation throughput.

The two lattice contracts occupy **1135.51 MiB** in binary partitions;
the DuckDB database containing both occupies **112.51 MiB**.
Lattice is uncompressed; DuckDB uses automatic native compression. These
storage/cache differences and validation costs are part of the implementations,
not a controlled test of column layout.

At this study's completion, row storage, synthetic scale experiments, RSS
measurements and Linux execution were still pending. The later row/Windows
memory milestone is recorded in [VALIDATION.md](VALIDATION.md).

### Reusable evidence

- [Individual runs, including warmups](../bench/results/2026-10-05/runs.csv).
- [Summary statistics](../bench/results/2026-10-05/summary.csv).
- [Environment, hashes, schemas and exact reference summaries](../bench/results/2026-10-05/metadata.json).

Per-query JSON counters remain in the ignored local results directory.
The public-sized evidence files contain timings/counters and aggregate
reference values, not tick rows or a copy of either market-data source.

## Engine 0.2.0: four-variant row comparison

Completed locally on 2026-10-05 with the same host, compiler, dependency,
intervals and timing boundaries as above: **24 workloads × 4 variants ×
7 measured repetitions = 672 measured invocations**, plus 192 warmups.
All complete-source Decimal summary gates, 72 native-to-SQL preflight
comparisons and all 864 timed-invocation output comparisons passed.

Row conversion is offline and preserves every source block boundary and
record value/order. Parent manifest and complete partition hash inventories
are checked before timing. The run records both row/column provenance and
current source/binary fingerprints. No measured source file changed during
this run; this local repository still has no commit.

The legacy metadata keys `lattice_payload_bytes` and `lattice_row_payload_bytes`
mean total partition-file sizes, including headers/directories. Record payload
alone is `record_count * 48` bytes in either uncompressed layout.

Row reads 48 bytes per record with one block CRC. Column reads 24 bytes for
summary/bars and 40 bytes for flow, with CRCs over each accessed column.
Both use the same IEEE CRC algorithm, block boundaries, field projection and
aggregations. Row decoding projects required fields from the interleaved
buffer. The CRC byte counts, directory lengths and manifest parsing costs
differ. Consequently this compares layout plus its read/integrity costs,
rather than isolating CPU cache locality or decoder speed. Pruned-versus-column
still uses the same column store and query code.

### Median complete process latency (milliseconds)

Seven fresh-process samples per cell; OS cache and CPU frequency uncontrolled.
Complete CSV output and stats publication are included in this clock.

**ESZ26**

| Window | Query | Selected records | Row | Column | Pruned | DuckDB |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| full | summary | 17,111,923 | 2918.82 | 1837.72 | 1835.47 | 912.98 |
| full | flow | 17,111,923 | 3028.09 | 2736.16 | 2739.28 | 1250.81 |
| full | bars 1m | 17,111,923 | 3186.55 | 2092.90 | 2085.87 | 3097.56 |
| full | bars 1h | 17,111,923 | 2964.18 | 1871.68 | 1870.31 | 2710.16 |
| day | summary | 1,663,822 | 2722.09 | 1487.39 | 191.42 | 374.30 |
| day | flow | 1,663,822 | 2860.03 | 2369.15 | 285.00 | 406.25 |
| day | bars 1m | 1,663,822 | 2781.00 | 1518.21 | 205.44 | 569.23 |
| day | bars 1h | 1,663,822 | 2732.53 | 1507.29 | 195.29 | 551.48 |
| ten_minutes | summary | 46,082 | 2706.41 | 1464.32 | 33.06 | 314.62 |
| ten_minutes | flow | 46,082 | 2845.80 | 2355.96 | 39.44 | 314.57 |
| ten_minutes | bars 1m | 46,082 | 2699.80 | 1452.41 | 32.70 | 316.83 |
| ten_minutes | bars 1h | 46,082 | 2710.48 | 1460.22 | 31.40 | 319.81 |

**NQZ26**

| Window | Query | Selected records | Row | Column | Pruned | DuckDB |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| full | summary | 7,692,171 | 1326.78 | 821.39 | 815.40 | 584.06 |
| full | flow | 7,692,171 | 1377.38 | 1250.13 | 1246.12 | 764.34 |
| full | bars 1m | 7,692,171 | 1563.29 | 1042.09 | 1048.98 | 1778.81 |
| full | bars 1h | 7,692,171 | 1351.35 | 826.13 | 827.77 | 1420.85 |
| day | summary | 731,731 | 1239.99 | 657.50 | 93.97 | 348.09 |
| day | flow | 731,731 | 1302.67 | 1082.19 | 138.21 | 373.01 |
| day | bars 1m | 731,731 | 1269.29 | 668.31 | 108.92 | 438.85 |
| day | bars 1h | 731,731 | 1233.96 | 655.55 | 96.25 | 418.25 |
| ten_minutes | summary | 19,858 | 1224.25 | 643.99 | 31.25 | 317.74 |
| ten_minutes | flow | 19,858 | 1288.57 | 1072.34 | 37.81 | 320.08 |
| ten_minutes | bars 1m | 19,858 | 1230.40 | 644.93 | 31.84 | 323.56 |
| ten_minutes | bars 1h | 19,858 | 1224.29 | 641.61 | 32.20 | 323.92 |

### Full-summary bytes and secondary clocks

Requested bytes include each header and two directory passes; exclude manifest
and filesystem metadata. They are application reads, not disk traffic.
Internal clocks remain distinct across native lattice and DuckDB as above.

| Contract | Variant | Requested bytes | Internal median ms | Records/internal second |
| --- | --- | ---: | ---: | ---: |
| ESZ26 | row | 821,401,552 | 2901.87 | 5,896,860 |
| ESZ26 | column | 410,781,160 | 1815.75 | 9,424,174 |
| ESZ26 | pruned | 410,781,160 | 1820.40 | 9,400,111 |
| ESZ26 | duckdb | Not equated | 594.79 | 28,769,824 |
| NQZ26 | row | 369,239,728 | 1308.70 | 5,877,725 |
| NQZ26 | column | 184,659,064 | 800.00 | 9,615,245 |
| NQZ26 | pruned | 184,659,064 | 793.01 | 9,699,968 |
| NQZ26 | duckdb | Not equated | 274.61 | 28,011,695 |

The secondary throughput figures use different internal timing boundaries;
they do not remove integrity, compression or query-planner differences.

### Evidence and remaining scope

![Summary latency and interquartile range](figures/summary-latency.png)

The x-axis represents the selected-record fraction for the three fixed windows,
not a randomized selectivity experiment. Both axes are logarithmic. Error bars
are the inclusive 25th/75th percentiles of the seven measured samples. The figure
is generated directly from the saved raw samples; [SVG export](figures/summary-latency.svg).
Optional plot reproduction uses a separate environment and has no effect on the
engine or benchmark dependencies:

```powershell
python -m venv .venv-plots
.venv-plots/Scripts/python.exe -m pip install -r tools/requirements-plots.txt
.venv-plots/Scripts/python.exe tools/plot_benchmark.py `
  --run bench/results/2026-10-05-row --output plot-rerun.png
```

- [All 864 invocations](../bench/results/2026-10-05-row/runs.csv).
- [Median/min/max/IQR for all 96 groups](../bench/results/2026-10-05-row/summary.csv).
- [Environment, workload counts and provenance](../bench/results/2026-10-05-row/metadata.json).

The separate Windows memory experiment in [VALIDATION.md](VALIDATION.md) is
not a per-benchmark peak-RSS measurement. A subsequent [CI validation phase](VALIDATION.md)
passed Linux Release/ASan/UBSan, a bounded coverage-guided campaign and a
separate Linux address-space-cap/RSS experiment. The 1M/10M/100M timing series
remains pending. Profiling
should precede any full-scan optimization; these measurements do not support
a universal performance claim. Historical three-variant samples remain intact.
