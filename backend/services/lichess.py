"""Lichess API fetching and PGN parsing for Korchess."""

import hashlib
import io
import logging
import os
import re
import time
from typing import Optional
import psycopg

import chess.pgn
import httpx

from schemas import INVALID_USERNAME_MESSAGE, is_valid_chess_username
from services.opening_match import game_to_uci_plies, best_opening_match
LICHESS_API_BASE = "https://lichess.org/api"
NETWORK_ERROR_MESSAGE = "Could not reach Lichess. Please try again later."

logger = logging.getLogger(__name__)


class LichessAPIError(Exception):
    """Custom exception for Lichess API errors."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)


def _get(url: str, headers: dict, params: dict | None = None, timeout: float = 30.0) -> httpx.Response:
    """GET from Lichess. Network failures become a generic 502; details are logged, not returned.

    Authenticates with LICHESS_API_TOKEN when it is set, for Lichess's higher rate limits.
    """
    token = os.environ.get("LICHESS_API_TOKEN", "").strip()
    if token:
        headers = {**headers, "Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(timeout=timeout) as client:
            return client.get(url, headers=headers, params=params)
    except httpx.HTTPError as e:
        logger.warning("Lichess request to %s failed: %r", url, e)
        raise LichessAPIError(NETWORK_ERROR_MESSAGE, status_code=502) from e


def fetch_lichess_profile(username: str) -> dict:
    """
    Fetch a user's profile from Lichess, including ratings for all time controls.

    Returns:
        dict with keys: username, bullet_rating, blitz_rating, rapid_rating, classical_rating

    Raises:
        LichessAPIError: If user not found or API error.
    """
    if not is_valid_chess_username(username):
        raise LichessAPIError(INVALID_USERNAME_MESSAGE, status_code=400)
    url = f"{LICHESS_API_BASE}/user/{username}"
    headers = {"Accept": "application/json"}

    def make_request() -> httpx.Response:
        return _get(url, headers, timeout=30.0)

    response = make_request()

    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After", "60")
        try:
            wait_seconds = int(retry_after)
        except ValueError:
            wait_seconds = 60
        wait_seconds = min(wait_seconds, 120)
        time.sleep(wait_seconds)

        response = make_request()
        if response.status_code == 429:
            raise LichessAPIError(
                "Rate limited by Lichess. Please try again later.",
                status_code=429
            )

    if response.status_code == 404:
        raise LichessAPIError(
            f"User '{username}' not found on Lichess.",
            status_code=404
        )

    if response.status_code != 200:
        raise LichessAPIError(
            f"Lichess API error: {response.status_code}",
            status_code=response.status_code
        )

    try:
        data = response.json()
    except ValueError as e:
        logger.warning("Unparseable Lichess profile response for %s: %r", username, e)
        raise LichessAPIError("Unexpected response from Lichess.", status_code=502) from e
    perfs = data.get("perfs", {})

    return {
        "username": data.get("username", username),
        "bullet_rating": perfs.get("bullet", {}).get("rating"),
        "blitz_rating": perfs.get("blitz", {}).get("rating"),
        "rapid_rating": perfs.get("rapid", {}).get("rating"),
        "classical_rating": perfs.get("classical", {}).get("rating"),
    }


def fetch_lichess_pgn(username: str, max_games: int = 1000, since: int | None = None) -> str:
    """
    Fetch games for a user from Lichess as PGN text.
    Handles rate limiting with Retry-After header.

    Args:
        since: Milliseconds since epoch. Only fetch games played after this timestamp.
    """
    if not is_valid_chess_username(username):
        raise LichessAPIError(INVALID_USERNAME_MESSAGE, status_code=400)
    url = f"{LICHESS_API_BASE}/games/user/{username}"
    headers = {
        "Accept": "application/x-chess-pgn",
    }
    params: dict = {
        "rated": "true",
        "opening": "true",
        "clocks": "true",
        "max": max_games,
    }
    if since is not None:
        params["since"] = since

    def make_request() -> httpx.Response:
        return _get(url, headers, params=params, timeout=60.0)

    response = make_request()

    # Handle rate limiting with retry
    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After", "60")
        try:
            wait_seconds = int(retry_after)
        except ValueError:
            wait_seconds = 60

        # Cap wait time for reasonable UX
        wait_seconds = min(wait_seconds, 120)
        time.sleep(wait_seconds)

        # Retry once
        response = make_request()
        if response.status_code == 429:
            raise LichessAPIError(
                "Rate limited by Lichess. Please try again later.",
                status_code=429
            )

    if response.status_code == 404:
        raise LichessAPIError(
            f"User '{username}' not found on Lichess.",
            status_code=404
        )

    if response.status_code != 200:
        raise LichessAPIError(
            f"Lichess API error: {response.status_code}",
            status_code=response.status_code
        )

    return response.text


def extract_site_game_id(site_header: Optional[str], pgn_text: str) -> str:
    """
    Extract game ID from Site header URL or fallback to PGN hash.
    Site format: "https://lichess.org/abcd1234" or "https://lichess.org/abcd1234#0"
    """
    if site_header:
        # Match lichess game ID pattern
        match = re.search(r"lichess\.org/([a-zA-Z0-9]{8,12})", site_header)
        if match:
            return match.group(1)

    # Fallback to stable hash of PGN
    return hashlib.sha256(pgn_text.encode()).hexdigest()[:16]


def classify_time_control(headers: dict) -> str:
    """
    Classify time control into bullet/blitz/rapid/classical/unknown.
    Prefers Speed tag, then parses TimeControl header.
    """
    # Prefer Speed tag if present (Lichess-specific)
    speed = headers.get("Speed", "").lower()
    if speed in ("bullet", "blitz", "rapid", "classical"):
        return speed

    # Parse TimeControl header (format: "180+0" or "300+3")
    time_control = headers.get("TimeControl", "")
    if time_control and time_control != "-":
        match = re.match(r"(\d+)", time_control)
        if match:
            base_seconds = int(match.group(1))
            if base_seconds < 180:
                return "bullet"
            elif base_seconds < 480:
                return "blitz"
            elif base_seconds < 1500:
                return "rapid"
            else:
                return "classical"

    return "unknown"


def normalize_result(result_header: str, is_white: bool) -> str:
    """
    Normalize PGN result to win/draw/loss from user perspective.
    """
    if result_header == "1-0":
        return "win" if is_white else "loss"
    elif result_header == "0-1":
        return "loss" if is_white else "win"
    elif result_header == "1/2-1/2":
        return "draw"
    else:
        # Unknown result (e.g., "*" for ongoing/abandoned)
        return "unknown"


def parse_int_or_none(value: Optional[str]) -> Optional[int]:
    """Parse string to int, returning None if invalid."""
    if value is None:
        return None
    try:
        # Handle rating with provisional marker like "1500?"
        clean_value = value.rstrip("?")
        return int(clean_value)
    except ValueError:
        return None


def get_played_at(headers: dict) -> Optional[str]:
    """Extract played_at timestamp from headers."""
    utc_date = headers.get("UTCDate", headers.get("Date", ""))
    utc_time = headers.get("UTCTime", "")

    if utc_date:
        # Format: YYYY.MM.DD -> YYYY-MM-DD
        date_str = utc_date.replace(".", "-")
        if utc_time:
            return f"{date_str}T{utc_time}Z"
        return date_str

    return None


def opening_from_headers(headers: dict) -> tuple[str, str]:
    """(eco, opening_name) from Lichess's ECO/Opening PGN headers; "?" or missing means unknown.

    Used when the openings table has no match (e.g. it hasn't been seeded). Games
    without an opening_id are grouped by opening_name, the same as Chess.com's
    header-derived names.
    """
    eco = (headers.get("ECO") or "").strip().upper()
    if not re.fullmatch(r"[A-E]\d\d", eco):
        eco = "UNKNOWN"
    opening_name = (headers.get("Opening") or "").strip()
    if not opening_name or opening_name == "?":
        opening_name = "Unknown"
    return eco, opening_name


def game_to_pgn_string(game: chess.pgn.Game) -> str:
    """Convert a parsed game back to PGN string."""
    exporter = chess.pgn.StringExporter(headers=True, variations=True, comments=True)
    return game.accept(exporter)


def parse_pgn_games(
    pgn_text: str,
    target_username: str,
    db_con: psycopg.Connection,
) -> tuple[list[dict], int]:
    """
    Parse multi-game PGN text and extract game data.
    Returns (list of game dicts, count of skipped games).
    """
    games = []
    skipped = 0
    pgn_io = io.StringIO(pgn_text)
    target_lower = target_username.lower()

    while True:
        game = chess.pgn.read_game(pgn_io)
        if game is None:
            break

        headers = dict(game.headers)

        # Skip non-standard variants
        variant = headers.get("Variant", "Standard")
        if variant and variant != "Standard":
            skipped += 1
            continue

        # Get player names
        white_player = headers.get("White", "").lower()
        black_player = headers.get("Black", "").lower()

        # Determine if target user is in this game
        if target_lower == white_player:
            is_white = True
            opponent = headers.get("Black", "Unknown")
        elif target_lower == black_player:
            is_white = False
            opponent = headers.get("White", "Unknown")
        else:
            # User not found in headers, skip this game
            skipped += 1
            continue

        # Get per-game PGN string
        game_pgn = game_to_pgn_string(game)

        # Extract site game ID
        site_header = headers.get("Site", "")
        site_game_id = extract_site_game_id(site_header, game_pgn)

        # Normalize result
        result_header = headers.get("Result", "*")
        result = normalize_result(result_header, is_white)

        # Skip games with unknown result
        if result == "unknown":
            skipped += 1
            continue

        # Determine opening via canonical UCI prefix match
        opening = None
        try:
            uci_plies = game_to_uci_plies(game)
            opening = best_opening_match(db_con, uci_plies)
        except Exception:
            opening = None
        if opening:
            eco, opening_name = opening["eco"], opening["name"]
        else:
            eco, opening_name = opening_from_headers(headers)

        # Extract other fields
        game_data = {
            "site": "lichess",
            "site_game_id": site_game_id,
            "username": target_username,
            "played_at": get_played_at(headers),
            "time_class": classify_time_control(headers),
            "color": "white" if is_white else "black",
            "result": result,
            "eco": eco,
            "opening_name": opening_name,
            "opening_id": (opening["opening_id"] if opening else None),
            "opening_ply_count": (opening["ply_count"] if opening else None),
            "opponent": opponent,
            "white_elo": parse_int_or_none(headers.get("WhiteElo")),
            "black_elo": parse_int_or_none(headers.get("BlackElo")),
            "pgn": game_pgn,
        }

        games.append(game_data)

    return games, skipped
