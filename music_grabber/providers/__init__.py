"""Download providers: Deezer (streamrip) + yt-dlp fallback.

``build_providers(config)`` returns the enabled providers in PROVIDER_ORDER. BPM Tagger's
Monochrome (Tidal proxy) provider was on hold and is not carried over.
"""

import logging

from .base import DownloadedFile, Provider, ProviderCandidate, TrackMeta
from .deezer import DeezerProvider
from .ytdlp import YtDlpProvider

log = logging.getLogger(__name__)

__all__ = ["Provider", "TrackMeta", "ProviderCandidate", "DownloadedFile",
           "DeezerProvider", "YtDlpProvider", "build_providers"]


def build_providers(config: dict) -> list[Provider]:
    order = [p.strip().lower() for p in str(config.get("provider_order", "deezer,ytdlp")).split(",") if p.strip()]
    built: list[Provider] = []
    for name in order:
        if name == "deezer":
            if config.get("deezer_arl"):
                built.append(DeezerProvider(config))
            else:
                log.info("Provider 'deezer' skipped (DEEZER_ARL not set)")
        elif name == "ytdlp":
            built.append(YtDlpProvider(config))
        else:
            log.warning("Unknown provider in PROVIDER_ORDER: %s", name)
    return built
