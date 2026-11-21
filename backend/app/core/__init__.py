"""Shared kernel: configuration, logging, request context, errors, security.

``app.core`` sits outside the import-linter layer contract on purpose — it is
the one package every layer may import (ARCHITECTURE §3). Keep it dependency-light
and free of I/O.
"""
