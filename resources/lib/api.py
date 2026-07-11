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

    def _request(self, endpoint, params=None):
        if params is None:
            params = {}

        # Rainwave's API docs guarantee every endpoint accepts POST;
        # only a subset additionally accept GET (undocumented here which
        # ones). POSTing unconditionally, with the params as a
        # form-urlencoded body rather than a query string, works
        # everywhere -- this is also what the site's own JS client and
        # every official usage example do.
        data = urllib.parse.urlencode(params).encode("utf-8")
        url = f"{BASE}{endpoint}"

        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

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

    def tune_in(self, sid):
        # tune_in registers this session as an actual "listener" of the
        # station (relevant for e.g. the site's listener count). It is
        # NOT required to fetch now-playing data: Rainwave's own docs
        # show info() working as a plain, stateless call with just
        # "sid", no prior session needed -- "you can simply GET
        # http://rainwave.cc/api4/info?sid=1 to get a full JSON
        # payload". tune_in has been 404ing consistently for this
        # anonymous, credential-less client (likely expects an
        # authenticated user), so it must stay best-effort and never
        # gate get_station_info()/get_now_playing() -- see below.
        self.current_sid = sid
        return self._request("tune_in", {"sid": sid})

    def get_station_info(self, sid=None):
        sid = sid or self.current_sid
        self.current_sid = sid

        # No tune_in dependency here on purpose -- see tune_in()'s
        # comment above. info() is a self-contained, stateless call.
        return self._request("info", {"sid": sid})

    @staticmethod
    def _art_url(path):
        if not path:
            return ""
        return ART_FORMAT.format(path)

    def _parse_song(self, song):
        # Shared shape across sched_current.songs[], sched_next[].songs[]
        # and sched_history[].songs[] -- each song carries its own
        # "artists" list and "albums" list (an election can technically
        # have multiple album entries; the first is the one actually
        # tied to that song). Used both for the current-track fallback
        # below and for the previous/next songs in get_now_playing().
        artists = ", ".join(a["name"] for a in song.get("artists", []))
        albums = song.get("albums", [])
        album = albums[0] if albums else {}
        return {
            "title": song.get("title", ""),
            "artist": artists,
            "album": album.get("name", ""),
            "art": self._art_url(album.get("art", "")),
        }

    def get_now_playing(self, sid=None):
        sid = sid or self.current_sid
        info = self.get_station_info(sid)

        # info comes back {} on a failed request (network error, bad
        # response, etc -- see _request()). Returning None here, rather
        # than a dict full of blank fields, lets callers recognize "no
        # data this cycle" and simply leave whatever was already on
        # screen alone instead of overwriting good title/artist/album/
        # art with empty strings.
        if not info:
            return None

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
            result = self._parse_song(song)
            result["station"] = STATIONS.get(sid, "")

        # sched_next is an array of upcoming election events (soonest
        # first); sched_history is past events, most recent first --
        # confirmed directly against a live /api4/info response rather
        # than assumed.
        #
        # For sched_history/sched_current, songs[0] really is the
        # right song: those elections are already settled (voting
        # closed), so index 0 is the confirmed winner. sched_next is
        # different -- its election is still open, and each candidate
        # carries its own "entry_votes" field, but that field turned
        # out NOT to be live: it stayed frozen across repeated polls
        # even while the real vote count (visible on the website) kept
        # climbing. Rainwave tracks live vote tallies through a
        # separate real-time channel that isn't exposed by this
        # endpoint, so there's no reliable way to know the actual
        # current leader from here. Rather than presenting a guess
        # that looks authoritative but often isn't, every candidate is
        # returned here and the caller (widget.py) rotates through
        # them one at a time instead of picking one.
        sched_next = info.get("sched_next", [])
        next_songs = sched_next[0].get("songs", []) if sched_next else []
        result["next_candidates"] = [self._parse_song(s) for s in next_songs]

        sched_history = info.get("sched_history", [])
        history_songs = sched_history[0].get("songs", []) if sched_history else []
        result["previous"] = self._parse_song(history_songs[0]) if history_songs else {}

        result.update(timing)
        return result
