import xbmcgui


class Widget:
    """Writes now-playing data to Home (10000) window properties.

    The now-playing skin XML reads these via $INFO[Window(10000)
    .Property(...)] expressions, so calling refresh() is all that's
    needed to update whatever's currently on screen.
    """

    PREFIX = "Rainwave."
    KEYS = ("Title", "Artist", "Album", "Art", "Station")

    def __init__(self, api):
        self.api = api
        self.window = xbmcgui.Window(10000)

    def refresh(self, sid=None):
        song = self.api.get_now_playing(sid)

        self.window.setProperty(self.PREFIX + "Title", song["title"])
        self.window.setProperty(self.PREFIX + "Artist", song["artist"])
        self.window.setProperty(self.PREFIX + "Album", song["album"])
        self.window.setProperty(self.PREFIX + "Art", song["art"])
        self.window.setProperty(self.PREFIX + "Station", song["station"])

        # Timing fields (start_actual/length/server_time) aren't shown
        # via $INFO like the rest -- the progress bar needs live
        # per-second updates that a static window property can't give
        # us, so the caller feeds these straight to the dialog object
        # instead. Returning song here just avoids a second API call.
        return song

    def clear(self):
        for key in self.KEYS:
            self.window.clearProperty(self.PREFIX + key)
