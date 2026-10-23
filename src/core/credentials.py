"""Secrets in the OS store (Windows Credential Manager), through src.platform.

Used for the SMTP password (JARVIS/SMTP/<user>). Secrets never go into config,
prompts or logs.
"""
from typing import Optional

from src.platform import current


def write(target: str, username: str, secret: str) -> None:
    current().write_secret(target, username, secret)


def read(target: str) -> Optional[str]:
    try:
        return current().read_secret(target)
    except NotImplementedError:
        return None


def delete(target: str) -> None:
    try:
        current().delete_secret(target)
    except NotImplementedError:
        pass
