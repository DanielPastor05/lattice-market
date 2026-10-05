# Validation ledger

Executed locally on 2026-10-05, Windows x64, MSVC 19.43.34809.0, Python 3.14.4,
CMake's Visual Studio 2022 generator, Release configuration.

- Build passed with `/W4 /permissive-`.
- Seven Python integration tests passed in Release, both through CTest and
  directly. They include a 65,540-record deterministic randomized source that
  crosses the 65,536-record block boundary.
- The synthetic demo passed for summary, one-minute bars and estimated flow.
- Both complete real sources were imported, SHA-256 checked before/after, and
  published as immutable contract directories. A full `verify` subsequently
  checked every column and record in each published contract.
- All twelve real-data differential comparisons passed: three query kinds,
  two scan modes, two contracts. Window: 2026-09-10 14:00:00–14:01:00 UTC,
  183 ES records and 14 NQ records. Bars used one-second buckets. Exact values
  matched; VWAP/fractions met the documented tolerance.

| Contract | Verified records | Full-range volume | Minimum price | Maximum price |
| --- | ---: | ---: | ---: | ---: |
| ESZ26 | 17111923 | 22521586 | 7575.00 | 7848.50 |
| NQZ26 | 7692171 | 8281192 | 29053.00 | 31282.50 |

Full-range counts, volumes and extrema match the earlier independent source
inspection. The original twelve query comparisons cover the stated window only. The
subsequent experiment also checks full-range VWAP against an 80-digit Decimal
reference and compares every benchmark query to an independent source-text SQL
import; see the follow-up results below.

Machine-readable comparison evidence: [validation-real.json](validation-real.json).
The full verification establishes internal file consistency, not complete
exchange feed coverage or absence of missing trades in the original export.

Reproduce the real-data checks after import (choose a new report filename):

```powershell
python tools/validate_real.py --lattice build/Release/lattice.exe --data data `
  --source-dir "C:/Users/pasto/OneDrive/Documents/Proyectolattice" `
  --report docs/validation-real-rerun.json
```

At the initial milestone, Linux/macOS builds, remote GitHub Actions, sanitizers,
mutation campaigns and formal memory-limit tests had not been executed. Linux execution remains pending because this host
has no installed WSL environment. No cross-platform success is claimed.

## Follow-up milestone: independent SQL and repeated measurements

- Five additional DuckDB tests passed: small ordered/precision/quote cases,
  signed/unsigned extremes and overflow, source mutation, a 45-invocation
  benchmark smoke test, and a 65,540-row source-order fixture.
- DuckDB independently imported both complete original texts with exact integer
  ns/quarter-point prices and original row order, matching hashes/counts/timestamps.
- Both full-history summaries matched the 80-digit text/Decimal reference.
- A direct lattice-to-Decimal follow-up check also passed for both full-history
  summaries: [validation-full-summary.json](validation-full-summary.json).
- All 24 benchmark workloads matched independent SQL; column and pruned modes
  both passed, including all full-history minute/hour bars and flow aggregates.
- 648 timed CLI invocations completed: 144 warmups and 504 measured samples.
  Every output was compared outside its recorded clocks.

See [PERFORMANCE.md](PERFORMANCE.md) for the 24 workload results, precise timing
boundaries and links to individual samples/environment metadata. The SQL baseline
is independent of the lattice binary parser; the Decimal oracle covers complete
summaries. Full-history flow/bars use the independent SQL oracle, rather than a
separate full-history Decimal run for each query kind.

## Row layout, AddressSanitizer and memory milestone

Executed locally with engine 0.2.0 on the same Windows host:

- Nine engine test methods and five DuckDB test methods passed. Row tests cover
  exact bytes requested, duplicate timestamps, preserved short interior blocks,
  day transitions, incompatible/mixed layouts, repaired-CRC invalid bounds and
  conversion failure without publication. The benchmark smoke now makes 60
  invocations across all four variants (24 warmups and 36 measured samples).
- Release CTest passed all three suites: engine integration, 24 deterministic
  reader mutations and DuckDB differential checks.
- MSVC ASan RelWithDebInfo CTest passed engine integration and reader mutations.
  The instrumented executable includes the MSVC ASan runtime. No local UBSan
  or Linux sanitizer execution is claimed.
- An additional seeded 256-case ASan mutation campaign passed: 196 rejected
  inputs and 60 accepted inputs, with no unexpected exits or sanitizer reports.
  Accepted mutations can leave valid values, unused provenance strings or
  unchanged metadata. This is a bounded single-block CLI campaign, not a proof
  that every malformed file is rejected. The runner mutates header/directory
  bounds with repaired CRCs, payload with repaired CRCs, raw/truncated files and
  manifest JSON. Each process has a ten-second timeout. It does not measure code
  coverage and does not replace the planned coverage-guided fuzzer.

Evidence: [ASan mutations](validation-mutations-asan.json).

### Windows memory cap

The deterministic generator produced 24,000,000 synthetic records, adjacent
pairs with equal timestamps. It has 1,152,000,000 bytes of uncompressed record
payload (>1 GiB), independently of headers/directories. Source text is
1,272,000,000 bytes; it is not a licensed market export. Price and volume periods
divide 1000, and all timestamps land within one minute. Expected summary/flow/bar
outputs are calculated with Decimal for one period and exact count/volume
scaling. There is one bar and the final period preserves the expected close.

All summary, flow and one-minute bar queries passed in row, column and pruned
modes, under a **256 MiB process committed-memory cap**. There is no selective
pruning in this full-fixture check. Observed peak commit was at most 3,952,640
bytes; peak working set was at most 7,643,136 bytes. The reader still scans the
entire selected record payload using bounded block buffers.

The native helper creates a suspended process, assigns its Job Object before
resuming it, then reports peak committed memory and peak working set. A canary
requesting a 128 MiB allocation under a 64 MiB cap returned the expected
`MemoryError`, verifying enforcement. This uses the Windows
[process-memory limit](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information)
and [working-set counters](https://learn.microsoft.com/en-us/windows/win32/api/psapi/ns-psapi-process_memory_counters).
It establishes Windows query evidence for this fixture; Linux RSS/rlimit,
importer memory and arbitrary daily directory growth are separate requirements.
ASan was not active in the constrained-memory run.

Evidence: [Windows memory report](validation-memory-windows.json), including
source/generator/binary/probe hashes, exact outputs, nine sets of native counters
and the enforcement canary. Reproduce in a new ignored directory:

```powershell
python tools/generate_ticks.py --output data-stress/rerun/source.txt --rows 24000000
python tools/import_dataset.py --lattice build/Release/lattice.exe `
  --input data-stress/rerun/source.txt --contract ESZ26 --data data-stress/rerun/column
python tools/import_dataset.py --lattice build/Release/lattice.exe `
  --row-source data-stress/rerun/column --contract ESZ26 --data data-stress/rerun/row
$stressHash = (Get-Content data-stress/rerun/column/ESZ26/manifest.json | ConvertFrom-Json).source_sha256
python tools/check_memory.py --lattice build/Release/lattice.exe `
  --probe build/Release/memory_probe.exe --data data-stress/rerun/column `
  --row-data data-stress/rerun/row --rows 24000000 --source-sha256 $stressHash `
  --report memory-rerun.json
```

This fixture checks memory, not the planned 1M/10M/100M timing series.

### Four-variant real-data measurements

The current 0.2.0 study passed all 24 workloads in row/column/pruned/DuckDB,
including all complete-history bars and flow outputs. All 864 timed
invocations passed their output comparison: 192 warmups and 672 measured
samples. Source and binary fingerprints match the finished working tree.
See [PERFORMANCE.md](PERFORMANCE.md) for full results and historical evidence.
