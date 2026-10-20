import logging
import os
import threading

import requests

from utils.bot import Bot

HTTP_OK = 200
HTTP_NO_CONTENT = 204

SUBDOMAIN_PARTS_WITH_LANG_SUFFIX = 2


class FandomAuth:
    """Manage MediaWiki login sessions for each wiki independently."""

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self.wiki_sessions: dict[str, requests.Session] = {}
        self.logger = logging.getLogger(__name__)
        self.semaphore = threading.Semaphore()

    @staticmethod
    def create_wiki_api_url(subdomain: str) -> str:
        """Build the MediaWiki API URL for a given Fandom wiki subdomain."""
        parts = subdomain.split(".")
        parts.reverse()

        url = "https://" + parts[0] + ".fandom.com/"
        if len(parts) == SUBDOMAIN_PARTS_WITH_LANG_SUFFIX:
            url = url + parts[1] + "/"

        return url + "api.php"

    async def get_session(self, wiki: str) -> requests.Session:
        """Return a per-wiki requests session."""
        if wiki not in self.wiki_sessions:
            self.wiki_sessions[wiki] = requests.Session()
        return self.wiki_sessions[wiki]

    async def login_to_wiki(self, wiki: str) -> bool:
        """Login to the given wiki using the MediaWiki login flow, returning True on success or False on failure."""
        session = self.wiki_sessions.setdefault(wiki, requests.Session())

        username = os.environ.get("FANDOM_USERNAME")
        password = os.environ.get("FANDOM_PASSWORD")

        api_url = self.create_wiki_api_url(wiki)

        self.semaphore.acquire()
        try:
            response = session.get(
                api_url,
                params={"action": "query", "meta": "tokens", "type": "login", "format": "json"},
            )
            if response.status_code != HTTP_OK:
                msg = f"Login token request failed for {wiki}: {response.status_code}"
                if self.bot.exception_reporter:
                    await self.bot.exception_reporter.report(None, error_message=msg, include_traceback=False)
                return False

            data = response.json()
            login_token = data["query"]["tokens"]["logintoken"]

            payload = {
                "action": "login",
                "lgname": username,
                "lgpassword": password,
                "lgtoken": login_token,
                "format": "json",
            }

            response = session.post(api_url, data=payload)

            if response.status_code != HTTP_OK:
                msg = f"Login request failed for {wiki}: {response.status_code}"
                if self.bot.exception_reporter:
                    await self.bot.exception_reporter.report(None, error_message=msg, include_traceback=False)
                return False

            result = response.json().get("login", {}).get("result")

            if result == "Aborted":
                self.logger.warning(
                    "Login request was aborted for %s. Assuming login is already valid. Response was: %s",
                    wiki,
                    response.text,
                )
                return True

            if result != "Success":
                msg = f"Login did not succeed for {wiki}: {response.text[:500]}"
                if self.bot.exception_reporter:
                    await self.bot.exception_reporter.report(None, error_message=msg)
                return False
        finally:
            self.semaphore.release()

        return True

    def fandom_logout(self, wiki: str, csrf_token: str) -> bool:
        """Log out the session for a given wiki."""
        session = self.wiki_sessions.get(wiki)
        if session is None or csrf_token == r"+\\":  # noqa: S105
            return True

        self.semaphore.acquire()
        response = session.post(
            url=self.create_wiki_api_url(wiki),
            data={"action": "logout", "token": csrf_token, "format": "json"},
        )
        self.semaphore.release()

        if response.status_code == HTTP_OK:
            self.wiki_sessions.pop(wiki, None)
        return response.status_code == HTTP_OK
