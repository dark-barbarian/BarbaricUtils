import json
import logging
import os
import threading

import requests

HTTP_OK = 200
HTTP_NO_CONTENT = 204


class FandomAuth:
    """Class to handle Fandom authentication and session management."""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session_token = ""
        self.semaphore = threading.Semaphore()
        self.logger = logging.getLogger(__name__)

    def fandom_login(self) -> None:
        """Authenticate against Fandom services, storing a session token."""
        # <Response [400]> {'error': {'id': 'session_already_available', 'code': 400, 'status': 'Bad Request', '
        # <Response [410]> {'error': {'id': 'self_service_flow_expired', 'code': 410, 'status': 'Gone'
        headers = {
            "Connection": "Keep alive",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "DarkBarbarian/Bot",
        }

        payload = {
            "method": "password",
            "identifier": os.environ.get("FANDOM_USERNAME", ""),
            "password": os.environ.get("FANDOM_PASSWORD", ""),
        }

        self.semaphore.acquire()

        try:
            response = self.session.get("https://services.fandom.com/kratos-public/self-service/login/api")
            if response.status_code != HTTP_OK:
                return
            data = response.json()

            response = self.session.post(data["ui"]["action"], headers=headers, data=payload)
            if response.status_code != HTTP_OK:
                return
            data = response.json()

            self.session_token = data["session_token"]
        except Exception:
            if self.logger:
                self.logger.exception("An error occurred when logging into Fandom!")
        finally:
            self.semaphore.release()

    def fandom_logout(self) -> bool:
        """Log out from Fandom services using the current session token."""
        headers = {
            "Content-Type": "application/json",
        }

        payload = {"session_token": self.session_token}

        response = self.session.delete(
            "https://services.fandom.com/kratos-public/self-service/logout/api",
            headers=headers,
            data=json.dumps(payload),
        )

        return response.status_code == HTTP_NO_CONTENT
