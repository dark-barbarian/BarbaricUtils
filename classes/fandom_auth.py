import json
import threading

import requests

import config


SESSION = requests.Session()
SESSION_TOKEN = ""

SEMAPHORE_AUTH = threading.Semaphore()


def fandom_login():
    # <Response [400]> {'error': {'id': 'session_already_available', 'code': 400, 'status': 'Bad Request', '
    # <Response [410]> {'error': {'id': 'self_service_flow_expired', 'code': 410, 'status': 'Gone'
    headers = {
        'Connection': 'Keep alive',
        'Content-Type': 'application/x-www-form-urlencoded',
        'User-Agent': 'DarkBarbarian/Bot'
    }

    payload = {
        'method': 'password',
        'identifier': config.FANDOM_USERNAME,
        'password': config.FANDOM_PASSWORD
    }

    SEMAPHORE_AUTH.acquire(blocking=True)
    response = SESSION.get('https://services.fandom.com/kratos-public/self-service/login/api')
    data = response.json()
    if response.status_code != 200:
        SEMAPHORE_AUTH.release()
        return False

    response = SESSION.post(data['ui']['action'], headers=headers, data=payload)
    data = response.json()
    if response.status_code != 200:
        SEMAPHORE_AUTH.release()
        return False

    global SESSION_TOKEN
    SESSION_TOKEN = data['session_token']
    SEMAPHORE_AUTH.release()
    return True


def fandom_logout():
    headers = {
        'Content-Type': 'application/json',
    }

    payload = {
        'session_token': SESSION_TOKEN
    }

    response = SESSION.delete('https://services.fandom.com/kratos-public/self-service/logout/api', headers=headers,
                              data=json.dumps(payload))

    if response.status_code != 204:
        return False

    return True