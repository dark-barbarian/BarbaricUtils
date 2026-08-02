import bisect
import io
import json
import os
import re
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

import anyio
import discord
import requests
from aiocsv import AsyncDictReader
from discord import HTTPException, SlashCommandGroup, option
from discord.ext import commands
from slpp import slpp as lua

from utils import wiki_operations
from utils.bot import Bot

CSV_FILE_PATH = "./stats.csv"
MODULE_LIST_FILE_PATH = "./persistent/csvmodules.json"
OBSERVABLE_PAGES_LIST_FILE_PATH = "./persistent/updatemanually.json"

NUMBER_OF_AUTOCOMPLETE_RESULTS = 25
"""Maximum number of results to return for autocomplete handlers."""

LEVEL_KEYS = [
    "TroopLevel",
    "BuildingLevel",
    "HeroLevel",
    "SpellLevel",
    "TrapLevel",
    "ModuleLevel",
    "EquipmentLevel",
]
"""Keys indicating level information about a troop etc. in data modules."""


class ClashStats(commands.Cog):
    """Cog to work with stats around Clash of Clans."""

    wiki = SlashCommandGroup("wiki", "Commands to do wiki-related operations")
    additions = wiki.create_subgroup("add", "Commands to add items to lists")
    removals = wiki.create_subgroup("remove", "Commands to remove items from lists")

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self.data_module_names = []
        self.pages_with_manual_entries: dict[str, list[str]] = {}
        """Pages with entries not in the CSV (e.g. AltDPS for Electro Titan)."""

        for opt in cast("discord.SlashCommand", self.wikiupdate).options:
            if opt.name == "module":
                opt.autocomplete = discord.utils.basic_autocomplete(self._autocomplete_module_names)
                break

        for opt in cast("discord.SlashCommand", self.remove_module).options:
            if opt.name == "name":
                opt.autocomplete = discord.utils.basic_autocomplete(self._autocomplete_module_names)
                break

        for opt in cast("discord.SlashCommand", self.add_observable_page).options:
            if opt.name == "category":
                opt.autocomplete = discord.utils.basic_autocomplete(self._autocomplete_page_categories)
                break

        for opt in cast("discord.SlashCommand", self.remove_observable_page).options:
            if opt.name == "name":
                opt.autocomplete = discord.utils.basic_autocomplete(
                    self._autocomplete_page_observer_names, filter=lambda *_: True
                )
                break

    async def _autocomplete_module_names(self, _ctx: discord.AutocompleteContext) -> list[str]:
        """Autocomplete handler for module names used in CSV modules."""
        return self.data_module_names

    async def _autocomplete_page_categories(self, _ctx: discord.AutocompleteContext) -> list[str]:
        """Autocomplete handler for configured page categories."""
        return list(self.pages_with_manual_entries.keys())

    async def _autocomplete_page_observer_names(self, ctx: discord.AutocompleteContext) -> list[str]:  # noqa: C901
        """Autocomplete handler for page observer names with pagination."""
        user_input = ctx.value.removeprefix("[KATEGORIE] ").removeprefix("◀ ").split("(Seite")[0].strip()

        def check(page: str) -> bool:
            return page.lower().startswith(user_input.lower())

        if user_input == "":  # show categories for user to click on
            return ["[KATEGORIE] " + page for page in self.pages_with_manual_entries]
        if user_input in self.pages_with_manual_entries:
            result = list(self.pages_with_manual_entries[user_input])
        else:
            result = [page for pages in self.pages_with_manual_entries.values() for page in pages if check(page)]
            # for pages in self.pages_with_manual_entries.values():
            #     for page in pages:
            #         if check(page):
            #             result.append(page)  # noqa: ERA001

        def get_page_number(ctx_value: str) -> int | None:
            number = re.search(r"\(Seite (\d+)\)", ctx_value)
            if number:
                return int(number.group(1))
            return None

        # pagination logic, if more than 25 entries
        if any(
            c in ctx.value for c in ["▶", "◀"]
        ):  # if there is no '▶' or '◀' we don't need to do the regex and can directly set 1 as page number
            page_number = get_page_number(ctx.value) or 1
        else:
            page_number = 1

        # count number of pages we need
        pages = 1
        if len(result) > (NUMBER_OF_AUTOCOMPLETE_RESULTS):
            entries = len(result) - (NUMBER_OF_AUTOCOMPLETE_RESULTS - 1)
            while entries > (NUMBER_OF_AUTOCOMPLETE_RESULTS - 1):
                entries -= NUMBER_OF_AUTOCOMPLETE_RESULTS - 2
                pages += 1
            pages += 1

        if pages == 1:
            return result

        if page_number == 1:
            return [*result[: NUMBER_OF_AUTOCOMPLETE_RESULTS - 1], f"{user_input} (Seite 2) ▶"]
        if page_number == pages:
            return [
                f"◀ {user_input} (Seite {page_number - 1})",
                *result[
                    (NUMBER_OF_AUTOCOMPLETE_RESULTS - 1) + (page_number - 2) * (NUMBER_OF_AUTOCOMPLETE_RESULTS - 2) :
                ],
            ]
        return [
            f"◀ {user_input} (Seite {page_number - 1})",
            *result[
                (NUMBER_OF_AUTOCOMPLETE_RESULTS - 1)
                + (page_number - 2) * (NUMBER_OF_AUTOCOMPLETE_RESULTS - 2) : (NUMBER_OF_AUTOCOMPLETE_RESULTS - 1)
                + (page_number - 1) * (NUMBER_OF_AUTOCOMPLETE_RESULTS - 2)
            ],
            f"{user_input} (Seite {page_number + 1}) ▶",
        ]

    def _find_dict_by_target(self, to_search: dict, to_find: str) -> tuple[str, dict]:
        """Find a dict entry by its 'Name' field, returning the key and value dict."""
        for k, v in to_search.items():
            if v["Name"] == to_find:
                return k, v
        return "", {}

    def _remove_empty_values(self, vs: list) -> tuple[int, Any]:
        """Filter out empty string values and coerce to ints if possible."""
        filled_values = list(filter(lambda x: x != "", vs))

        with suppress(ValueError):
            filled_values = [int(x) for x in filled_values]

        return len(filled_values), (filled_values[0] if len(filled_values) == 1 else filled_values)

    async def _update_wiki_stats(self, page: str, wiki: str) -> tuple[dict | bool, list[str]]:  # noqa: C901, PLR0912
        """Update a wiki data module from CSV and return the API result plus manual-update list."""
        # TODO: Revisit and refactor to reduce complexity; split into helpers
        result = {}
        update_manually = []
        current_key = ""

        def flatten(xss: list[list[Any]]) -> list[Any]:
            return [x for xs in xss for x in xs]

        async with await anyio.open_file(CSV_FILE_PATH, "r") as f:
            async for row in AsyncDictReader(f):
                if all((item.lower() in ["string", "int", "boolean", ""]) for item in row.values()):
                    continue

                for column, value in row.items():
                    if column == "Name" and value != "":
                        current_key = value
                        result.setdefault(value, {})

                    try:
                        result[current_key].setdefault(column, []).append(value)
                    except KeyError as e:
                        msg = f"CSV does not have a 'Name' value for row: {row}"
                        self.bot.logger.exception(msg)
                        if self.bot.reporter:
                            await self.bot.reporter.report(e, context=msg)
                        return False, []

        # TODO: Armeelager (Bauarbeiterbasis) does not have levels in the CSV - they are manually added in the wiki.
        # Create an exception, so that if we are looking at Armeelager (Bauarbeiterbasis) in the CSV, it is skipped.
        # Should be possible by checking the len of the corresponding dict, Armeelager (Bauarbeiterbasis) has 1 row only
        # TODO: Lösung für Skelett finden, das taucht mehrfach auf.
        for k, v in result.items():
            for k2 in list(v.keys()):
                if self._remove_empty_values(v[k2])[0] == 0:
                    del result[k][k2]
                else:
                    result[k][k2] = self._remove_empty_values(v[k2])[1]

        in_wiki_version = await self._convert_from_lua(page, wiki)

        # update existing entries
        for k, v in cast("dict", in_wiki_version).items():
            target = v["Name"]
            result_key, result_value = self._find_dict_by_target(result, target)
            v_before = v.copy()
            v_before_level = next((v_before[k] for k in LEVEL_KEYS if v_before.get(k) is not None), None)
            v.update(result_value)
            v_level = next((v[k] for k in LEVEL_KEYS if v.get(k) is not None), None)

            try:
                if (k in flatten(list(self.pages_with_manual_entries.values()))) and (
                    (v_before_level and v_level and len(v_before_level) < len(v_level))
                    or (not v_before_level and not v_level and v_before != v)
                ):
                    update_manually.append(k)
                    self.bot.logger.warning("Possibly manual update necessary: %s", k)
            except TypeError:  # easiest solution to not break the bot if v isn't a dict
                pass

            if result_key != "":
                del result[result_key]

        # add new entries
        for k, v in result.items():
            if page.endswith(("Building/data", "Building2/data")):
                if "ResourceType" not in v:  # should always be true, but in case it does exist, don't overwrite it
                    v["ResourceType"] = ""
            elif page.endswith(("Troop/data", "Spell/data", "Hero/data")):
                if "ElixirType" not in v:  # should always be true, but in case it does exist, don't overwrite it
                    v["ElixirType"] = ""

            cast("dict", in_wiki_version)[k] = v

        return (
            await self.bot.wikiops.edit_page(page, "return " + lua.encode(in_wiki_version), bot=False, wiki=wiki),
            update_manually,
        )

    async def _convert_from_lua(self, module: str, wiki: str) -> object:
        """Fetch a Lua data module from the wiki."""
        content = await self.bot.wikiops.get_contents(module, wiki)
        if content == "":
            return {}
        return lua.decode(content[6:])

    def _get_clash_api_contents(self, session: requests.Session) -> None:
        """Fetch sample data from Clash of Clans API and log the JSON payload."""
        # TODO: Implement actual functionality using the fetched data
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": "Bearer " + os.environ.get("CLASH_API_TOKEN", ""),
        }

        response = session.get("https://api.clashofclans.com/v1/labels/players?limit=50", headers=headers)
        self.bot.logger.debug("Clash API response: %s", response.json())

    async def _clash_info(self, name: str, stat: str, level: int, module: str) -> object:
        """Return a specific stat value for a unit at the requested level."""
        # TODO: think about actual uses of this function
        stats = await self._convert_from_lua(module, wiki_operations.DEFAULT_WIKI)

        if level > 0:
            return cast("dict", stats)[name][stat][level - 1]
        return cast("dict", stats)[name][stat]

    @wiki.command(name="update", description="Update the wiki with CSV data")
    @option("module", description="The module you want to update", input_type=str)
    @option(
        "wiki",
        description=f'The wiki in which you want to update the values, default is "{wiki_operations.DEFAULT_WIKI}"',
        input_type=str,
        required=False,
        default=wiki_operations.DEFAULT_WIKI,
    )
    @commands.is_owner()
    async def wikiupdate(
        self, ctx: discord.ApplicationContext, file: discord.Attachment, module: str, wiki: str
    ) -> None:
        """Update wiki pages from a CSV attachment for the given module and wiki."""
        if file.content_type is None or not file.content_type.startswith("text/csv;"):
            await ctx.respond(
                embed=self.bot.create_embed(
                    description="The file you uploaded doesn't seem to be a CSV file.", color=0xFF0000
                ),
                ephemeral=True,
            )
            return

        if module not in self.data_module_names:
            await ctx.respond(
                embed=self.bot.create_embed(description="There is no module with this name!", color=0xFF0000),
                ephemeral=True,
            )
            return

        if not module.startswith(("Modul:", "Module:")):
            module = "Modul:" + module

        await ctx.defer()

        try:
            await file.save(Path(CSV_FILE_PATH))
        except HTTPException:
            await ctx.respond(
                embed=self.bot.create_embed(
                    description="Something went wrong upon uploading your file. Please try again.", color=0xFF0000
                ),
                ephemeral=True,
            )
            self.bot.logger.exception("Saving the attachment failed")
            return

        data, update_manually = await self._update_wiki_stats(module, wiki)
        if not data or not isinstance(data, dict):
            await ctx.respond(
                embed=self.bot.create_embed(description="Something went wrong. Please try again.", color=0xFF0000),
                ephemeral=True,
            )
            return

        response = next(iter(data.keys()))
        if response == "edit" and data["edit"]["result"] == "Success":
            if len(update_manually):
                output_file_data = io.BytesIO("\n".join(sorted(update_manually, key=str.lower)).encode("utf-8"))
                output_file = discord.File(fp=output_file_data, filename="pages.txt")
                await ctx.respond(
                    embed=self.bot.create_embed(
                        description="Added the data successfully, but some pages need to be updated manually!",
                        color=0x00FF00,
                    ),
                    file=output_file,
                )
                return
            await ctx.respond(embed=self.bot.create_embed(description="Added the data successfully!", color=0x00FF00))
        elif response == "error":
            await ctx.respond(
                embed=self.bot.create_embed(
                    description="Something went wrong!", footer=data["error"]["code"], color=0xFF0000
                ),
                ephemeral=True,
            )
        else:
            await ctx.respond(
                embed=self.bot.create_embed(description="Something went wrong!", color=0xFF0000), ephemeral=True
            )

    @additions.command(
        name="module", description="Adds a new module name to the module selection list (duplicates are ignored)"
    )
    @option("name", description="The name of the module you want to add", input_type=str)
    @commands.is_owner()
    async def add_module(self, ctx: discord.ApplicationContext, name: str) -> None:
        """Add a module name to the selection list (ignores duplicates)."""
        if name.startswith(("Modul:", "Module:")):
            name = name.split(":", 1)[1]

        bisect.insort(self.data_module_names, name, key=str.lower)
        self.data_module_names = list(dict.fromkeys(self.data_module_names))  # remove duplicates while preserving order

        try:
            async with await anyio.open_file(MODULE_LIST_FILE_PATH, "w") as file:
                await file.write(json.dumps(self.data_module_names, ensure_ascii=False, indent=4))
        except (OSError, json.JSONDecodeError):
            self.bot.logger.exception("Failed to store module name to file")
            await ctx.respond(
                embed=self.bot.create_embed(
                    description="Added the new name until next restart, but couldn't store it.", color=0xFF0000
                )
            )
            return

        await ctx.respond(embed=self.bot.create_embed(description="Added the new name!", color=0x00FF00))

    @removals.command(name="module", description="Removes a module name from the module selection list")
    @option("name", description="The name of the module you want to remove", input_type=str)
    @commands.is_owner()
    async def remove_module(self, ctx: discord.ApplicationContext, name: str) -> None:
        """Remove a module name from the selection list if present."""
        try:
            self.data_module_names.remove(name)
        except ValueError:
            await ctx.respond(embed=self.bot.create_embed(description="This module does not exist!", color=0xFF0000))
            return

        try:
            async with await anyio.open_file(MODULE_LIST_FILE_PATH, "w") as file:
                await file.write(json.dumps(self.data_module_names, ensure_ascii=False, indent=4))
        except (OSError, json.JSONDecodeError):
            self.bot.logger.exception("Failed to remove module name from file, file might be empty now")
            await ctx.respond(
                embed=self.bot.create_embed(
                    description="Removed the new name until next restart, but saving failed.", color=0xFF0000
                )
            )
            return

        await ctx.respond(embed=self.bot.create_embed(description="Removed the module name!", color=0x00FF00))

    @additions.command(name="page", description="Adds a new page to be warned about when updating the wiki data")
    @option("category", description="The category this page belongs to (is created if not listed)", input_type=str)
    @option("name", description="The name of the page you want to be observed", input_type=str)
    @commands.is_owner()
    async def add_observable_page(self, ctx: discord.ApplicationContext, category: str, name: str) -> None:
        """Add a page under a category to observe for manual updates."""
        observable_pages = self.pages_with_manual_entries

        bisect.insort(observable_pages.setdefault(category, []), name, key=str.lower)
        self.pages_with_manual_entries = {
            key: observable_pages[key] for key in sorted(observable_pages.keys(), key=str.lower)
        }

        try:
            async with await anyio.open_file(OBSERVABLE_PAGES_LIST_FILE_PATH, "w") as file:
                await file.write(json.dumps(self.pages_with_manual_entries, ensure_ascii=False, indent=4))
        except (OSError, json.JSONDecodeError):
            self.bot.logger.exception("Failed to store page name to file")
            await ctx.respond(
                embed=self.bot.create_embed(
                    description="Added the new name until next restart, but couldn't store it.", color=0xFF0000
                )
            )
            return

        await ctx.respond(embed=self.bot.create_embed(description="Added the new name!", color=0x00FF00))

    @removals.command(
        name="page",
        description="Removes a page or category from the observer list (does nothing if name doesn't exist)",
    )
    @option(
        "name", description="The page name you want to remove (its category is also removed if empty)", input_type=str
    )
    @commands.is_owner()
    async def remove_observable_page(self, ctx: discord.ApplicationContext, name: str) -> None:
        """Remove a page (and empty categories) from the observer list."""
        observable_pages = self.pages_with_manual_entries
        observable_pages_keys = list(observable_pages.keys())

        error_counter = 0
        for key in observable_pages_keys:
            try:
                observable_pages[key].remove(name)
            except ValueError:
                error_counter += 1

            if len(observable_pages[key]) == 0:
                del observable_pages[key]

        if error_counter == len(observable_pages_keys):
            await ctx.respond(embed=self.bot.create_embed(description="Name doesn't exist!", color=0xFF0000))
            return

        try:
            async with await anyio.open_file(OBSERVABLE_PAGES_LIST_FILE_PATH, "w") as file:
                await file.write(json.dumps(observable_pages, ensure_ascii=False, indent=4))
        except (OSError, json.JSONDecodeError):
            self.bot.logger.exception("Failed to remove page name from file, file might be empty now")
            await ctx.respond(
                embed=self.bot.create_embed(
                    description="Removed the new name until next restart, but saving failed.", color=0xFF0000
                )
            )
            return

        await ctx.respond(embed=self.bot.create_embed(description="Removed the page name!", color=0x00FF00))


def setup(bot: Bot) -> None:
    """Register the `ClashStats` cog with the bot."""
    bot.add_cog(ClashStats(bot))
