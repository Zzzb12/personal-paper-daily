"""Private interest corpus providers."""

from .base import InterestReadResult, ZoteroGateway
from .zotero import ZoteroInterestProvider

__all__ = ["InterestReadResult", "ZoteroGateway", "ZoteroInterestProvider"]
