"""
Repository abstract base class.

All data access objects inherit from BaseRepository.
This ensures a consistent interface and makes swapping backends straightforward.
"""

from __future__ import annotations

from abc import ABC
from typing import Any, Generic, List, Optional, TypeVar

ID = TypeVar("ID")
T = TypeVar("T")


class BaseRepository(ABC, Generic[T, ID]):
    """
    Generic repository interface.

    Concrete repositories (ClaimRepository, MemberRepository, etc.)
    will implement these in Phase 1+.

    Type parameters:
        T:  The domain/ORM model type this repository manages.
        ID: The type of the primary identifier.
    """

    async def get_by_id(self, id: ID) -> Optional[T]:
        raise NotImplementedError

    async def list_all(self, *, limit: int = 100, offset: int = 0) -> List[T]:
        raise NotImplementedError

    async def save(self, entity: T) -> T:
        raise NotImplementedError

    async def delete(self, id: ID) -> bool:
        raise NotImplementedError
