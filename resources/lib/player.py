import xbmc
from .api import RainwaveAPI

STATIONS = {
    1: "game",
    2: "ocremix",
    3: "covers",
    4: "chiptune",
    5: "all",
    6: "chill",
}

class Player:
    def __init__(self, api):
        self.api = api

    def get_stream_url(self, sid):
        # "|Icy-MetaData=0" tells Kodi's player core not to read the
        # relay stream's own embedded ICY metadata. Without this, Kodi
        # treats the stream's in-band title tag as authoritative for
        # the currently-playing item and silently overwrites whatever
        # title/artist/album we set via setResolvedUrl()/updateInfoTag()
        # -- the album art survives (ICY never carries art), but every
        # other field gets clobbered almost immediately, and every
        # subsequent update from service.py loses the same race. This
        # is exactly why Kore only ever shows the first song's artwork
        # and never updates: the stream itself keeps re-asserting its
        # own (blank/generic) metadata over ours. Disabling ICY parsing
        # makes our own metadata the only source Kodi (and therefore
        # any JSON-RPC client like Kore) ever sees.
        return f"https://relay.rainwave.cc/{STATIONS[sid]}.mp3|Icy-MetaData=0"