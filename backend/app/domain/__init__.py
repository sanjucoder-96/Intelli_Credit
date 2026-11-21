"""Pure domain layer: enums, self-validating value objects, read-model entities.

No I/O, no framework imports beyond pydantic. The lowest layer of the
import-linter contract — everything may depend on it, it depends on nothing
above it (ARCHITECTURE §3).
"""
