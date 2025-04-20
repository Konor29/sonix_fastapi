import requests
import re

SOUNDCLOUD_SEARCH_URL = "https://soundcloud.com/search/sounds?q={query}"
SOUNDCLOUD_TRACK_REGEX = r'<a href="(/[^/]+/[^/]+)".*?title="([^"]+)".*?aria-label="Go to (.*?)'s profile"'


def search_soundcloud(query, max_results=1):
    """
    Search SoundCloud for tracks matching the query (public search, no API key).
    Returns a list of dicts: [{ 'url': ..., 'title': ..., 'artist': ... }]
    """
    url = SOUNDCLOUD_SEARCH_URL.format(query=requests.utils.quote(query))
    headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; SonixBot/1.0)'
    }
    resp = requests.get(url, headers=headers, timeout=10)
    if resp.status_code != 200:
        return []
    matches = re.findall(SOUNDCLOUD_TRACK_REGEX, resp.text)
    tracks = []
    for match in matches[:max_results]:
        path, title, artist = match
        tracks.append({
            'url': f'https://soundcloud.com{path}',
            'title': title,
            'artist': artist
        })
    return tracks
