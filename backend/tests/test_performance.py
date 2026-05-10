
import time
from repository.db_connection import get_connection
from services.opening_match import best_opening_match



def test_best_opening_match(game_uci: list[str]):
    with get_connection() as conn:
        start = time.perf_counter()
        best_opening_match(conn, game_uci)
        end = time.perf_counter()
        elapsed_time = end - start
        print(f"Best opening match took {elapsed_time} seconds for {len(game_uci)} plies")

def get_sample_game_uci():
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT pgn FROM games WHERE username = 'elizura' AND site = 'lichess' LIMIT 1")
        return cur.fetchone()[0]


