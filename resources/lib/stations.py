import xbmcgui, xbmcplugin, sys
from .constants import STATIONS
from .artwork import Artwork

class StationMenu:
    def __init__(self, handle):
        self.handle = handle
        self.art = Artwork()

    def show(self):
        base_url = sys.argv[0]

        for sid, name in STATIONS.items():

            url = f"{base_url}?action=play&id={sid}"

            item = xbmcgui.ListItem(label=name)

            item.setArt({
                "thumb": self.art.station(name),
                "icon": self.art.icon(),
                "fanart": self.art.fanart()
            })

            # CRITICAL: mark as playable item
            item.setProperty("IsPlayable", "true")

            xbmcplugin.addDirectoryItem(
                handle=self.handle,
                url=url,
                listitem=item,
                isFolder=False   # <-- IMPORTANT CHANGE
            )

        xbmcplugin.endOfDirectory(self.handle)
