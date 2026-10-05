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

## Publication and Linux validation phase

The public repository is [DanielPastor05/lattice-market](https://github.com/DanielPastor05/lattice-market).
The initial commit `42d496cfdb4b08431bb0f32ada9db9a90591446a` passed all four
[GitHub Actions jobs](https://github.com/DanielPastor05/lattice-market/actions/runs/37337662759):
Ubuntu and Windows Release (engine, mutation and DuckDB suites), Linux/Clang
ASan + UBSan, and Windows/MSVC ASan. Earlier local results above are preserved
as historical evidence; their raw source hashes predate Git publication and can
also differ from a checkout's newline normalization.

The portable memory runner also passed all nine Windows queries after the
Linux branch was added: [portable-runner Windows report](validation-memory-windows-portable.json).
The existing 64-record and two-short-block fixtures seed a new optional libFuzzer
target. It includes the production translation unit behind a main-only guard;
there is no duplicate reader. Raw binary, repaired binary, JSON and source-line
paths handle expected rejection independently. Unexpected exceptions and
sanitizer diagnostics fail the campaign. The repair's cumulative payload hashing
is bounded by input size, even for overlapping descriptors. Input limit: 4 MiB;
JSON limit: 1 MiB; source-line limit: 4096 bytes.

Reproduce on Linux with Clang and a compatible C++20 standard library:

```sh
cmake -S . -B build-fuzz -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_CXX_COMPILER=clang++ -DLATTICE_ASAN=ON -DLATTICE_FUZZ=ON
cmake --build build-fuzz --parallel 2
python tools/make_fuzz_corpus.py --lattice build-fuzz/lattice --output fuzz-corpus
mkdir -p fuzz-artifacts
UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 ASAN_OPTIONS=abort_on_error=1 \
  build-fuzz/lattice_fuzz fuzz-corpus -seed=731 -max_len=4194304 -runs=20000 \
  -timeout=10 -rss_limit_mb=512 -artifact_prefix=fuzz-artifacts/ -print_final_stats=1
```

The new CI campaign is bounded to 20,000 executions and uploads its log/crash
artifacts, including on failure. A finite successful campaign is evidence for
those seeds/mutations, not a proof of complete parser safety. The dedicated
Linux memory job builds a separate Release binary, creates a 24M-record fixture
and runs all nine queries through GNU time with `RLIMIT_AS=256 MiB` set before
execution. GNU time measures each executed child's peak RSS independently. An
allocation-denial canary verifies the cap; timeouts kill the complete child
process group. This is a virtual-address-space cap, not an RSS-specific limit,
and sanitizers are not active in that constrained-memory run.

New libFuzzer and Linux memory execution results are pending the next pushed CI
run. The 1M/10M/100M timing series remains outside this validation phase.

### Four-variant real-data measurements

The recorded 0.2.0 study passed all 24 workloads in row/column/pruned/DuckDB,
including all complete-history bars and flow outputs. All 864 timed
invocations passed their output comparison: 192 warmups and 672 measured
samples. Source and binary fingerprints identify the original benchmark working
tree, before the later validation tooling changes.
See [PERFORMANCE.md](PERFORMANCE.md) for full results and historical evidence.
