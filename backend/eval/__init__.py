"""IntelliCredit Phase-8 evaluation harness.

A small, reproducible evaluation framework over the real pipeline. It measures
what can be measured honestly and labels each measurement by kind
(deterministic correctness, public-data ML, LLM-qualitative, live-service) —
it never fabricates an accuracy number.

Modules:
- ``extraction_corpus``  — document corpus with ground truth + a deterministic
  extraction scorer (precision/recall/numeric/page/classification).
- ``acceptance``         — assembles the end-to-end acceptance report.
"""
