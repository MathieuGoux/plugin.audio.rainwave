import sys
from urllib.parse import parse_qs
import xbmc, xbmcplugin, xbmcgui


from .stations import StationMenu
from .player import Player
from .views import MainMenu
from .api import RainwaveAPI
from .game_art import GameArtProvider
from .history import HistoryMenu

class Router:
    def __init__(self):
        self.handle = int(sys.argv[1])
        self.params = parse_qs(sys.argv[2][1:])
        self.api = RainwaveAPI()

    def run(self):
        action = self.params.get("action", [None])[0]
        
        handle = int(sys.argv[1])

        if action is None:
            MainMenu(self.handle).show()
            xbmcplugin.endOfDirectory(self.handle)
            return

        if action == "stations":
            StationMenu(self.handle).show()
            xbmcplugin.endOfDirectory(self.handle)
            return

        if action == "history":
            HistoryMenu(self.handle, self.api).show_stations()
            return

        if action == "history_songs":
            sid = int(self.params["id"][0])
            HistoryMenu(self.handle, self.api).show_songs(sid)
            return
            
        if action == "play":
            sid = int(self.params["id"][0])

            # Bind the API session to this station so get_now_playing()
            # (used by the widget) reports the right station instead of
            # whatever self.api.current_sid last defaulted to.
            self.api.tune_in(sid)

            xbmcgui.Window(10000).setProperty(
                "Rainwave.CurrentStation",
                str(sid)
            )

            player = Player(self.api)

            # force Kodi to drop previous stream/session
            stream_url = player.get_stream_url(sid)

            xbmc.Player().stop()

            # Fetch current track metadata up front so we can attach it
            # to the actual playing ListItem below. Previously this data
            # only ever reached Window(10000) properties (see widget.py),
            # which the addon's own skin XML reads via $INFO[...] -- but
            # that's invisible to anything using the standard JSON-RPC
            # Player.GetItem/Player.GetProperties calls, which is how
            # Kore (and any other remote) reads now-playing info. Setting
            # it on the ListItem itself makes it visible there too.
            #
            # get_now_playing() returns None when the API call failed
            # this cycle (network hiccup, session not tuned in yet,
            # etc -- see api.py). That must never block playback itself:
            # the stream URL doesn't depend on this metadata at all, and
            # service.py's periodic refresh will fill the real info in
            # a few seconds once/if the API recovers. So treat a failed
            # fetch as "no metadata yet" and still play the stream.
            song = self.api.get_now_playing(sid) or {}

            listitem = xbmcgui.ListItem(path=stream_url)

            # setInfo("music", {...}) is the old, deprecated metadata
            # API -- Kodi has been warning about it on every single call
            # in the logs ("Please use the respective setter in
            # InfoTagMusic"). On this Kodi version that deprecated path
            # doesn't reliably populate the same underlying tag that
            # Player.GetItem/updateInfoTag() expose over JSON-RPC: it
            # doesn't throw, it just silently under-delivers, which
            # fits exactly what Kore was showing (artwork -- set via
            # the separate, non-deprecated setArt() call below -- but
            # never title/artist/album). Using getMusicInfoTag()'s own
            # setters instead is the modern, fully-supported path.
            tag = listitem.getMusicInfoTag()
            tag.setTitle(song.get("title", ""))
            tag.setArtist(song.get("artist", ""))
            tag.setAlbum(song.get("album", ""))
            tag.setMediaType("song")

            art = song.get("art", "")
            if art:
                listitem.setArt({"thumb": art, "icon": art})

            # This is a continuous internet radio stream, not a
            # fixed-duration track -- without this, Kodi doesn't know
            # that, and can misread a brief buffering stall as the
            # track having actually ended. When that happens it fires
            # onPlayBackEnded/onPlayBackError and tries to fetch a
            # "next" item from this plugin's directory to auto-advance
            # (there isn't one, hence the "GetDirectory - Error getting
            # noop" log line), then gives up and fully stops -- which is
            # what was killing service.py's polling for the rest of the
            # session every time the stream so much as blipped.
            # IsLive tells Kodi to treat interruptions as "keep
            # buffering/reconnecting", not "this track is over".
            listitem.setProperty("IsLive", "true")

            xbmc.log(f"[Rainwave] PLAYING URL = {stream_url}", xbmc.LOGINFO)

            xbmcplugin.setResolvedUrl(self.handle, True, listitem)

            # Inhibit Screensaver on play, so the widget is always shown

            xbmc.executebuiltin('InhibitScreensaver(true)')

            # The now-playing widget itself is shown/hidden by service.py,
            # which watches actual playback state via xbmc.Player callbacks.

            return

        if action == "request":
            self.api.request_song(int(self.params["song"][0]), int(self.params["station"][0]))
            xbmcgui.Dialog().notification("Rainwave", "Requested")
            xbmcplugin.setResolvedUrl(self.handle, False, xbmcgui.ListItem())
            return

        if action == "clear_art_cache":
            # Invoked via the "Clear art cache" button in Add-on
            # Settings (settings.xml: action="RunPlugin(...)"). This
            # runs in its own short-lived process, separate from the
            # long-running service.py -- so it constructs its own
            # GameArtProvider rather than reaching into the running
            # service's, which isn't accessible from here anyway.
            dialog = xbmcgui.Dialog()
            if dialog.yesno("Rainwave", "Delete all cached background art? This can't be undone."):
                GameArtProvider().clear()
                dialog.notification("Rainwave", "Art cache cleared")
            xbmcplugin.setResolvedUrl(self.handle, False, xbmcgui.ListItem())
            return
