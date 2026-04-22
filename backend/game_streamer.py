"""Production game streamers for Lichess and Chess.com.

Yields individual PGN strings in chunks, designed to feed into the Celery
task queue. Adapted from explore.py (Lichess) and explore_chesscom.py (Chess.com).
"""

import logging
import re
import time
from datetime import datetime
from typing import Iterator, Optional

import httpx

logger = logging.getLogger(__name__)

LICHESS_API_BASE = "https://lichess.org/api"
CHESSCOM_API_BASE = "https://api.chess.com/pub"
CHESSCOM_USER_AGENT = "Korchess/1.0 (Chess opening analyzer)"

GAMES_PER_CHUNK = 5


class LichessStreamError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)


class ChesscomStreamError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)


def stream_lichess_pgns(
    username: str,
    max_games: int = 250,
    since: int | None = None,
    games_per_chunk: int = GAMES_PER_CHUNK,
) -> Iterator[list[str]]:
    """Stream PGN strings from Lichess using HTTP response streaming.

    Yields lists of individual PGN strings (one per game), chunked for
    batch efficiency. Each list contains up to ``games_per_chunk`` items.

    Args:
        username: Lichess username.
        max_games: Maximum number of games to fetch.
        since: Milliseconds since epoch -- only fetch games after this.
        games_per_chunk: How many PGNs to buffer before yielding.
    """
    url = f"{LICHESS_API_BASE}/games/user/{username}"
    headers = {"Accept": "application/x-chess-pgn", "Authorization": "Bearer ***REMOVED***"}
    params: dict = {
        "rated": "true",
        "opening": "true",
        "clocks": "true",
        "max": max_games,
    }
    if since is not None:
        params["since"] = since

    with httpx.Client(timeout=60.0) as client:
        with client.stream("GET", url, headers=headers, params=params) as response:
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "60")
                try:
                    wait_seconds = int(retry_after)
                except ValueError:
                    wait_seconds = 60
                wait_seconds = min(wait_seconds, 120)
                time.sleep(wait_seconds)

                with client.stream("GET", url, headers=headers, params=params) as retry_resp:
                    if retry_resp.status_code == 429:
                        raise LichessStreamError(
                            "Rate limited by Lichess. Please try again later.",
                            status_code=429,
                        )
                    yield from _iter_lichess_response(retry_resp, username, games_per_chunk)
                    return

            if response.status_code == 404:
                raise LichessStreamError(
                    f"User '{username}' not found on Lichess.",
                    status_code=404,
                )

            if response.status_code != 200:
                raise LichessStreamError(
                    f"Lichess API error: {response.status_code}",
                    status_code=response.status_code,
                )

            yield from _iter_lichess_response(response, username, games_per_chunk)


def _iter_lichess_response(
    response: httpx.Response,
    username: str,
    games_per_chunk: int,
) -> Iterator[list[str]]:
    """Walk a streaming Lichess PGN response and yield chunks of PGN strings."""
    buffer = ""
    games_buffer: list[str] = []
    total = 0

    for text_chunk in response.iter_text():
        buffer += text_chunk

        while "\n\n\n" in buffer:
            game_pgn, buffer = buffer.split("\n\n\n", 1)
            game_pgn = game_pgn.strip()
            if game_pgn:
                games_buffer.append(game_pgn)

            if len(games_buffer) == games_per_chunk:
                total += len(games_buffer)
                logger.info("[Lichess] Yielding chunk of %d games (total: %d)", len(games_buffer), total)
                yield list(games_buffer)
                games_buffer = []

    if buffer.strip():
        games_buffer.append(buffer.strip())

    if games_buffer:
        total += len(games_buffer)
        logger.info("[Lichess] Yielding final chunk of %d games (total: %d)", len(games_buffer), total)
        yield list(games_buffer)

    logger.info("[Lichess] Finished streaming %d games for %s", total, username)


# ---------------------------------------------------------------------------
# Chess.com streamer
# ---------------------------------------------------------------------------

def _archive_url_year_month(archive_url: str) -> tuple[int, int] | None:
    """Extract (year, month) from a Chess.com archive URL like .../games/2024/03."""
    match = re.search(r"/games/(\d{4})/(\d{2})$", archive_url)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None


def _get_follow_redirect(client: httpx.Client, url: str, headers: dict) -> httpx.Response:
    response = client.get(url, headers=headers)
    if response.status_code == 301:
        location = response.headers.get("Location")
        if location:
            response = client.get(location, headers=headers)
    return response


def _fetch_archive_urls(client: httpx.Client, username: str, headers: dict) -> list[str]:
    """Fetch monthly archive URLs for a user, newest first."""
    archives_url = f"{CHESSCOM_API_BASE}/player/{username}/games/archives"
    response = _get_follow_redirect(client, archives_url, headers)

    if response.status_code == 404:
        raise ChesscomStreamError(
            f"User '{username}' not found on Chess.com.",
            status_code=404,
        )
    if response.status_code != 200:
        raise ChesscomStreamError(
            f"Chess.com API error: {response.status_code}",
            status_code=response.status_code,
        )

    archive_urls = response.json().get("archives", [])
    archive_urls.reverse()
    return archive_urls


def _fetch_archive_games(
    client: httpx.Client,
    archive_url: str,
    headers: dict,
    max_retries: int = 3,
) -> list[dict]:
    """Fetch game JSON objects for every standard-chess game in one monthly archive."""
    wait_time = 2
    for attempt in range(max_retries):
        response = _get_follow_redirect(client, archive_url, headers)

        if response.status_code == 429:
            if attempt < max_retries - 1:
                logger.warning("[Chess.com] Rate limited, waiting %ds...", wait_time)
                time.sleep(wait_time)
                wait_time *= 2
                continue
            raise ChesscomStreamError(
                "Rate limited by Chess.com. Please try again later.",
                status_code=429,
            )

        if response.status_code != 200:
            logger.warning("[Chess.com] Archive fetch failed: %d", response.status_code)
            return []

        games = response.json().get("games", [])
        return [g for g in games if g.get("rules") == "chess"]

    return []


def stream_chesscom_games(
    username: str,
    max_games: int = 250,
    since: datetime | None = None,
    games_per_chunk: int = GAMES_PER_CHUNK,
) -> Iterator[list[dict]]:
    """Stream game JSON dicts from Chess.com by walking monthly archives.

    Yields lists of Chess.com game JSON objects (one per game), chunked for
    batch efficiency. Each list contains up to ``games_per_chunk`` items.
    The full JSON is needed because ``parse_chesscom_game`` requires metadata
    (player info, ratings, time class, result) beyond what the PGN headers carry.

    Args:
        username: Chess.com username.
        max_games: Maximum number of games to fetch.
        since: Only fetch archives from this month onwards.
        games_per_chunk: How many games to buffer before yielding.
    """
    headers = {"User-Agent": CHESSCOM_USER_AGENT}

    since_ym: tuple[int, int] | None = None
    if since is not None:
        since_ym = (since.year, since.month)

    with httpx.Client(timeout=30.0) as client:
        archive_urls = _fetch_archive_urls(client, username, headers)

        if not archive_urls:
            return

        games_buffer: list[dict] = []
        total = 0

        for archive_url in archive_urls:
            if total >= max_games:
                break

            if since_ym is not None:
                ym = _archive_url_year_month(archive_url)
                if ym is not None and ym < since_ym:
                    break

            time.sleep(1)

            game_jsons = _fetch_archive_games(client, archive_url, headers)

            for game_json in game_jsons:
                if total + len(games_buffer) >= max_games:
                    break

                games_buffer.append(game_json)

                if len(games_buffer) == games_per_chunk:
                    total += len(games_buffer)
                    logger.info("[Chess.com] Yielding chunk of %d games (total: %d)", len(games_buffer), total)
                    yield list(games_buffer)
                    games_buffer = []

        if games_buffer:
            total += len(games_buffer)
            logger.info("[Chess.com] Yielding final chunk of %d games (total: %d)", len(games_buffer), total)
            yield list(games_buffer)

        logger.info("[Chess.com] Finished streaming %d games for %s", total, username)
