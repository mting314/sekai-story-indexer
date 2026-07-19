"""CDN image/asset URL builders for the story data source.

Endpoint paths verified against sekai-viewer's asset resolver. Base is the JP
asset CDN (``ASSET_CDN``); sekai.best serves ``.webp``. Swap ``.webp`` for the
locale/format you need via ``ext``.
"""

from __future__ import annotations

from .constants import ASSET_CDN


def _url(endpoint: str) -> str:
    return f"{ASSET_CDN}/{endpoint}"


def event_logo_url(assetbundle_name: str, ext: str = "webp") -> str:
    """Event title logo (transparent), good for timeline cards."""
    return _url(f"event/{assetbundle_name}/logo/logo.{ext}")


def event_banner_url(assetbundle_name: str, ext: str = "webp") -> str:
    """Home-screen banner artwork for the event."""
    return _url(f"home/banner/{assetbundle_name}/{assetbundle_name}.{ext}")


def event_background_url(assetbundle_name: str, ext: str = "webp") -> str:
    return _url(f"event/{assetbundle_name}/screen/bg.{ext}")


def event_character_art_url(assetbundle_name: str, ext: str = "webp") -> str:
    """Foreground character render used on the event screen."""
    return _url(f"event/{assetbundle_name}/screen/character.{ext}")


def music_jacket_url(assetbundle_name: str, ext: str = "webp") -> str:
    """Jacket art for a commissioned song."""
    return _url(f"music/jacket/{assetbundle_name}/{assetbundle_name}.{ext}")
