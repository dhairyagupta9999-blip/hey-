"""Optional Spotify Web API Client for Hey Jev on Windows.

Provides an optional alternative to desktop Win32/SMTC control using Spotify's official
REST API endpoints. Requires SPOTIFY_CLIENT_ID and SPOTIFY_REFRESH_TOKEN in
Windows Credential Manager or .env.

Endpoints implemented:
- PUT  https://api.spotify.com/v1/me/player/play
- PUT  https://api.spotify.com/v1/me/player/pause
- POST https://api.spotify.com/v1/me/player/next
- POST https://api.spotify.com/v1/me/player/previous
- PUT  https://api.spotify.com/v1/me/player/volume
"""
import os
import requests
from typing import Optional, Dict, Any
from secrets_store import get_secret

SPOTIFY_API_BASE = "https://api.spotify.com/v1/me/player"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"

class SpotifyWebApiClient:
    """Client for Spotify Web API playback control."""
    def __init__(self):
        self.client_id = get_secret("SPOTIFY_CLIENT_ID")
        self.client_secret = get_secret("SPOTIFY_CLIENT_SECRET")
        self.refresh_token = get_secret("SPOTIFY_REFRESH_TOKEN")
        self._access_token = None

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.refresh_token)

    def _refresh_access_token(self) -> bool:
        """Obtain a fresh OAuth2 access token using the stored refresh token."""
        if not self.is_configured:
            return False
        try:
            resp = requests.post(
                SPOTIFY_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                timeout=5
            )
            if resp.status_code == 200:
                self._access_token = resp.json().get("access_token")
                return True
        except Exception as e:
            print(f"[Spotify API] Token refresh failed: {e}")
        return False

    def _headers(self) -> Dict[str, str]:
        if not self._access_token:
            self._refresh_access_token()
        return {"Authorization": f"Bearer {self._access_token}"}

    def _request(self, method: str, endpoint: str, **kwargs) -> Optional[requests.Response]:
        if not self.is_configured:
            return None
        url = f"{SPOTIFY_API_BASE}/{endpoint.lstrip('/')}"
        headers = self._headers()
        try:
            r = requests.request(method, url, headers=headers, timeout=5, **kwargs)
            if r.status_code == 401:
                # Token expired, retry once
                if self._refresh_access_token():
                    headers = self._headers()
                    r = requests.request(method, url, headers=headers, timeout=5, **kwargs)
            return r
        except Exception as e:
            print(f"[Spotify API] Request error ({method} {endpoint}): {e}")
            return None

    def play(self) -> bool:
        """Resume playback on the user's active device."""
        r = self._request("PUT", "play")
        return bool(r and r.status_code in (200, 204))

    def pause(self) -> bool:
        """Pause playback on the user's active device."""
        r = self._request("PUT", "pause")
        return bool(r and r.status_code in (200, 204))

    def next_track(self) -> bool:
        """Skip to next track."""
        r = self._request("POST", "next")
        return bool(r and r.status_code in (200, 204))

    def previous_track(self) -> bool:
        """Skip to previous track."""
        r = self._request("POST", "previous")
        return bool(r and r.status_code in (200, 204))

    def set_volume(self, percent: int) -> bool:
        """Set playback volume (0-100%)."""
        clamped = max(0, min(100, int(percent)))
        r = self._request("PUT", f"volume?volume_percent={clamped}")
        return bool(r and r.status_code in (200, 204))

    def get_playback_state(self) -> Optional[Dict[str, Any]]:
        """Retrieve current playback context and track information."""
        r = self._request("GET", "")
        if r and r.status_code == 200:
            return r.json()
        return None
