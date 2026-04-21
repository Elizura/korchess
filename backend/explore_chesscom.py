
import re
import time
from typing import Optional, Iterator

import httpx

CHESSCOM_API_BASE = "https://api.chess.com/pub"
USER_AGENT = "Korchess/1.0 (Chess opening analyzer)"


class ChesscomAPIError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)


def _get_follow_redirect(client: httpx.Client, url: str, headers: dict) -> httpx.Response:
    response = client.get(url, headers=headers)
    if response.status_code == 301:
        location = response.headers.get("Location")
        if location:
            response = client.get(location, headers=headers)
    return response


def _fetch_archive_urls(client: httpx.Client, username: str, headers: dict) -> list[str]:
    """Fetch the list of monthly archive URLs for a user, newest first."""
    archives_url = f"{CHESSCOM_API_BASE}/player/{username}/games/archives"
    response = _get_follow_redirect(client, archives_url, headers)

    if response.status_code == 404:
        raise ChesscomAPIError(
            f"User '{username}' not found on Chess.com.",
            status_code=404,
        )
    if response.status_code != 200:
        raise ChesscomAPIError(
            f"Chess.com API error: {response.status_code}",
            status_code=response.status_code,
        )

    archive_urls = response.json().get("archives", [])
    archive_urls.reverse()
    return archive_urls


def _fetch_archive_pgns(client: httpx.Client, archive_url: str, headers: dict, max_retries: int = 3) -> list[str]:
    """Fetch PGN strings for every game in one monthly archive, with retry on 429."""
    wait_time = 2
    for attempt in range(max_retries):
        response = _get_follow_redirect(client, archive_url, headers)

        if response.status_code == 429:
            if attempt < max_retries - 1:
                print(f"[Chess.com] Rate limited, waiting {wait_time}s...")
                time.sleep(wait_time)
                wait_time *= 2
                continue
            raise ChesscomAPIError(
                "Rate limited by Chess.com. Please try again later.",
                status_code=429,
            )

        if response.status_code != 200:
            print(f"[Chess.com] Archive fetch failed: {response.status_code}")
            return []

        games = response.json().get("games", [])
        pgns = []
        for g in games:
            if g.get("rules") != "chess":
                continue
            pgn = g.get("pgn")
            if pgn:
                pgns.append(pgn)
        return pgns

    return []


def fetch_chesscom_pgn_stream(username: str, games_per_chunk: int = 5, max_games: int = 500) -> Iterator[str]:
    """
    Stream PGN text from Chess.com in chunks, walking through monthly
    archives from newest to oldest — mirrors explore.py's Lichess stream.
    """
    start_time = time.time()
    headers = {"User-Agent": USER_AGENT}

    with httpx.Client(timeout=30.0) as client:
        archive_urls = _fetch_archive_urls(client, username, headers)

        if not archive_urls:
            return

        games_buffer: list[str] = []
        total_game_count = 0
        chunk_num = 0

        for archive_url in archive_urls:
            if total_game_count >= max_games:
                break

            time.sleep(1)

            pgns = _fetch_archive_pgns(client, archive_url, headers)

            for pgn in pgns:
                if total_game_count >= max_games:
                    break

                games_buffer.append(pgn.rstrip() + "\n\n\n")

                if len(games_buffer) == games_per_chunk:
                    combined = "".join(games_buffer)
                    total_game_count += games_per_chunk
                    chunk_num += 1
                    print(f"Chunk {chunk_num}: {games_per_chunk} games ({len(combined)} bytes)")
                    yield combined
                    games_buffer = []

        if games_buffer:
            combined = "".join(games_buffer)
            count = len(games_buffer)
            total_game_count += count
            chunk_num += 1
            print(f"Chunk {chunk_num} (final): {count} games ({len(combined)} bytes)")
            yield combined

    elapsed = time.time() - start_time
    print(f"Fetched {total_game_count} games in {elapsed:.2f} seconds")


def fetch_chesscom_pgn(username: str, max_games: int = 50) -> str:
    """Non-streaming variant — collect all PGN text at once."""
    start_time = time.time()
    headers = {"User-Agent": USER_AGENT}

    with httpx.Client(timeout=30.0) as client:
        archive_urls = _fetch_archive_urls(client, username, headers)
        all_pgns: list[str] = []

        for archive_url in archive_urls:
            if len(all_pgns) >= max_games:
                break

            time.sleep(1)
            pgns = _fetch_archive_pgns(client, archive_url, headers)
            for pgn in pgns:
                if len(all_pgns) >= max_games:
                    break
                all_pgns.append(pgn.rstrip() + "\n\n\n")

    full_text = "".join(all_pgns)
    elapsed = time.time() - start_time
    print(f"Fetched {len(all_pgns)} games in {elapsed:.2f} seconds")
    return full_text


if __name__ == "__main__":
    print("Streaming version:")
    for chunk in fetch_chesscom_pgn_stream("hikaru"):
        print(f"Received chunk of {len(chunk)} bytes")
        print(chunk[:500], "..." if len(chunk) > 500 else "")
