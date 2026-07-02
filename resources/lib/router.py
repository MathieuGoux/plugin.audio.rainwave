import sys
from urllib.parse import parse_qs
import xbmc, xbmcplugin, xbmcgui


from .stations import StationMenu
from .player import Player
from .views import MainMenu
from .api import RainwaveAPI

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

            listitem = xbmcgui.ListItem(path=stream_url)
            
            xbmc.log(f"[Rainwave] PLAYING URL = {stream_url}", xbmc.LOGINFO)

            xbmcplugin.setResolvedUrl(self.handle, True, listitem)
            
            #Inhibit Screensaver on play, so the widget is always shown
            
            xbmc.executebuiltin('InhibitScreensaver(true)')

            # The now-playing widget itself is shown/hidden by service.py,
            # which watches actual playback state via xbmc.Player callbacks.

            return

        if action == "vote":
            api.vote(int(self.params["song"][0]), int(self.params["rating"][0]))
            xbmcgui.Dialog().notification("Rainwave", "Vote sent")
            return

        if action == "request":
            api.request_song(int(self.params["song"][0]), int(self.params["station"][0]))
            xbmcgui.Dialog().notification("Rainwave", "Requested")
            return
