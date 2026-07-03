import random

import xbmcaddon
import xbmcgui
import xbmcvfs

from .utils import log

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".bmp")

# How long before a swap we start loading the next image into the
# hidden slot. Kodi still processes/loads textures for controls with
# <visible>false</visible>, so referencing the file here gets it
# decoded and cached ahead of time -- that's what avoids the flash.
PRELOAD_LEAD = 1.0  # seconds


class Slideshow:
    """Crossfades a background picture via Window(10000) properties.

    Kodi's <multiimage> control only accepts a literal integer for
    <timeperimage> -- it can't be driven by a setting. A single
    <image> control with <texture fadetime="..."> can be driven by a
    setting, but swapping one texture in place means Kodi has to
    decode the new file from disk *during* the transition, which
    shows a brief gap of whatever's behind the dialog.

    This class instead double-buffers between two slots (A/B): the
    next image is written into whichever slot is currently hidden
    a moment before the swap, so it's already decoded and cached by
    the time we flip Rainwave.SlideshowActive. The skin then crossfades
    the two controls via a VisibleChange animation.
    """

    PATH_PROP = "Rainwave.SlideshowPath"
    ACTIVE_PROP = "Rainwave.SlideshowActive"
    IMAGE_PROPS = {"A": "Rainwave.SlideshowImageA", "B": "Rainwave.SlideshowImageB"}

    def __init__(self):
        self.home = xbmcgui.Window(10000)
        self.files = []
        self.index = -1
        self.interval = 8
        self.enabled = False
        self.active_slot = "A"
        self.next_change = 0
        self.preloaded = False
        self.reload_settings()

    def reload_settings(self):
        addon = xbmcaddon.Addon()
        self.enabled = addon.getSettingBool("slideshow_enabled")
        path = addon.getSettingString("slideshow_path")
        self.interval = max(2, addon.getSettingInt("slideshow_time"))

        if self.enabled and path:
            self.home.setProperty(self.PATH_PROP, path)
            self._scan(path)
        else:
            self.home.clearProperty(self.PATH_PROP)
            self.home.clearProperty(self.ACTIVE_PROP)
            for prop in self.IMAGE_PROPS.values():
                self.home.clearProperty(prop)
            self.files = []

        self.index = -1
        self.active_slot = "A"
        self.preloaded = False
        self.next_change = 0  # show an image immediately on the next tick

    def _scan(self, path):
        try:
            _dirs, files = xbmcvfs.listdir(path)
        except Exception:
            files = []
            log(f"Slideshow: could not list {path}")

        self.files = [f for f in files if f.lower().endswith(IMAGE_EXTS)]
        random.shuffle(self.files)

        if not self.files:
            log(f"Slideshow: no images found in {path}")

    def _full_path(self, filename):
        path = self.home.getProperty(self.PATH_PROP)
        if not path.endswith(("/", "\\")):
            path += "/"
        return path + filename

    def _next_file(self):
        self.index += 1
        if self.index >= len(self.files):
            self.index = 0
            random.shuffle(self.files)
        return self.files[self.index]

    def tick(self, now):
        """Call regularly (e.g. every second) from the service loop."""
        if not (self.enabled and self.files):
            return

        if self.next_change == 0:
            # first image: nothing on screen yet, so just show it directly
            self.home.setProperty(self.IMAGE_PROPS["A"], self._full_path(self._next_file()))
            self.home.setProperty(self.ACTIVE_PROP, "A")
            self.active_slot = "A"
            self.preloaded = False
            self.next_change = now + self.interval
            return

        lead = min(PRELOAD_LEAD, self.interval / 2)
        time_left = self.next_change - now

        if not self.preloaded and time_left <= lead:
            hidden_slot = "B" if self.active_slot == "A" else "A"
            self.home.setProperty(self.IMAGE_PROPS[hidden_slot], self._full_path(self._next_file()))
            self.preloaded = True

        if now >= self.next_change:
            self.active_slot = "B" if self.active_slot == "A" else "A"
            self.home.setProperty(self.ACTIVE_PROP, self.active_slot)
            self.next_change = now + self.interval
            self.preloaded = False
