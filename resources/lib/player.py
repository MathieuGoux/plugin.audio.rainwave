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
        return f"https://relay.rainwave.cc/{STATIONS[sid]}.mp3"