"""Lichess API fetching and PGN parsing for Openingscope."""

import hashlib
import io
import re
import time
from typing import Optional

import chess.pgn
import httpx

LICHESS_API_BASE = "https://lichess.org/api"


class LichessAPIError(Exception):
    """Custom exception for Lichess API errors."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)


def fetch_lichess_pgn(username: str, max_games: int = 200) -> str:
    """
    Fetch games for a user from Lichess as PGN text.
    Handles rate limiting with Retry-After header.
    """
    url = f"{LICHESS_API_BASE}/games/user/{username}"
    headers = {
        "Accept": "application/x-chess-pgn",
    }
    params = {
        "max": max_games,
        "rated": "true",
        "opening": "true",
    }

    def make_request() -> httpx.Response:
        with httpx.Client(timeout=60.0) as client:
            return client.get(url, headers=headers, params=params)

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

    print(">>>>>>>>>>>>>>>", response.text)

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


def game_to_pgn_string(game: chess.pgn.Game) -> str:
    """Convert a parsed game back to PGN string."""
    exporter = chess.pgn.StringExporter(headers=True, variations=True, comments=True)
    return game.accept(exporter)


def parse_pgn_games(pgn_text: str, target_username: str) -> tuple[list[dict], int]:
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

        # Extract other fields
        game_data = {
            "site": "lichess",
            "site_game_id": site_game_id,
            "username": target_username,
            "played_at": get_played_at(headers),
            "time_class": classify_time_control(headers),
            "color": "white" if is_white else "black",
            "result": result,
            "eco": headers.get("ECO", "UNKNOWN") or "UNKNOWN",
            "opening_name": headers.get("Opening", "Unknown") or "Unknown",
            "opponent": opponent,
            "white_elo": parse_int_or_none(headers.get("WhiteElo")),
            "black_elo": parse_int_or_none(headers.get("BlackElo")),
            "pgn": game_pgn,
        }

        games.append(game_data)

    return games, skipped
