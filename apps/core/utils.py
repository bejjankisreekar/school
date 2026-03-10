"""
Utility helpers for core app.
"""


def add_warning_once(request, session_key: str, message: str):
    """
    No-op: Bootstrap alert notifications have been removed from the project.
    Kept for API compatibility with existing callers.
    """
    pass
