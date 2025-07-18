import asyncio
from datetime import datetime, timedelta
import json
import logging
from typing import cast
from zoneinfo import ZoneInfo

import discord
from discord import option
from discord.ext import commands
import requests

from utils.bot_utils import create_embed, local_tz


class PageErrorReminders(commands.Cog):
    def __init__(self, bot):
        self.bot: commands.Bot = bot
        
        self.channel_id = 1372252214814310651 # 248493533537763328
        self.categories_json_file_path = "./wikicategories.json"
        
        self.category_check_day_hour = (5, 12)  # (weekday (0 to 6), hour)
        self.wiki_categories = {}
    
    async def check_wiki_page_errors(self):
        await self.bot.wait_until_ready()
        channel = self.bot.get_channel(self.channel_id)
        
        data = self.fetch_categories("darkbarbarian.fandom.com", "Benutzer:DarkBarbarian/WikiCategories.json")
        if data:
            self.wiki_categories.update(data)
            try:
                with open(self.categories_json_file_path, 'w') as file:
                    json.dump(self.wiki_categories, file, ensure_ascii=False, indent=4)
            except (OSError, json.JSONDecodeError) as e:
                logging.error(f"Failed to store wiki categories to file: {e}")
                return
        
        while True:
            now = datetime.now(local_tz)
            days_until = (self.category_check_day_hour[0] - now.weekday()) % 7
            target_time = (now + timedelta(days=days_until)).replace(hour=self.category_check_day_hour[1], minute=0, second=0, microsecond=0)
            if now > target_time:
                target_time += timedelta(weeks=1)
            
            await asyncio.sleep((target_time - now).total_seconds())
            
            error_counter = 0
            for wiki, categories in self.wiki_categories.items():
                if wiki == "allowed_errors":
                    continue
                
                message = f"# Category report for {wiki}\n"
                for category in categories:
                    logging.info(f"Checking category '{category}' on wiki '{wiki}'")
                    pages = self.fetch_category_members(wiki, category)
                    
                    message = f"{message}- [{category}](<https://{wiki}/wiki/Kategorie:{category.replace(' ', '_')}>) - "
                    if pages is not None:
                        error_count = len(pages)
                        message += f"{error_count} page(s){" :warning:\n" if error_count > 0 else "\n"}"
                        error_counter += error_count
                    else:
                        message += "error\n"
                    
                    await asyncio.sleep(5)
                
                if error_counter > self.wiki_categories.get("allowed_errors", 0):
                    message += f"<@{self.bot.owner_id}>"
                
                if channel:
                    await cast(discord.TextChannel, channel).send(message)

    def fetch_categories(self, wiki: str, page_title: str):
        url = f"https://{wiki}/api.php"
        params = {
            "action": "query",
            "format": "json",
            "formatversion": 2,
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "main",
            "titles": page_title
        }

        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            pages = data.get("query", {}).get("pages", [])
            if pages and "revisions" in pages[0]:
                raw_content = pages[0]["revisions"][0]["slots"]["main"]["content"]
                return json.loads(raw_content)
            else:
                logging.error(f"No revisions or content found while fetching https://{wiki}/wiki/{page_title}")
                return None
        except Exception as e:
            logging.error(f"Error fetching or parsing JSON: {e}")
            return None

    def fetch_category_members(self, wiki: str, category: str):
        url = f"https://{wiki}/api.php"
        params = {
            "action": "query",
            "format": "json",
            "formatversion": 2,
            "list": "categorymembers",
            "cmtitle": f"Kategorie:{category}"
        }
        
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            return data.get("query", {}).get("categorymembers", [])
        except Exception as e:
            logging.error(f"Error fetching category members from {wiki}: {e}")
            return None
    
    
    @commands.slash_command(
        name="update_alowed_errors",
        description="Updates the amount of errors the category report may yield without notifying a certain someone"
    )
    @option(
        "value",
        description="Up to this many errors are allowed and will not result in a ping",
        input_type=int,
        min_value=0
    )
    @commands.is_owner()
    async def update_allowed_errors(self, ctx: discord.ApplicationContext, value: int):
        self.wiki_categories["allowed_errors"] = value
        try:
            with open(self.categories_json_file_path, 'w') as file:
                json.dump(self.wiki_categories, file, ensure_ascii=False, indent=4)
        except (OSError, json.JSONDecodeError) as e:
            logging.error(f"Failed to store allowed errors to file: {e}")
            await ctx.respond(embed=create_embed(description="Updated allowed errors until next restart, but saving failed.", color=0xFF0000))
            return
        
        await ctx.respond(embed=create_embed(description="Updated allowed errors in the category report!", color=0x00FF00))

def setup(bot: commands.Bot):
    bot.add_cog(PageErrorReminders(bot))