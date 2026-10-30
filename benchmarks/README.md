# Capability benchmarks

Run from the repository root:

```bash
python benchmarks/fetch_samples.py
python benchmarks/pdf_word_bench.py
```

Outputs go to `benchmarks/work/` (gitignored), never outside the project tree.
The PDF→Word benchmark records text recall, heading/table/image counts, runtime
and DOCX structure for each sample. `scanned.pdf` is expected to be rejected by
the current preflight; the benchmark records that failure as a result.
