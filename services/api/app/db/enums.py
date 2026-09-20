"""Centralized enum definitions for DB CHECK constraints and Python validation.

These enums mirror the CHECK constraints defined in docs/DATABASE.md and are the
single source of truth used by SQLAlchemy column types, Pydantic schemas, and
verifier code. Any new enum value MUST be added here AND in the matching CHECK
constraint (in Alembic migration) AND in any Pydantic schemas that use it.
"""
from enum import StrEnum


class HomeRole(StrEnum):
    """User's role within a home."""

    OWNER = "owner"
    MEMBER = "member"


class RoomType(StrEnum):
    """Classification of a room. 'other' is the catch-all."""

    BEDROOM = "bedroom"
    KITCHEN = "kitchen"
    BATHROOM = "bathroom"
    STUDY = "study"
    LIVING = "living"
    STORAGE = "storage"
    OTHER = "other"


class StorageUnitType(StrEnum):
    """Type of storage furniture / container."""

    CABINET = "cabinet"
    SHELF = "shelf"
    DRAWER_CABINET = "drawer_cabinet"
    BOX = "box"
    OTHER = "other"


class StorageSectionType(StrEnum):
    """Type of a section inside a storage unit (a layer, drawer, box, etc.)."""

    LAYER = "layer"
    DRAWER = "drawer"
    BOX = "box"
    COMPARTMENT = "compartment"
    OTHER = "other"


class PlacementSource(StrEnum):
    """How an ItemPlacement was created."""

    AI_RECOMMENDATION = "ai_recommendation"
    USER_MANUAL = "user_manual"


class RecommendationStatus(StrEnum):
    """Lifecycle state of a Recommendation record.

    ``pending``    — AI returned a recommendation; awaiting user decision.
    ``accepted``   — user accepted the recommendation (or its edited form);
                     an ItemPlacement row was created.
    ``rejected``   — user rejected the recommendation; nothing was placed.
    ``superseded`` — a newer recommendation replaced this one (e.g. the user
                     re-ran ``POST /recommend`` on the same item and accepted
                     the new one). Kept for history / audit.
    """

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class AgentTraceStatus(StrEnum):
    """Final state of an Agent execution."""

    SUCCESS = "success"
    VERIFIER_FAILED = "verifier_failed"
    ERROR = "error"


class MessageRole(StrEnum):
    """Role of a message in a Conversation."""

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class HomeRuleType(StrEnum):
    """Hard rules must be enforced by Verifier; soft rules are ranking hints."""

    HARD = "hard"
    SOFT = "soft"


class AssetStatus(StrEnum):
    """Lifecycle state of an uploaded Asset.

    PENDING — row created, object upload in progress (or failed).
    READY   — uploaded and persisted; presigned URLs can be issued.
    FAILED  — upload attempt failed; safe to retry with a fresh upload.
    """

    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


__all__ = [
    "AgentTraceStatus",
    "AssetStatus",
    "HomeRole",
    "HomeRuleType",
    "MessageRole",
    "PlacementSource",
    "RecommendationStatus",
    "RoomType",
    "StorageSectionType",
    "StorageUnitType",
]
