from __future__ import annotations
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import aiohttp
import discord
from discord.ext import commands, tasks
from typing import List
import os
import discord
from discord.ext import commands
from discord import Object
import dotenv
from dotenv import load_dotenv

load_dotenv()


# CONFIG – set these
data_file = os.getenv("data_file", "/data")
os.makedirs(data_file, exist_ok=True)
YOUTUBE_STATE_FILE = os.path.join(data_file, "youtube_state.json")



TEST_GUILD_ID: int = 1313681001377038377  # replace with your test guild id or 0
YOUTUBE_LIVE_DEST_CHANNEL_ID = 1521360464783605838
YOUTUBE_VIDEO_DEST_CHANNEL_ID = 1521360464783605838

def load_youtube_state() -> dict:
    if not YOUTUBE_STATE_FILE.is_file():
        return {}
    try:
        with YOUTUBE_STATE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_youtube_state(data: dict):
    try:
        with YOUTUBE_STATE_FILE.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


class YouTubePollerCog(commands.Cog):
    """
    Poll the YouTube RSS feed for a channel and post new lives/videos.

    - Lives -> YOUTUBE_LIVE_DEST_CHANNEL_ID (ping @everyone)
    - VOD  -> YOUTUBE_VIDEO_DEST_CHANNEL_ID (ping @everyone)
    - Waiting/upcoming/premiere -> ignored (but advances state)
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.channel_id = "UCrGjDSsGCwensGughwNtxUA"  # change if needed
        self.feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={self.channel_id}"
        self.state = load_youtube_state()  # { "last_video_id": str }
        self.poll_task.start()

    def cog_unload(self):
        self.poll_task.cancel()

    # ---------- helpers ----------

    def _get_video_url(self, entry: ET.Element) -> str:
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "yt": "http://www.youtube.com/xml/schemas/2015",
        }
        link = entry.find("atom:link[@rel='alternate']", ns)
        if link is not None and "href" in link.attrib:
            return link.attrib["href"]
        vid = entry.findtext("yt:videoId", default=None, namespaces=ns)
        if vid:
            return f"https://www.youtube.com/watch?v={vid}"
        return ""

    def _classify_entry(self, entry: ET.Element) -> str:
        """
        Return one of: 'live', 'vod', 'ignore'.
        """
        title = (entry.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
        t_lower = title.lower()

        # 1) Check yt:liveBroadcast tag explicitly
        for child in entry:
            if child.tag.endswith("liveBroadcast"):
                txt = (child.text or "").strip().lower()
                if "upcoming" in txt:
                    return "ignore"
                if "live" in txt:
                    return "live"
                return "ignore"

        # 2) No liveBroadcast tag — use heuristics on title
        if any(word in t_lower for word in ("waiting", "upcoming", "premiere")):
            return "ignore"

        if ("live" in t_lower or "stream" in t_lower) and "upcoming" not in t_lower:
            return "live"

        return "vod"

    async def _fetch_feed(self) -> List[ET.Element]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.feed_url, timeout=15) as resp:
                    if resp.status != 200:
                        return []
                    text = await resp.text()
        except Exception:
            return []
        try:
            root = ET.fromstring(text)
        except Exception:
            return []
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)
        return entries or []

    # ---------- loop ----------

    @tasks.loop(minutes=5)
    async def poll_task(self):
        await self.bot.wait_until_ready()
        entries = await self._fetch_feed()
        if not entries:
            return

        ns = {"yt": "http://www.youtube.com/xml/schemas/2015"}
        last_seen = self.state.get("last_video_id")

        # collect entries newer than last_seen
        new_entries: List[ET.Element] = []
        for e in entries:
            vid = e.findtext("yt:videoId", default=None, namespaces=ns)
            if not vid:
                continue
            if vid == last_seen:
                break
            new_entries.append(e)

        if not new_entries:
            return

        # process from oldest to newest
        new_entries.reverse()

        if not self.bot.guilds:
            return
        guild = next((g for g in self.bot.guilds if g.id == TEST_GUILD_ID), self.bot.guilds[0])

        live_dest = guild.get_channel(YOUTUBE_LIVE_DEST_CHANNEL_ID) if YOUTUBE_LIVE_DEST_CHANNEL_ID else None
        video_dest = guild.get_channel(YOUTUBE_VIDEO_DEST_CHANNEL_ID) if YOUTUBE_VIDEO_DEST_CHANNEL_ID else None

        newest_vid_id = last_seen

        for e in new_entries:
            vid = e.findtext("yt:videoId", default=None, namespaces=ns)
            if not vid:
                continue

            title = e.findtext("{http://www.w3.org/2005/Atom}title") or "Video"
            url = self._get_video_url(e)
            kind = self._classify_entry(e)  # 'live', 'vod', or 'ignore'

            if kind == "ignore":
                newest_vid_id = vid
                continue

            if kind == "live":
                if isinstance(live_dest, discord.TextChannel):
                    body = f"@everyone\n\n# [{title}]({url})"
                    try:
                        await live_dest.send(body, allowed_mentions=discord.AllowedMentions(everyone=True))
                    except Exception:
                        pass

            elif kind == "vod":
                if isinstance(video_dest, discord.TextChannel):
                    body = f"@everyone\n\n# Watch {title}\n\n{url}"
                    try:
                        await video_dest.send(body, allowed_mentions=discord.AllowedMentions(everyone=True))
                    except Exception:
                        pass

            newest_vid_id = vid

        if newest_vid_id:
            self.state["last_video_id"] = newest_vid_id
            save_youtube_state(self.state)

    @poll_task.before_loop
    async def before_poll(self):
        await self.bot.wait_until_ready()


def setup(bot: commands.Bot):
    bot.add_cog(YouTubePollerCog(bot))


# ---------------- BOT SETUP ----------------

# intents
INTENTS = discord.Intents.default()
INTENTS.message_content = True  # only needed if you want to read messages

class MainBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=INTENTS)
        self._web_runner: None = None  # keep type simple

    async def setup_hook(self):
        guild_obj = Object(id=TEST_GUILD_ID)
        cog_names = [
            "YouTubePollerCog",  # only this cog for now
        ]

        for name in cog_names:
            cls = globals().get(name)
            if cls is None:
                print(f"Skipping cog {name}: not defined")
                continue
            try:
                await self.add_cog(cls(self))
                print(f"Added cog: {name}")
            except Exception:
                import traceback
                traceback.print_exc()
                print(f"Failed to add cog: {name}")

        try:
            await self.tree.sync(guild=guild_obj)
            print("Commands synced.")
        except Exception:
            import traceback
            traceback.print_exc()
            print("Failed to sync commands.")


# ------------- RUN BOT -------------
if __name__ == "__main__":
    # Option A: env var

    bot = MainBot()
    bot.run(os.getenv("token"))
