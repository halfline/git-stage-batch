# Matching benchmarks

The matching benchmark exercises the public line-matching and ownership
attribution APIs. It uses deterministic fixtures and emits JSON so results can
be retained and compared across revisions.

Run the pull-request-sized suite from a source checkout:

```console
python scripts/benchmark_matching.py --mode quick --output matching.json
```

The full suite increases the repeated-line, low-similarity, reversed-order,
Unicode, and many-batch workloads and adds a 50,000-line sparse-edit case:

```console
python scripts/benchmark_matching.py --mode full --output matching-full.json
```

Use `--case NAME` to isolate a regression. The option can be repeated. Use
`--seed`, `--warmups`, and `--repeats` to control reproducibility and sampling.
The defaults are one warm-up and three samples in quick mode, and two warm-ups
and seven samples in full mode. The full suite can take several minutes on a
typical workstation.

## Reading results

Each measured phase reports every sample plus minimum, median, 95th percentile,
and maximum elapsed time and peak Python allocation. Elapsed-time samples run
without allocation tracing. Peak-allocation samples run separately, with at
most three samples per phase, so `tracemalloc` does not distort the timing
results. Fixture generation, repository creation, and separately managed phase
prerequisites and cleanup happen outside the timer. End-to-end public APIs still
include any cleanup that is intrinsic to the operation.

Synthetic text is coalesced into bounded 64 KiB byte chunks before measurement,
so large cases do not retain a Python object for every line alongside the
line-addressable buffers.

The phases are:

- `buffer_loading`: create line-addressable buffers and build their line indexes.
- `git_object_resolution`: resolve the expressions needed by batch claims.
- `blob_loading`: stream source blobs into bounded line buffers.
- `mapping`: construct and traverse the structural line mapping.
- `unit_attribution`: enumerate changed units from an already-built comparison.
- `claim_attribution`: run file attribution end to end, including Git I/O,
  matching, and ownership claims.

The many-batch case keeps its file dimensions fixed between modes so it isolates
the cost of increasing the number of claims.

The report records the seed, sample counts, project revision, project version,
Python, Git, platform, input dimensions, and content hashes.
`tracemalloc_peak_bytes` measures Python allocator activity; it is not total
process resident memory. Tracing adds overhead, so compare runs made with the
same settings and on comparable hardware. Reports also identify a dirty tracked
working tree so results made from uncommitted code are not mistaken for their
recorded revision.

The `binary-exclusion` case records that NUL-containing input is intentionally
excluded from text matching. It has no measured phases.
