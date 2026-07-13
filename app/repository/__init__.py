"""Persistence module: LoanRepository ABC plus in-memory and Supabase stores."""

from __future__ import annotations

import logging

from app.repository.base import LoanRepository
from app.repository.memory import InMemoryLoanRepository

logger = logging.getLogger("coin_master")


def build_repository() -> LoanRepository:
    """Pick the store based on configuration.

    Supabase when credentials are present, otherwise an in-memory store so the
    API can be driven end-to-end without a database.
    """
    from app.repository.config import settings as supabase_settings

    if supabase_settings.enabled:
        from app.repository.supabase import SupabaseLoanRepository

        logger.info("Using Supabase repository at %s", supabase_settings.url)
        return SupabaseLoanRepository(supabase_settings)
    logger.warning("Supabase not configured -- using in-memory repository")
    return InMemoryLoanRepository()


__all__ = ["LoanRepository", "InMemoryLoanRepository", "build_repository"]
