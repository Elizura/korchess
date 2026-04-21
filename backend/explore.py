
import time
from typing import Optional, Iterator

import httpx

LICHESS_API_BASE = "https://lichess.org/api"

class LichessAPIError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)


def fetch_lichess_pgn_stream(username: str, games_per_chunk: int = 5) -> Iterator[str]:
    start_time = time.time()
    
    url = f"{LICHESS_API_BASE}/games/user/{username}"
    headers = {
        "Accept": "application/x-chess-pgn",
        "Authorization": "Bearer ***REMOVED***",
    }
    params: dict = {
        "rated": "true",
        "opening": "true",
        "clocks": "true",
        "max": 500,
    }

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

                with client.stream("GET", url, headers=headers, params=params) as retry_response:
                    if retry_response.status_code == 429:
                        raise LichessAPIError(
                            "Rate limited by Lichess. Please try again later.",
                            status_code=429
                        )
                    response = retry_response

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

            buffer = ""
            games_buffer = []
            total_game_count = 0
            chunk_num = 0
            
            for chunk in response.iter_text():
                buffer += chunk
                
                while "\n\n\n" in buffer:
                    game, buffer = buffer.split("\n\n\n", 1)
                    games_buffer.append(game + "\n\n\n")
                    
                    if len(games_buffer) == games_per_chunk:
                        combined = "".join(games_buffer)
                        total_game_count += games_per_chunk
                        chunk_num += 1
                        print(f"Chunk {chunk_num}: {games_per_chunk} games ({len(combined)} bytes)")
                        yield combined
                        games_buffer = []
            
            if buffer.strip():
                games_buffer.append(buffer)
            
            if games_buffer:
                combined = "".join(games_buffer)
                game_count_in_final = len(games_buffer)
                total_game_count += game_count_in_final
                chunk_num += 1
                print(f"Chunk {chunk_num} (final): {game_count_in_final} games ({len(combined)} bytes)")
                yield combined

    elapsed_time = time.time() - start_time
    print(f"Fetched {total_game_count} games in {elapsed_time:.2f} seconds")


def fetch_lichess_pgn(username: str) -> str:
    start_time = time.time()
    
    url = f"{LICHESS_API_BASE}/games/user/{username}"
    headers = {
        "Accept": "application/x-chess-pgn",
        "Authorization": "Bearer ***REMOVED***",
    }
    params: dict = {
        "rated": "true",
        "opening": "true",
        "clocks": "true",
        "max": 500,
    }

    def make_request() -> httpx.Response:
        with httpx.Client(timeout=60.0) as client:
            return client.get(url, headers=headers, params=params)

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

    elapsed_time = time.time() - start_time
    game_count = response.text.count('[Event "')
    print(f"Fetched {game_count} games in {elapsed_time:.2f} seconds")

    return response.text


if __name__ == "__main__":
    print("Streaming version:")
    for chunk in fetch_lichess_pgn_stream("Marsalseny"):
        print(f"Received chunk of {len(chunk)} bytes")
        print(chunk)
    
    # print("\nNon-streaming version:")
    # pgn_text = fetch_lichess_pgn("elizura")
    # print(f"Total length: {len(pgn_text)} bytes")