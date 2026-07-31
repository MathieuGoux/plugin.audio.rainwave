import json
import os
import urllib.error
import urllib.parse
import urllib.request

import xbmcaddon
import xbmcgui
import xbmc
import xbmcvfs
from .utils import log
from datetime import datetime

API_BASE = "https://www.steamgriddb.com/api/v2"
CACHE_SUBDIR = "art_cache"
MANIFEST_NAME = "manifest.json"
CACHE_SCHEMA_VERSION = 13

class GameSelectorDialog:
    def __init__(self, api_key, current_album=None, current_sid=None, current_song_title=None, current_game=None):
        self.api_key = api_key
        self.current_album = current_album #Album currently playing
        self.current_sid = current_sid #Station currently playing
        self.current_song_title = current_song_title #Song currently playing
        self.current_game = current_game #Game chosen by logic
        self.selected_game = None

    def show(self):
        """Prompt for a game name, then display matching games."""
        if not self.api_key:
            xbmcgui.Dialog().ok(
                "SteamGridDB",
                "SteamGridDB API key is not configured."
            )
            return False

        # Debug: show what we received
        log(f"GameSelector: album={self.current_album}, song={self.current_song_title}")

        prompt = f"Search for a game"
        if self.current_game:
            prompt = f"Search for a game (Current: {self.current_game})"
        elif self.current_album:
            prompt = f"Search for a game (Album: {self.current_album})"

        keyboard = xbmc.Keyboard("", prompt)

        #keyboard = xbmc.Keyboard("", "Search for a game")
        keyboard.doModal()

        if not keyboard.isConfirmed():
            return False

        query = keyboard.getText().strip()

        if len(query) < 2:
            xbmcgui.Dialog().ok(
                "SteamGridDB",
                "Please enter at least two characters."
            )
            return False

        games = self._search_games(query)

        if not games:
            xbmcgui.Dialog().ok(
                "SteamGridDB",
                "No matching games found."
            )
            return False

        names = []

        for g in games:

            name = g.get("name", "Unknown")
            release_date = g.get("release_date")
            year = g.get("year") or g.get("release_year")

            if not year and release_date is not None:
                if isinstance(release_date, str):
                    year = release_date.split("-", 1)[0]

                elif isinstance(release_date, int):
                    if 1900 <= release_date <= 2100:
                        # Looks like a calendar year
                        year = release_date
                    else:
                        # Assume Unix timestamp
                        year = datetime.utcfromtimestamp(release_date).year

            if year:
                names.append(f"{name} ({year})")
            else:
                names.append(name)

        selected = xbmcgui.Dialog().select(
            "Select Game",
            names
        )

        if selected == -1:
            return False

        self.selected_game = {
            "id": str(games[selected]["id"]),
            "name": games[selected]["name"]
        }

        # Save manual override if we have an album title
        if self.current_album:
            # Ask user: song-level or album-level override?
            if self.current_song_title:
                choices = [
                    f"Song only: '{self.current_song_title}'",
                    f"Entire album: '{self.current_album}'"
                ]
                override_type = xbmcgui.Dialog().select(
                    "Override scope",
                    choices
                )
                # If user cancels, default to song-level
                is_song_override = override_type == 0 if override_type != -1 else True
            else:
                # No song title available, must be album-level
                is_song_override = False

            self._save_manual_override(
                self.current_album,
                self.selected_game,
                is_song_override
            )

        return True
        
    def _open_game_selector(self):
        """Open game selector and update manifest with manual override."""
        try:
            from .game_selector import GameSelectorDialog
            addon = xbmcaddon.Addon()
            # Get current album and song from the now-playing widget
            current_album = xbmcgui.Window(10000).getProperty("Rainwave.Album")
            # Try multiple property names for song title
            current_song_title = (xbmcgui.Window(10000).getProperty("Rainwave.Song") or
                                 xbmcgui.Window(10000).getProperty("Rainwave.NowPlaying") or
                                 xbmcgui.Window(10000).getProperty("Rainwave.Track"))
            current_sid = xbmcgui.Window(10000).getProperty("Rainwave.CurrentStation")
            current_sid = int(current_sid) if current_sid else None

            dialog = GameSelectorDialog(
                addon.getSettingString("steamgriddb_api_key"),
                current_album=current_album,
                current_sid=current_sid,
                current_song_title=current_song_title
            )
            if dialog.show():
                # Update slideshow immediately
                xbmcgui.Window(10000).setProperty(
                    "Rainwave.ManualGame",
                    dialog.selected_game.get("name", "")
                )
        except Exception as e:
            log(f"Game selector failed: {e}")
        
    def _search_games(self, query):

        if len(query.strip()) < 2:
            return []

        query = urllib.parse.quote(query)

        url = f"{API_BASE}/search/autocomplete/{query}"

        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "Kodi"
            }
        )

        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())["data"]
            
    def _save_manual_override(self, album_title, selected_game, is_song_override=False):
        """Save manual override at song-level or album-level."""
        if not album_title or not selected_game:
            return

        addon = xbmcaddon.Addon()
        profile = xbmcvfs.translatePath(addon.getAddonInfo("profile"))
        cache_dir = os.path.join(profile, CACHE_SUBDIR)
        manifest_path = os.path.join(cache_dir, MANIFEST_NAME)

        # Load existing manifest
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            manifest = {"_schema": CACHE_SCHEMA_VERSION, "games": {}, "manual_overrides": {}}

        # Ensure schema is up to date
        manifest["_schema"] = CACHE_SCHEMA_VERSION

        # Initialize manual_overrides if missing
        if "manual_overrides" not in manifest:
            manifest["manual_overrides"] = {}

        # Save based on override type
        if is_song_override and self.current_song_title:
            # Song-level override
            if album_title not in manifest["manual_overrides"]:
                manifest["manual_overrides"][album_title] = {}
            manifest["manual_overrides"][album_title][self.current_song_title] = selected_game.get("name", "")
        else:
            # Album-level override
            manifest["manual_overrides"][album_title] = selected_game.get("name", "")

        # Save back to disk
        try:
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)
        except Exception as e:
            xbmcgui.Dialog().ok("Error", f"Failed to save override: {e}")
            