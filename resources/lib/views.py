import xbmcplugin, xbmcgui

class MainMenu:
    def __init__(self, handle):
        self.handle = handle

    def _add(self, label, action):
        item = xbmcgui.ListItem(label)
        url = f"plugin://plugin.audio.rainwave/?action={action}"
        xbmcplugin.addDirectoryItem(self.handle, url, item, True)

    def show(self):
        self._add("Stations", "stations")
        self._add("History", "history")
        """self._add("Now Playing", "now")"""
