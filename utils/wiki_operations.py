import threading

from utils.bot import Bot
from utils.fandom_auth import FandomAuth

DEFAULT_WIKI = "de.clashofclans"
SUBDOMAIN_PARTS_WITH_LANG_SUFFIX = 2


class WikiOperations:
    """Utility functions for performing wiki operations via the MediaWiki API."""

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self.semaphore = threading.Semaphore()
        self.fandom_auth = FandomAuth()

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
        self.fandom_auth.fandom_login()

        params = {"action": "query", "meta": "tokens", "format": "json"}

        self.semaphore.acquire()
        try:
            response = self.fandom_auth.session.get(url=self._create_url(subdomain), params=params)
            data = response.json()
        except Exception:
            self.bot.logger.exception("An error occurred when retrieving the CSRF token!")
            return ""
        finally:
            self.semaphore.release()

        return data["query"]["tokens"]["csrftoken"]

    async def get_contents(self, page: str, wiki: str = DEFAULT_WIKI) -> str:
        """Fetch the contents of a wiki page as a string."""
        self.fandom_auth.fandom_login()

        payload = {
            "action": "query",
            "format": "json",
            "titles": page,
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "*",
            "rvlimit": "1",
        }

        self.semaphore.acquire()
        try:
            response = self.fandom_auth.session.get(url=self._create_url(wiki), params=payload)
            data = response.json()
        except Exception as e:
            msg = f"An error occurred when retrieving the page content for page '{page}'!"
            self.bot.logger.exception(msg)
            if self.bot.reporter:
                await self.bot.reporter.report(e, context=msg)
            return ""
        finally:
            self.semaphore.release()

        if "error" in data:
            self.bot.logger.error("An error occurred when retrieving the page contents: %s", data["error"])
            return ""

        raw_stats = data["query"]["pages"]
        wiki_module_id = next(iter(raw_stats.keys()))
        if wiki_module_id == "-1":
            return ""
        return str(raw_stats[wiki_module_id]["revisions"][0]["slots"]["main"]["*"])

    async def edit_page(self, page: str, content: str, *, bot: bool = False, wiki: str = DEFAULT_WIKI) -> dict | bool:
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

        self.semaphore.acquire()
        response = self.fandom_auth.session.post(self._create_url(wiki), data=params)
        self.semaphore.release()

        try:
            return response.json()
        except Exception as e:
            msg = f"An error occurred when editing the page '{page}'!"
            self.bot.logger.exception(msg)
            if self.bot.reporter:
                await self.bot.reporter.report(e, context=msg)
            return False

    async def move_page(self, old_name: str, new_name: str, wiki: str = DEFAULT_WIKI) -> dict | bool:
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

        self.semaphore.acquire()
        response = self.fandom_auth.session.post(self._create_url(wiki), data=params)
        self.semaphore.release()

        try:
            return response.json()
        except Exception as e:
            msg = f"An error occurred when moving the page '{old_name}' to '{new_name}'!"
            self.bot.logger.exception(msg)
            if self.bot.reporter:
                await self.bot.reporter.report(e, context=msg)
            return False

    async def upload_image(self, title: str, source_url: str, wiki: str = DEFAULT_WIKI) -> dict | bool:
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

        self.semaphore.acquire()
        response = self.fandom_auth.session.post(self._create_url(wiki), data=params)
        self.semaphore.release()

        try:
            return response.json()
        except Exception as e:
            msg = f"An error occurred when uploading the image '{title}'!"
            self.bot.logger.exception(msg)
            if self.bot.reporter:
                await self.bot.reporter.report(e, context=msg)
            return False
