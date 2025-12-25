import asyncio
import json
import logging
from datetime import datetime, time

import anyio
import discord
import requests
from discord import option
from discord.ext import commands, tasks

from utils.bot_utils import LOCAL_TZ, create_embed

logger = logging.getLogger(__name__)

WEEKDAY_SATURDAY = 5


class PageErrorReminders(commands.Cog):
    """Cog to report category pages with errors across configured wikis."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        self.channel_id = 1372252214814310651  # Clash of Clans Wiki -> #wiki-category-report
        # self.channel_id = 836247026118295642  # barbs tests -> #bot  # noqa: ERA001
        self.categories_json_file_path = "./wikicategories.json"
        self.wiki_categories = {}

    @tasks.loop(time=time(hour=12, tzinfo=LOCAL_TZ))
    async def check_wiki_page_errors(self, channel: discord.TextChannel) -> None:
        """Weekly report of category pages with errors for each configured wiki."""
        if datetime.now(LOCAL_TZ).weekday() != WEEKDAY_SATURDAY:
            return

        data = self.fetch_categories("darkbarbarian.fandom.com", "Benutzer:DarkBarbarian/WikiCategories.json")
        if data:
            self.wiki_categories.update(data)
            try:
                async with await anyio.open_file(self.categories_json_file_path, "w") as file:
                    await file.write(json.dumps(self.wiki_categories, ensure_ascii=False, indent=4))
            except (OSError, json.JSONDecodeError):
                logger.exception("Failed to store wiki categories to file")

        error_counter = 0
        for wiki, categories in self.wiki_categories.items():
            if wiki == "allowed_errors":
                continue

            message = f"# Category report for {wiki}\n"
            for category in categories:
                logger.info("Checking category '%s' on wiki '%s'", category, wiki)
                pages = self.fetch_category_members(wiki, category)

                message = f"{message}- [{category}](<https://{wiki}/wiki/Kategorie:{category.replace(' ', '_')}>) - "
                if pages is not None:
                    error_count = len(pages)
                    message += f"{error_count} page(s){' :warning:\n' if error_count > 0 else '\n'}"
                    error_counter += error_count
                else:
                    message += "error\n"

                await asyncio.sleep(5)

            if error_counter > self.wiki_categories.get("allowed_errors", 0):
                message += f"<@{self.bot.owner_id}>"

            if channel:
                await channel.send(message)

    def fetch_categories(self, wiki: str, page_title: str) -> dict | None:
        """Fetch wiki categories JSON content from a given wiki page."""
        url = f"https://{wiki}/api.php"
        params = {
            "action": "query",
            "format": "json",
            "formatversion": 2,
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "main",
            "titles": page_title,
        }

        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            pages = data.get("query", {}).get("pages", [])
            if pages and "revisions" in pages[0]:
                raw_content = pages[0]["revisions"][0]["slots"]["main"]["content"]
                return json.loads(raw_content)
            logger.error("No revisions or content found while fetching https://%s/wiki/%s", wiki, page_title)
        except Exception:
            logger.exception("Error fetching or parsing JSON")

    def fetch_category_members(self, wiki: str, category: str) -> list | None:
        """Fetch members of a category from the given wiki."""
        url = f"https://{wiki}/api.php"
        params = {
            "action": "query",
            "format": "json",
            "formatversion": 2,
            "list": "categorymembers",
            "cmtitle": f"Kategorie:{category}",
        }

        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            return data.get("query", {}).get("categorymembers", [])
        except Exception:
            logger.exception("Error fetching category members from %s", wiki)
            return None

    @commands.slash_command(
        name="update_alowed_errors",
        description="Updates the amount of errors the category report may yield without notifying a certain someone",
    )
    @option(
        "value",
        description="Up to this many errors are allowed and will not result in a ping",
        input_type=int,
        min_value=0,
    )
    @commands.is_owner()
    async def update_allowed_errors(self, ctx: discord.ApplicationContext, value: int) -> None:
        """Update the allowed error count threshold used in category reports."""
        self.wiki_categories["allowed_errors"] = value
        try:
            async with await anyio.open_file(self.categories_json_file_path, "w") as file:
                await file.write(json.dumps(self.wiki_categories, ensure_ascii=False, indent=4))
        except (OSError, json.JSONDecodeError):
            logger.exception("Failed to store allowed errors to file")
            await ctx.respond(
                embed=create_embed(
                    description="Updated allowed errors until next restart, but saving failed.", color=0xFF0000
                )
            )
            return

        await ctx.respond(
            embed=create_embed(description="Updated allowed errors in the category report!", color=0x00FF00)
        )


def setup(bot: commands.Bot) -> None:
    """Register the `PageErrorReminders` cog with the bot."""
    bot.add_cog(PageErrorReminders(bot))
