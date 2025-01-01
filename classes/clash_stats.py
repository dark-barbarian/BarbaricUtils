import csv
import logging

import requests
from slpp import slpp as lua

from classes import wiki_operations
import config

FILE_PATH = "./stats.csv"
DATA_MODULE_NAMES = [  # TODO: export both globals to file
    "Building/data",
    "Building2/data",
    "Building3/data",
    "Equipment/data",
    "Hero/data",
    "Hero2/data",
    "Pet/data",
    "Spell/data",
    "Spell3/data",
    "Supercharge/data",
    "Trap/data",
    "Trap2/data",
    "Trap3/data",
    "Troop/data",
    "Troop2/data",
    "Troop3/data",
]

# list of pages that have entries not contained in the CSV (e.g. AltDPS for Electro Titan), that need to be updated
# manually
PAGES_WITH_MANUAL_ENTRIES = [
    "Minenwerfer",
    "Monolith",
    "Zauberturm",

    "Armeelager (Bauarbeiterbasis)",
    "Uhrenturm",
    "Multimörser",
    "O.T.T.O.s Außenposten",

    "Mauer (Clanstadt)",
    "Kanone (Clanstadt)",
    "Speerwerfer",
    "Luftabwehr (Clanstadt)",
    "Multikanone",
    "Bombenturm (Clanstadt)",
    "Multimörser (Clanstadt)",
    "Supermagier-Turm",
    "Luftbomben (Clanstadt)",
    "Rasende Raketen",
    "Zerschmetterer (Clanstadt)",
    "Verborgener Megatesla",
    "Riesenkanone (Clanstadt)",
    "Raketenartillerie",
    "Infernoturm (Clanstadt)",
    "Mächtiger Bogen",
    "Superriesen-Posten",
    "Plünderkarren-Posten",
    "Superdrachen-Posten",

    "Kampfmaschine",
    "Kampfschrauber",

    "Phönix",
    "Giftechse",
    "Diggy",
    "Frosty",

    "Weihnachtspräsent",

    "Skelettfalle",
    "Tornadofalle",

    "Mine (Clanstadt)",
    "Megamine (Clanstadt)",
    "Kampfholzfalle",
    "Knallfalle",

    "Eisgolem",
    "Kampfholzwerfer",
    "Flammenschleuder",
    "Elektrotitanin",

    "Mutantenlakai",
    "Haudraufriese",
    "Skelettballon",
    "Drachenbaby (Bauarbeiterbasis)",
    "Nachthexe",
    "Super-P.E.K.K.A. (Bauarbeiterbasis)",
    "Elektrofeuermagier",

    "Fliegende Festung"
]


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
    reader = csv.DictReader(open(FILE_PATH))
    result = {}
    current_key = ""

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

    for k, v in in_wiki_version.items():
        target = v['Name']
        result_key, result_value = find_dict_by_target(result, target)
        v_before = v.copy()
        update_values(v, result_value)

        # there were changes to a page that has manually updated entries
        if (v_before != v) and (k in PAGES_WITH_MANUAL_ENTRIES):
            logging.warning(f"Possibly manual update necessary: {k}")
            print('\033[93m' + "Possibly manual update necessary: " + k + '\033[0m')

        if result_key != "":
            del result[result_key]

    for k, v in result.items():
        in_wiki_version[k] = v

    return wiki_operations.edit_page(page, "return " + lua.encode(in_wiki_version), bot=False, wiki=wiki)


def convert_from_lua(module: str, wiki: str):
    content = wiki_operations.get_contents(module, wiki)
    if content == "":
        return {}
    return lua.decode(content[6:])


def get_clash_api_contents(session: requests.Session):
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Authorization': 'Bearer ' + config.CLASH_API_TOKEN
    }

    response = session.get('https://api.clashofclans.com/v1/labels/players?limit=50', headers=headers)
    
    print(response.json())


async def clash_info(name: str, stat: str, level: int, module: str):
    stats = convert_from_lua(module, wiki_operations.DEFAULT_WIKI)

    if level > 0:
        return stats[name][stat][level - 1]
    else:
        return stats[name][stat]