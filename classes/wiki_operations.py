import threading

from classes import fandom_auth


URL = ""
SUBDOMAIN = ""
DEFAULT_WIKI = "de.clashofclans"

SEMAPHORE_WIKI = threading.Semaphore()


def create_url(subdomain: str = DEFAULT_WIKI):
    global URL, SUBDOMAIN
    if SUBDOMAIN == subdomain:
        return
    SUBDOMAIN = subdomain

    parts = subdomain.split('.')
    parts.reverse()

    URL = "https://" + parts[0] + ".fandom.com/"
    if len(parts) == 2:
        URL = URL + parts[1] + "/"
    URL = URL + "api.php"


def get_csrf_token():
    fandom_auth.fandom_login()

    params = {
        "action": "query",
        "meta": "tokens",
        "format": "json"
    }

    SEMAPHORE_WIKI.acquire(blocking=True)
    response = fandom_auth.SESSION.get(url=URL, params=params)
    data = response.json()
    SEMAPHORE_WIKI.release()

    return data['query']['tokens']['csrftoken']


def get_contents(page: str, wiki: str = DEFAULT_WIKI):
    fandom_auth.fandom_login()

    create_url(wiki)

    payload = {
        'action': 'query',
        'format': 'json',
        'titles': page,
        'prop': 'revisions',
        'rvprop': 'content',
        'rvslots': '*',
        'rvlimit': '1'
    }

    SEMAPHORE_WIKI.acquire(blocking=True)
    response = fandom_auth.SESSION.get(url=URL, params=payload)
    data = response.json()
    SEMAPHORE_WIKI.release()

    if "error" in data:
        return ""

    raw_stats = data['query']['pages']
    wiki_module_id = list(raw_stats.keys())[0]
    if wiki_module_id == '-1':
        return ""
    return str(raw_stats[wiki_module_id]['revisions'][0]['slots']['main']['*'])


def edit_page(page: str, content: str, bot: bool = False, wiki: str = DEFAULT_WIKI):
    create_url(wiki)

    params = {
        "action": "edit",
        "title": page,
        "text": content,
        "token": get_csrf_token(),
        "format": "json",
        "assert": "user"
    }
    if bot:
        params["bot"] = True

    SEMAPHORE_WIKI.acquire(blocking=True)
    response = fandom_auth.SESSION.post(URL, data=params)
    SEMAPHORE_WIKI.release()

    return response.json()


def move_page(old_name: str, new_name: str, wiki: str = DEFAULT_WIKI):
    create_url(wiki)

    params = {
        "action": "move",
        "format": "json",
        "from": old_name,
        "to": new_name,
        "noredirect": 1,
        "token": get_csrf_token(),
        "assert": "user"
    }

    SEMAPHORE_WIKI.acquire(blocking=True)
    response = fandom_auth.SESSION.post(URL, data=params)
    SEMAPHORE_WIKI.release()

    return response.json()


def upload_image(title: str, source_url: str, wiki: str = DEFAULT_WIKI):
    create_url(wiki)

    params = {
        "action": "upload",
        "format": "json",
        "filename": title,
        "url": source_url,
        "ignorewarnings": True,
        "token": get_csrf_token(),
        "assert": "user"
    }

    SEMAPHORE_WIKI.acquire(blocking=True)
    response = fandom_auth.SESSION.post(URL, data=params)
    SEMAPHORE_WIKI.release()

    return response.json()