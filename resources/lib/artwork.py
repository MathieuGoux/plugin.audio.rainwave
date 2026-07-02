class Artwork:
    def icon(self):
        return "special://home/addons/plugin.audio.rainwave/icon.png"

    def fanart(self):
        return "special://home/addons/plugin.audio.rainwave/resources/media/fanart.png"

    def station(self, name):
        return f"special://home/addons/plugin.audio.rainwave/resources/media/stations/{name}.png"
