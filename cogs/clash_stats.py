import csv
import logging
import os
import re
from typing import Any, cast

import discord
import requests
from slpp import slpp as lua

from utils import wiki_operations

CSV_FILE_PATH = "./stats.csv"
MODULE_LIST_FILE_PATH = "./csvmodules.json"
OBSERVABLE_PAGES_LIST_FILE_PATH = "./updatemanually.json"
DATA_MODULE_NAMES = []

LEVEL_KEYS = [
    "TroopLevel", "BuildingLevel", "HeroLevel", "SpellLevel",
    "TrapLevel", "ModuleLevel", "EquipmentLevel",
]

# list of pages that have entries not contained in the CSV (e.g. AltDPS for Electro Titan), that need to be updated manually
PAGES_WITH_MANUAL_ENTRIES: dict[str, list[str]] = {}


async def autocomplete_module_names(ctx: discord.AutocompleteContext):
    return DATA_MODULE_NAMES


async def autocomplete_page_categories(ctx: discord.AutocompleteContext):
    return PAGES_WITH_MANUAL_ENTRIES.keys()
    

async def autocomplete_page_observer_names(ctx: discord.AutocompleteContext):
    user_input = ctx.value.removeprefix("[KATEGORIE] ").split("(Seite")[0].strip()
    
    def check(page: str):
        return page.lower().startswith(user_input.lower())
    
    if user_input == "":  # show categories for user to click on
        return [("[KATEGORIE] " + page) for page in PAGES_WITH_MANUAL_ENTRIES.keys()]
    elif user_input in PAGES_WITH_MANUAL_ENTRIES.keys():
        result = [page for page in PAGES_WITH_MANUAL_ENTRIES[user_input]]
    else:
        result = [page for pages in PAGES_WITH_MANUAL_ENTRIES.values() for page in pages if check(page)]  # populate result array while applying filter
    
    def get_page_number(ctx_value):
        number = re.search(r"\(Seite (\d+)\)", ctx_value)
        if number:
            return int(number.group(1))
    
    # pagination logic, if more than 25 entries
    if '▶' in ctx.value:  # if there is no '▶' we don't need to do the regex and can directly set 1 as page number
        page_number = get_page_number(ctx.value) or 1
    else:
        page_number = 1
        
    if len(result) > ((page_number - 1) * 24 + 25):
        result = result[(page_number - 1) * 24:page_number * 24]
        result.append(f"{user_input} (Seite {page_number + 1}) ▶")
        return result
    return result[(page_number - 1) * 24:]


def find_dict_by_target(to_search: dict, to_find: str):
    for k, v in to_search.items():
        if v['Name'] == to_find:
            return k, v
    return "", {}


def remove_empty_values(vs: list):
    filled_values = list(filter(lambda x: x != '', vs))

    try:
        filled_values = list(map(lambda x: int(x), filled_values))
    except ValueError:
        pass

    return len(filled_values), (filled_values[0] if len(filled_values) == 1 else filled_values)


# inplace
def update_values(current_dict: dict, to_add: dict):
    for k, v in to_add.items():
        current_dict[k] = v


def update_wiki_stats(page: str, wiki: str):
    reader = csv.DictReader(open(CSV_FILE_PATH))
    result = {}
    update_manually = []
    current_key = ""
    
    def flatten(xss: list[list[Any]]):
        return [x for xs in xss for x in xs]

    for row in reader:
        if all((item.lower() in ['string', 'int', 'boolean', '']) for item in row.values()):
            continue

        for column, value in row.items():
            if column == 'Name' and value != '':
                current_key = value
                result.setdefault(value, {})

            result[current_key].setdefault(column, []).append(value)

    # TODO: Armeelager (Bauarbeiterbasis) does not have levels in the CSV - they are manually added in the wiki.
    # Create an exception, so that if we are looking at Armeelager (Bauarbeiterbasis) in the CSV, it is skipped.
    # Should be possible by checking the len of the corresponding dict - Armeelager (Bauarbeiterbasis) has 1 row only.
    for k, v in result.items():
        for k2 in list(v.keys()):
            if remove_empty_values(v[k2])[0] == 0:
                del result[k][k2]
            else:
                result[k][k2] = remove_empty_values(v[k2])[1]

    in_wiki_version = convert_from_lua(page, wiki)

    # update existing entries
    for k, v in cast(dict, in_wiki_version).items():
        target = v['Name']
        result_key, result_value = find_dict_by_target(result, target)
        v_before = v.copy()
        v_before_level = next((v_before[k] for k in LEVEL_KEYS if v_before.get(k) is not None), None)
        update_values(v, result_value)
        v_level = next((v[k] for k in LEVEL_KEYS if v.get(k) is not None), None)
        
        try:
            if ((k in flatten(list(PAGES_WITH_MANUAL_ENTRIES.values()))) and
                ((v_before_level and v_level and len(v_before_level) < len(v_level)) or
                (not v_before_level and not v_level and v_before != v))):
                update_manually.append(k)
                logging.warning(f"Possibly manual update necessary: {k}")
                print('\033[93m' + "Possibly manual update necessary: " + k + '\033[0m')
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
        
        cast(dict, in_wiki_version)[k] = v

    return wiki_operations.edit_page(page, "return " + lua.encode(in_wiki_version), bot=False, wiki=wiki), update_manually


def convert_from_lua(module: str, wiki: str):
    content = wiki_operations.get_contents(module, wiki)
    if content == "":
        return {}
    return lua.decode(content[6:])


def get_clash_api_contents(session: requests.Session):
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Authorization': 'Bearer ' + os.environ.get("CLASH_API_TOKEN", ""),
    }

    response = session.get('https://api.clashofclans.com/v1/labels/players?limit=50', headers=headers)
    
    print(response.json())


async def clash_info(name: str, stat: str, level: int, module: str):
    stats = convert_from_lua(module, wiki_operations.DEFAULT_WIKI)

    if level > 0:
        return cast(dict, stats)[name][stat][level - 1]
    else:
        return cast(dict, stats)[name][stat]
