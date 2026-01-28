"""OAuth re-authorization module for Nest Thermostat Logger.

When the refresh token expires (typically after ~7 days for unverified OAuth apps),
this module automates the re-authorization flow by:
1. Starting a local HTTP server to capture the OAuth callback
2. Opening the browser to the PCM authorization URL
3. Exchanging the authorization code for new tokens
4. Updating config.json with the new refresh token
"""
from __future__ import annotations

import atexit
import http.server
import json
import logging
import pathlib
import smtplib
import socket
import tempfile
import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass
from email.mime.text import MIMEText
from typing import Optional

import requests

LOCKFILE_PATH = pathlib.Path(tempfile.gettempdir()) / "nest_poller_reauth.lock"

REAUTH_REDIRECT_PORT = 8085
REAUTH_REDIRECT_URI = f"http://localhost:{REAUTH_REDIRECT_PORT}"
PCM_AUTH_URL_TEMPLATE = (
    "https://nestservices.google.com/partnerconnections/{project_id}/auth"
    "?redirect_uri={redirect_uri}"
    "&access_type=offline"
    "&prompt=consent"
    "&client_id={client_id}"
    "&response_type=code"
    "&scope=https://www.googleapis.com/auth/sdm.service"
)
TOKEN_URL = "https://oauth2.googleapis.com/token"

# Default timeout: 7 days (user may take days to log in and complete OAuth)
DEFAULT_REAUTH_TIMEOUT_SECONDS = 7 * 24 * 60 * 60  # 604800 seconds


@dataclass
class EmailConfig:
    """Configuration for email notifications."""
    recipient: str
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587
    sender_email: str = ""
    sender_password: str = ""  # Gmail App Password

    @classmethod
    def from_dict(cls, data: dict) -> "EmailConfig":
        return cls(
            recipient=data["recipient"],
            smtp_server=data.get("smtp_server", "smtp.gmail.com"),
            smtp_port=data.get("smtp_port", 587),
            sender_email=data.get("sender_email", data["recipient"]),
            sender_password=data.get("sender_password", ""),
        )


def send_reauth_notification_email(email_config: EmailConfig, auth_url: str) -> bool:
    """
    Send an email notification that OAuth reauthorization is needed.

    Args:
        email_config: Email configuration with SMTP settings.
        auth_url: The OAuth authorization URL to include in the email.

    Returns:
        True if email sent successfully, False otherwise.
    """
    if not email_config.sender_password:
        logging.warning("Email notification skipped: no sender_password configured")
        return False

    hostname = socket.gethostname()
    subject = f"Nest Thermostat Logger - OAuth Reauthorization Needed ({hostname})"
    body = f"""The Nest Thermostat Logger on {hostname} needs you to complete OAuth reauthorization.

Click here to authorize:
{auth_url}

After authorizing, you'll be redirected to localhost:8085 which will be captured by the poller running on {hostname}.

The poller will continue waiting (up to 7 days) for you to authorize.

This is an automated message from the Nest Thermostat Logger.
"""

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = email_config.sender_email
    msg["To"] = email_config.recipient

    try:
        with smtplib.SMTP(email_config.smtp_server, email_config.smtp_port, timeout=30) as server:
            server.starttls()
            server.login(email_config.sender_email, email_config.sender_password)
            server.sendmail(email_config.sender_email, email_config.recipient, msg.as_string())
        logging.info("Sent reauthorization notification email to %s", email_config.recipient)
        return True
    except smtplib.SMTPAuthenticationError as e:
        logging.error("Email authentication failed. Check sender_password (use Gmail App Password): %s", e)
        return False
    except Exception as e:
        logging.error("Failed to send notification email: %s", e)
        return False

def _acquire_reauth_lock() -> bool:
    """Try to acquire the re-authorization lock.

    Returns:
        True if lock acquired, False if another re-auth is in progress.
    """
    import time

    if LOCKFILE_PATH.exists():
        # Check if the lock is stale (older than 7 days + 1 hour buffer)
        try:
            age_seconds = time.time() - LOCKFILE_PATH.stat().st_mtime
            if age_seconds < DEFAULT_REAUTH_TIMEOUT_SECONDS + 3600:
                return False
            # Stale lock, remove it
            LOCKFILE_PATH.unlink(missing_ok=True)
        except OSError:
            return False

    try:
        LOCKFILE_PATH.write_text(str(pathlib.Path(__file__).parent))
        atexit.register(_release_reauth_lock)
        return True
    except OSError:
        return False


def _release_reauth_lock() -> None:
    """Release the re-authorization lock."""
    try:
        LOCKFILE_PATH.unlink(missing_ok=True)
    except OSError:
        pass


SUCCESS_HTML = """<!DOCTYPE html>
<html>
<head><title>Authorization Successful</title></head>
<body style="font-family: sans-serif; text-align: center; padding: 50px;">
<h1>Authorization Successful</h1>
<p>You can close this window and return to the Nest Thermostat Logger.</p>
</body>
</html>
"""

ERROR_HTML = """<!DOCTYPE html>
<html>
<head><title>Authorization Failed</title></head>
<body style="font-family: sans-serif; text-align: center; padding: 50px;">
<h1>Authorization Failed</h1>
<p>Error: {error}</p>
<p>Please try again.</p>
</body>
</html>
"""


class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler that captures the OAuth callback."""

    def log_message(self, format: str, *args) -> None:
        """Suppress default HTTP logging."""
        pass

    def do_GET(self) -> None:
        """Handle GET request from OAuth callback."""
        parsed = urllib.parse.urlparse(self.path)
        query_params = urllib.parse.parse_qs(parsed.query)

        if "code" in query_params:
            self.server.auth_code = query_params["code"][0]
            self.server.auth_error = None
            self._send_response(200, SUCCESS_HTML)
        elif "error" in query_params:
            error_msg = query_params.get("error_description", query_params["error"])[0]
            self.server.auth_code = None
            self.server.auth_error = error_msg
            self._send_response(400, ERROR_HTML.format(error=error_msg))
        else:
            self._send_response(400, ERROR_HTML.format(error="No authorization code received"))
            return

        # Signal that we received the callback
        self.server.callback_received.set()

    def _send_response(self, status: int, html: str) -> None:
        """Send HTML response to browser."""
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))


class OAuthCallbackServer(http.server.HTTPServer):
    """HTTP server that captures OAuth callback."""

    def __init__(self, port: int):
        super().__init__(("localhost", port), OAuthCallbackHandler)
        self.auth_code: Optional[str] = None
        self.auth_error: Optional[str] = None
        self.callback_received = threading.Event()


def wait_for_authorization_code(timeout_seconds: int = DEFAULT_REAUTH_TIMEOUT_SECONDS) -> str:
    """Start local server and wait for OAuth callback.

    The server runs continuously until the OAuth callback is received or timeout.
    This allows waiting for days if needed (user may not be logged in).

    Args:
        timeout_seconds: Maximum time to wait for authorization (default: 7 days).

    Returns:
        The authorization code from the callback.

    Raises:
        RuntimeError: If authorization fails or times out.
    """
    try:
        server = OAuthCallbackServer(REAUTH_REDIRECT_PORT)
    except OSError as e:
        raise RuntimeError(
            f"Could not start OAuth callback server on port {REAUTH_REDIRECT_PORT}. "
            f"Port may be in use. Error: {e}"
        ) from e

    # Run server continuously in a background thread (not just one request)
    # This ensures we capture the callback even if other requests hit the port
    def serve_until_callback():
        while not server.callback_received.is_set():
            server.handle_request()

    server_thread = threading.Thread(target=serve_until_callback, daemon=True)
    server_thread.start()

    timeout_days = timeout_seconds / 86400
    if timeout_days >= 1:
        logging.info(
            "Waiting for OAuth callback (timeout: %.1f days). "
            "Complete authorization in the browser tab when you log in.",
            timeout_days
        )
    else:
        logging.info("Waiting for OAuth callback (timeout: %d seconds)...", timeout_seconds)

    # Wait for callback with timeout
    if not server.callback_received.wait(timeout=timeout_seconds):
        server.server_close()
        raise RuntimeError(
            f"OAuth authorization timed out after {timeout_seconds} seconds. "
            "Please try again with --reauth flag."
        )

    server.server_close()

    if server.auth_error:
        raise RuntimeError(f"OAuth authorization failed: {server.auth_error}")

    if not server.auth_code:
        raise RuntimeError("No authorization code received from OAuth callback.")

    return server.auth_code


def exchange_code_for_tokens(
    code: str,
    client_id: str,
    client_secret: str,
) -> tuple[str, str]:
    """Exchange authorization code for access and refresh tokens.

    Args:
        code: Authorization code from OAuth callback.
        client_id: OAuth client ID.
        client_secret: OAuth client secret.

    Returns:
        Tuple of (access_token, refresh_token).

    Raises:
        RuntimeError: If token exchange fails.
    """
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REAUTH_REDIRECT_URI,
    }

    response = requests.post(TOKEN_URL, data=payload, timeout=15)

    if response.status_code != 200:
        error_data = {}
        if response.headers.get("content-type", "").startswith("application/json"):
            error_data = response.json()
        error_desc = error_data.get("error_description", response.text)
        raise RuntimeError(f"Token exchange failed: {response.status_code} {error_desc}")

    token_data = response.json()
    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")

    if not access_token or not refresh_token:
        raise RuntimeError("Token response missing access_token or refresh_token")

    return access_token, refresh_token


def update_config_refresh_token(config_path: pathlib.Path, new_token: str) -> None:
    """Update the refresh_token in config.json, preserving other fields.

    Args:
        config_path: Path to config.json.
        new_token: New refresh token to save.
    """
    with config_path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)

    raw["refresh_token"] = new_token

    with config_path.open("w", encoding="utf-8") as fh:
        json.dump(raw, fh, indent=2)

    logging.info("Updated refresh_token in %s", config_path)


class ReauthorizationInProgressError(RuntimeError):
    """Raised when another re-authorization is already in progress."""


def perform_reauthorization(
    config_path: pathlib.Path,
    project_id: str,
    client_id: str,
    client_secret: str,
    timeout_seconds: int = DEFAULT_REAUTH_TIMEOUT_SECONDS,
    email_config: Optional[EmailConfig] = None,
) -> tuple[str, str]:
    """Perform full OAuth re-authorization flow.

    This function:
    1. Opens the browser to the PCM authorization URL
    2. Sends email notification (if configured)
    3. Starts a local server to capture the callback (waits up to 7 days by default)
    4. Exchanges the authorization code for tokens
    5. Updates config.json with the new refresh token

    Args:
        config_path: Path to config.json file.
        project_id: Nest SDM project ID.
        client_id: OAuth client ID.
        client_secret: OAuth client secret.
        timeout_seconds: Maximum time to wait for user authorization (default: 7 days).
        email_config: Optional email configuration for notifications.

    Returns:
        Tuple of (access_token, refresh_token).

    Raises:
        ReauthorizationInProgressError: If another re-auth is already running.
        RuntimeError: If any step of the authorization fails.
    """
    # Check if another re-auth is already in progress
    if not _acquire_reauth_lock():
        raise ReauthorizationInProgressError(
            "Re-authorization already in progress (another instance is waiting for OAuth). "
            "Complete the authorization in your browser or wait for it to time out."
        )

    try:
        logging.info("Starting OAuth re-authorization flow...")

        # Build PCM authorization URL
        auth_url = PCM_AUTH_URL_TEMPLATE.format(
            project_id=project_id,
            redirect_uri=urllib.parse.quote(REAUTH_REDIRECT_URI, safe=""),
            client_id=client_id,
        )

        logging.info("Opening browser for authorization...")
        logging.debug("Authorization URL: %s", auth_url)

        # Open browser
        if not webbrowser.open(auth_url):
            logging.warning("Could not open browser automatically.")
            logging.info("Please open this URL manually:\n%s", auth_url)

        # Send email notification if configured
        if email_config:
            send_reauth_notification_email(email_config, auth_url)

        # Wait for callback
        auth_code = wait_for_authorization_code(timeout_seconds)
        logging.info("Received authorization code, exchanging for tokens...")

        # Exchange code for tokens
        access_token, refresh_token = exchange_code_for_tokens(
            code=auth_code,
            client_id=client_id,
            client_secret=client_secret,
        )

        # Update config.json
        update_config_refresh_token(config_path, refresh_token)

        logging.info("Re-authorization complete!")
        return access_token, refresh_token
    finally:
        _release_reauth_lock()
