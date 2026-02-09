"""yt-artist: fetch channel URLs, transcribe videos, summarize with AI."""

import logging

__version__ = "0.1.0"

# Package-level logger; handlers are attached in cli.main() so library use stays quiet.
logger = logging.getLogger("yt_artist")
