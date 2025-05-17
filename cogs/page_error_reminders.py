import json
import logging
import requests


CATEGORY_CHECK_DAY_HOUR = (5, 12)  # (weekday (0 to 6), hour)

CHANNEL_ID = 1372252214814310651

CATEGORIES_JSON_FILE_PATH = "./wikicategories.json"
WIKI_CATEGORIES = {}


def fetch_categories(wiki: str, page_title: str):
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

def fetch_category_members(wiki: str, category: str):
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
