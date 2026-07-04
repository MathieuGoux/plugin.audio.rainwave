import json
import time
import urllib.request
import urllib.parse
import http.cookiejar

import xbmc

from .constants import STATIONS, USER_AGENT

BASE = "https://rainwave.cc/api4/"
ART_FORMAT = "https://rainwave.cc{0}_320.jpg"


class RainwaveAPI:
    def __init__(self):
        self.cookiejar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookiejar)
        )
        self.current_sid = 5
        self.bootstrapped = False

    def _request(self, endpoint, params=None):
        if params is None:
            params = {}

        query = urllib.parse.urlencode(params)
        url = f"{BASE}{endpoint}"
        if query:
            url += f"?{query}"

        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

        try:
            with self.opener.open(req, timeout=10) as r:
                raw = r.read().decode("utf-8", errors="ignore")

                xbmc.log(f"[Rainwave] RAW {endpoint}: {raw[:300]}", xbmc.LOGDEBUG)

                if not raw.strip():
                    return {}

                return json.loads(raw)

        except Exception as e:
            xbmc.log(f"[Rainwave] ERROR {endpoint}: {e}", xbmc.LOGERROR)
            return {}

    def bootstrap(self, sid):
        data = self._request("bootstrap", {"sid": sid})
        self.current_sid = sid
        self.bootstrapped = True
        return data

    def tune_in(self, sid):
        self.bootstrap(sid)   # IMPORTANT: ensures session binds station
        return self._request("tune_in", {"sid": sid})

    def get_station_info(self, sid=None):
        sid = sid or self.current_sid

        # Rainwave binds a session to a station via the "rw_sid" cookie
        # as soon as any request is made, and that binding then takes
        # priority over the "sid" query parameter on every later
        # request through the same session/cookiejar -- see bootstrap()
        # below. This object's cookiejar lives for as long as the
        # RainwaveAPI instance does (in service.py, that's the whole
        # Kodi session), so without this check the FIRST station ever
        # queried through it gets "stuck": every later info() call
        # keeps silently returning that first station's data no matter
        # what sid is actually requested. Re-bootstrapping whenever the
        # requested sid changes keeps the cookie and the sid argument
        # in sync, so switching stations always gets that station's
        # real data instead of stale ones from whichever station
        # happened to be polled first.
        if sid != self.current_sid or not self.bootstrapped:
            self.bootstrap(sid)

        return self._request("info", {"sid": sid})

    @staticmethod
    def _art_url(path):
        if not path:
            return ""
        return ART_FORMAT.format(path)

    def get_now_playing(self, sid=None):
        sid = sid or self.current_sid
        info = self.get_station_info(sid)

        # "all_stations_info" gives us exactly what a now-playing widget
        # needs in one place -- title/album/art/artists for every
        # station -- without having to pick apart sched_current.songs[].
        station_info = info.get("all_stations_info", {}).get(str(sid))

        # sched_current carries the timing data needed to draw a
        # progress bar: "start_actual" is the unix timestamp (server
        # clock) the song actually started playing, songs[0]["length"]
        # is that song's duration in seconds. "api_info.time" is the
        # server's own clock at the moment it answered -- we hand it
        # back so the caller can correct for any drift between the
        # Kodi box's clock and Rainwave's, instead of trusting
        # time.time() to line up with start_actual.
        sched_current = info.get("sched_current", {})
        songs = sched_current.get("songs", [])
        song = songs[0] if songs else {}

        timing = {
            "start_actual": sched_current.get("start_actual"),
            "length": song.get("length") or sched_current.get("length"),
            "server_time": info.get("api_info", {}).get("time", time.time()),
        }

        if station_info:
            result = {
                "title": station_info.get("title", ""),
                "artist": station_info.get("artists", ""),
                "album": station_info.get("album", ""),
                "art": self._art_url(station_info.get("art", "")),
                "station": STATIONS.get(sid, ""),
            }
        else:
            # Fallback if all_stations_info wasn't present for some
            # reason: pick the info apart from sched_current.songs
            # directly (an election can have several candidates
            # queued; the currently-playing one is index 0).
            artists = ", ".join(a["name"] for a in song.get("artists", []))
            albums = song.get("albums", [])
            album = albums[0] if albums else {}

            result = {
                "title": song.get("title", ""),
                "artist": artists,
                "album": album.get("name", ""),
                "art": self._art_url(album.get("art", "")),
                "station": STATIONS.get(sid, ""),
            }

        result.update(timing)
        return result
