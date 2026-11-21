"""IntelliCredit AI backend application package.

Modular monolith (ADR-0001): api/tasks -> workflows -> agents -> infrastructure
-> application -> domain, with ``app.core`` as the shared kernel importable by
every layer. Layering is enforced mechanically by import-linter (see
backend/pyproject.toml).

Phase 2 ships the production-quality skeleton; later phases fill the seams
noted in each module docstring.
"""

__version__ = "0.2.0"
