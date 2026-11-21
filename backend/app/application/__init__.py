"""Application layer: use cases and ports (typing.Protocol).

Depends only on ``app.domain`` and the ``app.core`` shared kernel; concrete
adapters live in ``app.infrastructure`` and are wired in the composition root
``app.api.providers`` (ADR-0001). Phase 4 adds StartRun/IngestDocument use
cases; Phase 5 adds SubmitReview/GenerateReport.
"""
