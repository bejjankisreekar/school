"""
Utility helpers for core app.
"""


def add_warning_once(request, session_key: str, message: str):
    """
    Add a warning message only if it has not been shown this session.
    Prevents repeated "Invalid setup" / "Fee module not available" messages.
    """
    from django.contrib import messages

    if not request.session.get(session_key):
        messages.warning(request, message)
        request.session[session_key] = True
