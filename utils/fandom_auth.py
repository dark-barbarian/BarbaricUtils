import json
import logging
import os
import threading
from dataclasses import dataclass

import requests

SESSION = requests.Session()


@dataclass
class AuthState:
    """Mutable container for Fandom auth state (session token)."""

    session_token: str = ""


AUTH_STATE = AuthState()

SEMAPHORE_AUTH = threading.Semaphore()

logger = logging.getLogger(__name__)

HTTP_OK = 200
HTTP_NO_CONTENT = 204


def fandom_login() -> None:
    """Authenticate against Fandom services, storing a session token.

    Uses global `SESSION` and serializes with `SEMAPHORE_AUTH`.
    """
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

    SEMAPHORE_AUTH.acquire(blocking=True)

    try:
        response = SESSION.get("https://services.fandom.com/kratos-public/self-service/login/api")
        if response.status_code != HTTP_OK:
            return
        data = response.json()

        response = SESSION.post(data["ui"]["action"], headers=headers, data=payload)
        if response.status_code != HTTP_OK:
            return
        data = response.json()

        AUTH_STATE.session_token = data["session_token"]
    except Exception:
        logger.exception("An error occurred when logging into Fandom!")
    finally:
        SEMAPHORE_AUTH.release()


def fandom_logout() -> bool:
    """Log out from Fandom services using the current session token."""
    headers = {
        "Content-Type": "application/json",
    }

    payload = {"session_token": AUTH_STATE.session_token}

    response = SESSION.delete(
        "https://services.fandom.com/kratos-public/self-service/logout/api", headers=headers, data=json.dumps(payload)
    )

    return response.status_code == HTTP_NO_CONTENT
