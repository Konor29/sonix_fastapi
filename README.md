# Sonix Discord Music Bot

A powerful, production-ready Discord music bot with web dashboard control!

## Features (v1.02)
- Play music from YouTube in your Discord server
- Modern web dashboard for controlling the bot (Next.js frontend)
- Discord OAuth2 authentication
- Neon Postgres database for production reliability
- FastAPI backend integration
- Easy deployment to Vercel and cloud

## Setup
1. **Install Python 3.9 or newer.**
2. **Install dependencies:**
   ```sh
   pip install -r requirements.txt
   ```
3. **Configure your environment variables:**
   - Create a `.env` file with your Discord bot token and other secrets (see `.env.example` if present).
4. **Run the bot:**
   ```sh
   python main.py
   ```

## Web Dashboard
- The bot is designed to work with the [Sonix Website](https://github.com/Konor29/SonixWebsite) for full-featured web control.
- Deploy the website (Next.js app) to Vercel and configure your environment variables for Discord and Neon DB.

## Database
- Uses [Neon.tech](https://neon.tech/) (free Postgres) for cloud database storage.
- Make sure your `DATABASE_URL` is set in your environment variables for both the bot and the web dashboard.

## Version
- **v1.02**: Production-ready, Neon DB support, web dashboard integration, stable music playback.

---
For more details, see the documentation in each repo or open an issue if you need help!