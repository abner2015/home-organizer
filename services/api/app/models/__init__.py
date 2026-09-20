"""ORM models package — re-exports for Alembic autogenerate & convenient imports.

Importing this package registers all models on `Base.metadata`, which is what
Alembic uses to detect schema drift. Order: base → leaf-free → dependent.
"""
from app.models.asset import Asset
from app.models.conversation import Conversation, Message
from app.models.home import Home, HomeMembership
from app.models.item import Item, ItemImage
from app.models.placement import ItemPlacement
from app.models.preference import UserPreference
from app.models.recommendation import Recommendation
from app.models.room import Room
from app.models.rule import HomeRule
from app.models.storage import StorageSection, StorageSlot, StorageUnit
from app.models.trace import AgentTrace
from app.models.user import User

__all__ = [
    "AgentTrace",
    "Asset",
    "Conversation",
    "Home",
    "HomeMembership",
    "HomeRule",
    "Item",
    "ItemImage",
    "ItemPlacement",
    "Message",
    "Recommendation",
    "Room",
    "StorageSection",
    "StorageSlot",
    "StorageUnit",
    "User",
    "UserPreference",
]
