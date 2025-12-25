import logging
import threading

from utils import fandom_auth

DEFAULT_WIKI = "de.clashofclans"
SUBDOMAIN_PARTS_WITH_LANG_SUFFIX = 2


logger = logging.getLogger(__name__)


class WikiOperations:
    """Utility functions for performing wiki operations via the MediaWiki API."""

    def __init__(self) -> None:
        self.semaphore = threading.Semaphore()

    def _create_url(self, subdomain: str = DEFAULT_WIKI) -> str:
        """Build the base API URL for the provided subdomain."""
        parts = subdomain.split(".")
        parts.reverse()

        url = "https://" + parts[0] + ".fandom.com/"
        if len(parts) == SUBDOMAIN_PARTS_WITH_LANG_SUFFIX:
            url = url + parts[1] + "/"
        return url + "api.php"

    def _get_csrf_token(self, subdomain: str) -> str:
        """Retrieve a CSRF token from the wiki API."""
        fandom_auth.fandom_login()

        params = {"action": "query", "meta": "tokens", "format": "json"}

        self.semaphore.acquire(blocking=True)
        try:
            response = fandom_auth.SESSION.get(url=self._create_url(subdomain), params=params)
            data = response.json()
        except Exception:
            logger.exception("An error occurred when retrieving the CSRF token!")
            return ""
        finally:
            self.semaphore.release()

        return data["query"]["tokens"]["csrftoken"]

    def get_contents(self, page: str, wiki: str = DEFAULT_WIKI) -> str:
        """Fetch the contents of a wiki page as a string."""
        fandom_auth.fandom_login()

        payload = {
            "action": "query",
            "format": "json",
            "titles": page,
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "*",
            "rvlimit": "1",
        }

        self.semaphore.acquire(blocking=True)
        try:
            response = fandom_auth.SESSION.get(url=self._create_url(wiki), params=payload)
            data = response.json()
        except Exception:
            logger.exception("An error occurred when retrieving the page contents!")
            return ""
        finally:
            self.semaphore.release()

        if "error" in data:
            logger.error("An error occurred when retrieving the page contents: %s", data["error"])
            return ""

        raw_stats = data["query"]["pages"]
        wiki_module_id = next(iter(raw_stats.keys()))
        if wiki_module_id == "-1":
            return ""
        return str(raw_stats[wiki_module_id]["revisions"][0]["slots"]["main"]["*"])

    def edit_page(self, page: str, content: str, *, bot: bool = False, wiki: str = DEFAULT_WIKI) -> dict | bool:
        """Edit a wiki page with the given content; returns API response or False on error."""
        params: dict[str, object] = {
            "action": "edit",
            "title": page,
            "text": content,
            "token": self._get_csrf_token(wiki),
            "format": "json",
            "assert": "user",
        }
        if bot:
            params["bot"] = True

        self.semaphore.acquire(blocking=True)
        response = fandom_auth.SESSION.post(self._create_url(wiki), data=params)
        self.semaphore.release()

        try:
            return response.json()
        except Exception:
            logger.exception("An error occurred when editing the page!")
            return False

    def move_page(self, old_name: str, new_name: str, wiki: str = DEFAULT_WIKI) -> dict | bool:
        """Move a wiki page to a new name; returns API response or False on error."""
        params = {
            "action": "move",
            "format": "json",
            "from": old_name,
            "to": new_name,
            "noredirect": 1,
            "token": self._get_csrf_token(wiki),
            "assert": "user",
        }

        self.semaphore.acquire(blocking=True)
        response = fandom_auth.SESSION.post(self._create_url(wiki), data=params)
        self.semaphore.release()

        try:
            return response.json()
        except Exception:
            logger.exception("An error occurred when moving the page!")
            return False

    def upload_image(self, title: str, source_url: str, wiki: str = DEFAULT_WIKI) -> dict | bool:
        """Upload an image to the wiki; returns API response or False on error."""
        params = {
            "action": "upload",
            "format": "json",
            "filename": title,
            "url": source_url,
            "ignorewarnings": True,
            "token": self._get_csrf_token(wiki),
            "assert": "user",
        }

        self.semaphore.acquire(blocking=True)
        response = fandom_auth.SESSION.post(self._create_url(wiki), data=params)
        self.semaphore.release()

        try:
            return response.json()
        except Exception:
            logger.exception("An error occurred when uploading the image!")
            return False
