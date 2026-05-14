import os
import urllib.parse
import requests

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


def get_auth_url() -> str:
    params = {
        "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
        "redirect_uri": os.getenv("APP_URL", "http://localhost:8501"),
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
    }
    return f"{_AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(code: str) -> dict:
    """Exchange an OAuth code for user info. Returns dict with sub, email, name."""
    token_resp = requests.post(_TOKEN_URL, data={
        "code": code,
        "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET", ""),
        "redirect_uri": os.getenv("APP_URL", "http://localhost:8501"),
        "grant_type": "authorization_code",
    }, timeout=10)
    token_resp.raise_for_status()
    access_token = token_resp.json()["access_token"]

    userinfo = requests.get(_USERINFO_URL, headers={
        "Authorization": f"Bearer {access_token}"
    }, timeout=10)
    userinfo.raise_for_status()
    return userinfo.json()
