import requests
import re

INVIDIOUS_URL = "http://localhost:3000"  # Change if your Invidious is on a different host/port


def extract_video_id(url_or_id):
    """
    Extract the video ID from a YouTube URL or return the ID if already given.
    """
    # Direct video ID
    if re.match(r'^[a-zA-Z0-9_-]{11}$', url_or_id):
        return url_or_id
    # Full YouTube URL
    patterns = [
        r"youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})",
        r"youtu\.be/([a-zA-Z0-9_-]{11})",
        r"youtube\.com/embed/([a-zA-Z0-9_-]{11})"
    ]
    for pat in patterns:
        m = re.search(pat, url_or_id)
        if m:
            return m.group(1)
    return None


def get_invidious_audio_url(video_id):
    """
    Query Invidious for the best audio stream for a given video ID.
    Returns (audio_url, title, thumbnail, original_url) or None if not found.
    """
    api_url = f"{INVIDIOUS_URL}/api/v1/videos/{video_id}"
    try:
        resp = requests.get(api_url, timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        audio_streams = [f for f in data.get('formatStreams', []) if f.get('mimeType', '').startswith('audio/')]
        if not audio_streams:
            return None
        # Choose highest bitrate audio
        best_audio = max(audio_streams, key=lambda f: f.get('bitrate', 0))
        return {
            'audio_url': best_audio['url'],
            'title': data.get('title', video_id),
            'thumbnail': data.get('videoThumbnails', [{}])[0].get('url', ''),
            'webpage_url': f"https://youtube.com/watch?v={video_id}"
        }
    except Exception as e:
        print(f"[Invidious] Error: {e}")
        return None


def invidious_search(query):
    """
    Search Invidious for a video matching the query. Returns first video ID or None.
    """
    api_url = f"{INVIDIOUS_URL}/api/v1/search?q={requests.utils.quote(query)}&type=video"
    try:
        resp = requests.get(api_url, timeout=10)
        if resp.status_code != 200:
            return None
        results = resp.json()
        for item in results:
            if item.get('videoId'):
                return item['videoId']
        return None
    except Exception as e:
        print(f"[Invidious] Search error: {e}")
        return None
