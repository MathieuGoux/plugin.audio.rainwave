import xbmcaddon
from .constants import *

class Auth:
    def __init__(self):
        self.addon = xbmcaddon.Addon("plugin.audio.rainwave")

    def get_user_id(self):
        return self.addon.getSetting("user_id")

    def get_api_key(self):
        return self.addon.getSetting("api_key")

    def get_auth_params(self):
        if not self.get_user_id() or not self.get_api_key():
            return {}
        return {"user_id": self.get_user_id(), "key": self.get_api_key()}
