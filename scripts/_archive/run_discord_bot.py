r"""
Run Archi Discord Bot.

Requires: DISCORD_BOT_TOKEN in .env
Create a bot at https://discord.com/developers/applications

Run: .\venv\Scripts\python.exe scripts\run_discord_bot.py

Usage:
- DM the bot: any message gets a response from Archi
- In channels: @Archi <message> to get a response
"""

import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
os.chdir(_root)

try:
    from dotenv import load_dotenv
    load_dotenv(_root / ".env", override=True)
except ImportError:
    pass

if __name__ == "__main__":
    from src.interfaces.discord_bot import run_bot

    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        print("ERROR: DISCORD_BOT_TOKEN not set.")
        print("1. Create app at https://discord.com/developers/applications")
        print("2. Bot tab -> Add Bot")
        print("3. Copy token, add to .env: DISCORD_BOT_TOKEN=your_token")
        sys.exit(1)

    print("=" * 60)
    print("Archi Discord Bot starting...")
    print("DM the bot or @mention in channels to chat with Archi")
    print("=" * 60)

    run_bot(token)
