import os
import json

guild_keys_path = os.path.join(os.path.dirname(__file__), 'guild_keys.json')

if os.path.exists(guild_keys_path):
    with open(guild_keys_path, 'r') as f:
        guild_keys = json.load(f)
else:
    guild_keys = {}

def get_guild_key(guild_id):
    return guild_keys.get(str(guild_id))

def get_guild_id_from_key(server_key):
    for guild_id, key in guild_keys.items():
        if key == server_key:
            return guild_id
    return None
