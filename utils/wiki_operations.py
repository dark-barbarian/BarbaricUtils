import threading

import requests

from utils.bot import Bot
from utils.fandom_auth import FandomAuth

DEFAULT_WIKI = "de.clashofclans"


class WikiOperations:
    """Utility functions for performing wiki operations via the MediaWiki API."""

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self.semaphore = threading.Semaphore()
        self.fandom_auth = FandomAuth(bot)
        self.wiki_sessions = self.fandom_auth.wiki_sessions

    async def _get_session(self, wiki: str) -> requests.Session:
        """Return the cached MediaWiki session for the given wiki."""
        return await self.fandom_auth.get_session(wiki)

    @staticmethod
    def _is_assert_user_failed(data: dict | list | str | None) -> bool:
        """Return whether MediaWiki rejected the user assertion and requires re-login."""
        if not isinstance(data, dict):
            return False

        error = data.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            info = str(error.get("info", "")).lower()
            if code == "assertuserfailed" or "assertuserfailed" in info:
                return True

        return False

    @staticmethod
    def _is_read_api_denied(data: dict | list | str | None) -> bool:
        """Return whether MediaWiki denied the API read request."""
        if not isinstance(data, dict):
            return False

        error = data.get("error")
        return isinstance(error, dict) and error.get("code") == "readapidenied"

    async def _get_csrf_token(self, subdomain: str) -> str:
        """Retrieve a CSRF token from the wiki API using the wiki-specific session, need to be logged in."""
        session = await self._get_session(subdomain)

        params = {"action": "query", "meta": "tokens", "type": "csrf", "format": "json"}

        self.semaphore.acquire()
        response = session.get(url=self.fandom_auth.create_wiki_api_url(subdomain), params=params)
        self.semaphore.release()

        data = response.json()
        return data["query"]["tokens"]["csrftoken"]

    async def _operate_on_wiki(self, wiki: str, payload: dict) -> dict | bool:
        """Perform a wiki operation with the given parameters; returns API response or False on error."""
        session = await self._get_session(wiki)
        payload["assert"] = "user"

        for attempt in range(2):
            self.semaphore.acquire()
            response = session.post(url=self.fandom_auth.create_wiki_api_url(wiki), data=payload)
            self.semaphore.release()

            data = response.json()

            if not self._is_assert_user_failed(data) or attempt == 1:
                return data

            self.bot.logger.warning("MediaWiki user assertion failed for %s; refreshing login and retrying.", wiki)
            if not await self.fandom_auth.login_to_wiki(wiki):
                self.bot.logger.error("Failed to log in to %s after user assertion failure.", wiki)
                return False
            payload["token"] = self._get_csrf_token(wiki)

        return False

    async def get_contents(self, page: str, wiki: str = DEFAULT_WIKI) -> str:
        """Fetch the contents of a wiki page as a string."""
        session = await self._get_session(wiki)
        payload = {
            "action": "query",
            "format": "json",
            "titles": page,
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "*",
            "rvlimit": "1",
        }

        data: dict = {}
        self.semaphore.acquire()
        for attempt in range(2):
            response = session.get(url=self.fandom_auth.create_wiki_api_url(wiki), params=payload)
            data = response.json()

            if not self._is_read_api_denied(data) or attempt == 1:
                break

            self.bot.logger.warning("MediaWiki denied a public read for %s; logging in and retrying.", wiki)

            if not await self.fandom_auth.login_to_wiki(wiki):
                self.bot.logger.error("Failed to log in to %s after read API denial.", wiki)
                self.semaphore.release()
                return ""
            session = await self._get_session(wiki)
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
        if not await self.fandom_auth.login_to_wiki(wiki):
            self.bot.logger.error("Failed to log in to %s before editing page %s.", wiki, page)
            return False

        params: dict[str, object] = {
            "action": "edit",
            "title": page,
            "text": content,
            "token": await self._get_csrf_token(wiki),
            "format": "json",
            "assert": "user",
        }
        if bot:
            params["bot"] = True

        return await self._operate_on_wiki(wiki, params)

    async def move_page(self, old_name: str, new_name: str, wiki: str = DEFAULT_WIKI) -> dict | bool:
        """Move a wiki page to a new name; returns API response or False on error."""
        if not await self.fandom_auth.login_to_wiki(wiki):
            self.bot.logger.error("Failed to log in to %s before moving page %s to %s.", wiki, old_name, new_name)
            return False

        params = {
            "action": "move",
            "format": "json",
            "from": old_name,
            "to": new_name,
            "noredirect": 1,
            "token": await self._get_csrf_token(wiki),
            "assert": "user",
        }

        return await self._operate_on_wiki(wiki, params)

    async def upload_image(self, title: str, source_url: str, wiki: str = DEFAULT_WIKI) -> dict | bool:
        """Upload an image to the wiki; returns API response or False on error."""
        if not await self.fandom_auth.login_to_wiki(wiki):
            self.bot.logger.error("Failed to log in to %s before uploading image %s.", wiki, title)
            return False

        params = {
            "action": "upload",
            "format": "json",
            "filename": title,
            "url": source_url,
            "ignorewarnings": True,
            "token": await self._get_csrf_token(wiki),
            "assert": "user",
        }

        return await self._operate_on_wiki(wiki, params)
