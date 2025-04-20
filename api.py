from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
import os
import asyncio
from key_utils import get_guild_key

app = FastAPI()
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],  # Allow both Next.js dev ports
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Guild key validation replaces the old API key check

@app.get("/api/channels")
async def get_channels(server_key: str):
    # Look up guild_id from server_key
    from key_utils import get_guild_id_from_key
    guild_id = get_guild_id_from_key(server_key)
    if not guild_id:
        raise HTTPException(status_code=404, detail="Invalid server key")
    if not bot_instance:
        raise HTTPException(status_code=500, detail="Bot not initialized")
    guild = bot_instance.get_guild(int(guild_id))
    if not guild:
        raise HTTPException(status_code=404, detail="Guild not found")
    # Only return voice channels
    channels = [
        {"id": str(ch.id), "name": ch.name}
        for ch in getattr(guild, "voice_channels", [])
    ]
    return {"channels": channels, "guild_id": str(guild_id)}

# Reference to the bot instance (set from main.py)
bot_instance = None

def set_bot(bot):
    global bot_instance
    bot_instance = bot

class PlayRequest(BaseModel):
    guild_id: int
    channel_id: int
    query: str

import logging
logging.basicConfig(level=logging.WARNING)

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

import asyncio

def run_in_bot_loop(coro):
    future = asyncio.run_coroutine_threadsafe(coro, bot_instance.loop)
    return future.result()

def ensure_bot_in_voice(channel):
    voice_client = channel.guild.voice_client
    if not voice_client or voice_client.channel != channel:
        coro = channel.connect()
        future = asyncio.run_coroutine_threadsafe(coro, bot_instance.loop)
        return future.result()
    return voice_client

@app.post("/play")
async def play_song(req: PlayRequest, request: Request):
    print("DEBUG: /play endpoint called")
    logging.warning(f"/play called with guild_id={req.guild_id}, channel_id={req.channel_id}, query={req.query}")
    guild_key = request.headers.get("x-guild-key")
    expected_key = get_guild_key(str(req.guild_id))
    logging.warning(f"Provided key: {guild_key}, Expected key: {expected_key}")
    if not guild_key or guild_key != expected_key:
        logging.error("Invalid or missing guild key")
        raise HTTPException(status_code=401, detail="Invalid or missing guild key")
    if not bot_instance:
        logging.error("Bot not initialized")
        raise HTTPException(status_code=500, detail="Bot not initialized")
    guild = bot_instance.get_guild(req.guild_id)
    if not guild:
        logging.error(f"Guild not found for id {req.guild_id}")
    channel = guild.get_channel(req.channel_id) if guild else None
    if not channel:
        logging.error(f"Channel not found for id {req.channel_id} in guild {req.guild_id}")
    if not guild or not channel:
        raise HTTPException(status_code=404, detail="Guild or channel not found")
    logging.warning(f"Found guild: {guild}, channel: {channel}")
    # Ensure bot joins the voice channel before sending the play command
    ensure_bot_in_voice(channel)
    message = run_in_bot_loop(channel.send(f"Web: Play command received! {req.query}"))
    ctx = run_in_bot_loop(bot_instance.get_context(message))
    # Directly call play_song to play the requested music
    from main import play_song as internal_play_song
    run_in_bot_loop(internal_play_song(ctx, req.query))
    logging.warning(f"Play command invoked for {req.query}")
    return {"status": "queued", "query": req.query}


@app.post("/pause")
async def pause_song(request: Request):
    data = await request.json()
    guild_id = int(data.get("guild_id"))
    channel_id = int(data.get("channel_id"))
    guild_key = request.headers.get("x-guild-key")
    expected_key = get_guild_key(guild_id)
    if not guild_key or guild_key != expected_key:
        raise HTTPException(status_code=401, detail="Invalid or missing guild key")
    if not bot_instance:
        raise HTTPException(status_code=500, detail="Bot not initialized")
    guild = bot_instance.get_guild(guild_id)
    voice = None
    if guild:
        voice = next((vc for vc in bot_instance.voice_clients if vc.guild.id == guild_id), None)
    if voice and voice.is_playing():
        voice.pause()
        return {"status": "paused"}
    return {"status": "not_playing"}

@app.post("/unpause")
async def unpause_song(request: Request):
    data = await request.json()
    guild_id = int(data.get("guild_id"))
    channel_id = int(data.get("channel_id"))
    guild_key = request.headers.get("x-guild-key")
    expected_key = get_guild_key(guild_id)
    if not guild_key or guild_key != expected_key:
        raise HTTPException(status_code=401, detail="Invalid or missing guild key")
    if not bot_instance:
        raise HTTPException(status_code=500, detail="Bot not initialized")
    guild = bot_instance.get_guild(guild_id)
    voice = None
    if guild:
        voice = next((vc for vc in bot_instance.voice_clients if vc.guild.id == guild_id), None)
    if voice and voice.is_paused():
        voice.resume()
        return {"status": "resumed"}
    return {"status": "not_paused"}

@app.post("/replay")
async def replay_song(request: Request):
    data = await request.json()
    guild_id = int(data.get("guild_id"))
    channel_id = int(data.get("channel_id"))
    guild_key = request.headers.get("x-guild-key")
    expected_key = get_guild_key(guild_id)
    if not guild_key or guild_key != expected_key:
        raise HTTPException(status_code=401, detail="Invalid or missing guild key")
    if not bot_instance:
        raise HTTPException(status_code=500, detail="Bot not initialized")
    guild = bot_instance.get_guild(guild_id)
    channel = guild.get_channel(channel_id) if guild else None
    if not guild or not channel:
        raise HTTPException(status_code=404, detail="Guild or channel not found")
    from main import replay
    message = run_in_bot_loop(channel.send("Web: Replay command received!"))
    ctx = run_in_bot_loop(bot_instance.get_context(message))
    run_in_bot_loop(replay(ctx))
    return {"status": "replayed"}

@app.post("/skip")
async def skip_song(request: Request):
    data = await request.json()
    guild_id = int(data.get("guild_id"))
    channel_id = int(data.get("channel_id"))
    guild_key = request.headers.get("x-guild-key")
    expected_key = get_guild_key(guild_id)
    if not guild_key or guild_key != expected_key:
        raise HTTPException(status_code=401, detail="Invalid or missing guild key")
    if not bot_instance:
        raise HTTPException(status_code=500, detail="Bot not initialized")
    guild = bot_instance.get_guild(guild_id)
    voice = None
    if guild:
        voice = next((vc for vc in bot_instance.voice_clients if vc.guild.id == guild_id), None)
    if voice and voice.is_playing():
        voice.stop()
        return {"status": "skipped"}
    return {"status": "not_playing"}

def get_song_queue(guild_id):
    try:
        from main import song_queues
        queue = song_queues.get(guild_id, [])
        # Only return song dicts with expected metadata
        return [song for song in queue if isinstance(song, dict) and song.get('title')]
    except Exception:
        return []

def get_now_playing(guild_id):
    try:
        from main import now_playing
        song = now_playing.get(guild_id)
        if song and isinstance(song, dict) and song.get('title'):
            return song
        return None
    except Exception:
        return None

@app.get("/now_playing")
async def now_playing(request: Request, guild_id: int):
    guild_key = request.headers.get("x-guild-key")
    expected_key = get_guild_key(guild_id)
    if not guild_key or guild_key != expected_key:
        raise HTTPException(status_code=401, detail="Invalid or missing guild key")
    if not bot_instance:
        raise HTTPException(status_code=500, detail="Bot not initialized")
    guild = bot_instance.get_guild(guild_id)
    if not guild:
        raise HTTPException(status_code=404, detail="Guild not found")
    song = get_now_playing(guild_id)
    # Return only relevant fields for frontend
    if song:
        return {"now_playing": {
            "title": song.get("title"),
            "url": song.get("webpage_url"),
            "thumbnail": song.get("thumbnail")
        }}
    return {"now_playing": None}

@app.get("/queue")
async def get_queue(request: Request, guild_id: int):
    guild_key = request.headers.get("x-guild-key")
    expected_key = get_guild_key(guild_id)
    if not guild_key or guild_key != expected_key:
        raise HTTPException(status_code=401, detail="Invalid or missing guild key")
    if not bot_instance:
        raise HTTPException(status_code=500, detail="Bot not initialized")
    queue = get_song_queue(guild_id)
    # Exclude the first song if it's now playing
    queue_out = [
        {"title": song.get("title"), "url": song.get("webpage_url"), "thumbnail": song.get("thumbnail")}
        for song in queue[1:]
        if song.get("title")
    ] if queue else []
    return {"queue": queue_out}

@app.get("/guilds")
async def get_guilds(request: Request):
    if request.headers.get("x-api-key") != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    if not bot_instance:
        raise HTTPException(status_code=500, detail="Bot not initialized")
    guilds = []
    for guild in bot_instance.guilds:
        guild_info = {
            "id": guild.id,
            "name": guild.name,
            "text_channels": [
                {"id": c.id, "name": c.name} for c in guild.text_channels
            ],
            "voice_channels": [
                {"id": c.id, "name": c.name} for c in guild.voice_channels
            ]
        }
        guilds.append(guild_info)
    return {"guilds": guilds}
