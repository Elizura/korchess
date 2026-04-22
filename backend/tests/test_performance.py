
import time
from repository.db import get_connection
from services.opening_match import best_opening_match



def test_best_opening_match(game_uci: list[str]):
    conn = get_connection()
    try:
        start = time.perf_counter()
        best_opening_match(conn, game_uci)
        end = time.perf_counter()
        elapsed_time = end - start
        print(f"Best opening match took {elapsed_time} seconds for {len(game_uci)} plies")
    finally:
        conn.close()

def get_sample_game_uci():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT pgn FROM games WHERE username = 'elizura' AND site = 'lichess' LIMIT 1")
        return cur.fetchone()[0]
    finally:
        conn.close()


