"""Chess.com API fetching and game parsing for Korchess."""

import io
import re
import time
from datetime import datetime, timezone
from typing import Optional

import chess.pgn
import httpx
import psycopg

from opening_match import game_to_uci_plies, best_opening_match

CHESSCOM_API_BASE = "https://api.chess.com/pub"
USER_AGENT = "Korchess/1.0 (Chess opening analyzer)"


def _get_follow_redirect(client: httpx.Client, url: str, headers: dict) -> httpx.Response:
    """GET url; if 301, follow Location header and return that response."""
    response = client.get(url, headers=headers)
    if response.status_code == 301:
        location = response.headers.get("Location")
        if location:
            response = client.get(location, headers=headers)
    return response


class ChesscomAPIError(Exception):
    """Custom exception for Chess.com API errors."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)


def fetch_chesscom_games(username: str, max_games: int = 200, conn: psycopg.Connection | None = None) -> list[dict]:

    headers = {
        "User-Agent": USER_AGENT,
    }

    # 1. Get list of monthly archives
    archives_url = f"{CHESSCOM_API_BASE}/player/{username}/games/archives"
    
    try:
        with httpx.Client(timeout=30.0) as client:
            response = _get_follow_redirect(client, archives_url, headers)
    except httpx.RequestError as e:
        raise ChesscomAPIError(f"Network error: {str(e)}")

    if response.status_code == 404:
        raise ChesscomAPIError(
            f"User '{username}' not found on Chess.com.",
            status_code=404
        )
    
    if response.status_code != 200:
        raise ChesscomAPIError(
            f"Chess.com API error: {response.status_code}",
            status_code=response.status_code
        )

    try:
        archives_data = response.json()
        archive_urls = archives_data.get("archives", [])
    except Exception as e:
        raise ChesscomAPIError(f"Failed to parse archives response: {str(e)}")

    if not archive_urls:
        return []

    # 2. Fetch games from archives (newest first)
    archive_urls.reverse()  # Newest archives first
    
    all_games = []
    target_lower = username.strip().lower()
    
    for archive_url in archive_urls:
        if len(all_games) >= max_games:
            break
        
        # Add delay between requests to respect rate limits
        time.sleep(1)
        
        # Fetch games from this archive with retry logic
        games = fetch_archive_with_retry(archive_url, headers)
        
        # Parse each game
        for game_json in games:
            if len(all_games) >= max_games:
                break
            
            # Filter out variants - only keep standard chess
            if game_json.get("rules") != "chess":
                continue
            
            # Parse game
            game_data = parse_chesscom_game(game_json, target_lower)
            if game_data:
                opening = None
                if conn:
                    try:
                        pgn_text = game_data.get("pgn", "")
                        if pgn_text:
                            game_obj = chess.pgn.read_game(io.StringIO(pgn_text))
                            if game_obj:
                                uci_plies = game_to_uci_plies(game_obj, max_plies=40)
                                opening = best_opening_match(conn, uci_plies)
                    except Exception:
                        opening = None

                if opening:
                    game_data["eco"] = opening["eco"]
                    game_data["opening_name"] = opening["name"]
                    game_data["opening_id"] = opening["opening_id"]
                    game_data["opening_ply_count"] = opening["ply_count"]
                else:
                    game_data["opening_id"] = None
                    game_data["opening_ply_count"] = None

                all_games.append(game_data)

    return all_games


def fetch_archive_with_retry(archive_url: str, headers: dict, max_retries: int = 3) -> list[dict]:
    """
    Fetch a single monthly archive with exponential backoff on 429.
    """
    wait_time = 2  # Start with 2 seconds
    
    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=30.0) as client:
                response = _get_follow_redirect(client, archive_url, headers)

            if response.status_code == 429:
                if attempt < max_retries - 1:
                    print(f"[Chess.com] Rate limited, waiting {wait_time}s...")
                    time.sleep(wait_time)
                    wait_time *= 2  # Exponential backoff
                    continue
                else:
                    raise ChesscomAPIError(
                        "Rate limited by Chess.com. Please try again later.",
                        status_code=429
                    )
            
            if response.status_code != 200:
                print(f"[Chess.com] Warning: Archive fetch failed with status {response.status_code}")
                return []
            
            archive_data = response.json()
            return archive_data.get("games", [])
            
        except httpx.RequestError as e:
            if attempt < max_retries - 1:
                time.sleep(wait_time)
                wait_time *= 2
                continue
            print(f"[Chess.com] Network error fetching archive: {str(e)}")
            return []
    
    return []


def parse_chesscom_game(game_json: dict, target_username: str) -> Optional[dict]:
    """
    Parse a single Chess.com game JSON into game_data dict.
    Returns None if game should be skipped.
    """
    try:
        # Extract basic info
        white_username = game_json.get("white", {}).get("username", "").lower()
        black_username = game_json.get("black", {}).get("username", "").lower()
        
        # Determine user's color
        if target_username == white_username:
            color = "white"
            opponent = game_json.get("black", {}).get("username", "Unknown")
        elif target_username == black_username:
            color = "black"
            opponent = game_json.get("white", {}).get("username", "Unknown")
        else:
            # User not in this game
            return None
        
        # Extract game ID from URL
        game_url = game_json.get("url", "")
        site_game_id = extract_game_id(game_url)
        
        # Get timestamp
        end_time = game_json.get("end_time")
        if end_time:
            played_at = datetime.fromtimestamp(end_time, tz=timezone.utc).isoformat()
        else:
            played_at = None
        
        # Map time class
        time_class = map_time_class(game_json.get("time_class", ""))
        
        # Map result
        result = map_result(game_json, target_username, color)
        if result == "unknown":
            return None
        
        # Extract ECO and opening name from PGN if available
        pgn_text = game_json.get("pgn", "")
        eco, opening_name = extract_opening_from_pgn(pgn_text)
        
        # Get ratings
        white_elo = game_json.get("white", {}).get("rating")
        black_elo = game_json.get("black", {}).get("rating")
        
        return {
            "site": "chesscom",
            "site_game_id": site_game_id,
            "username": target_username,
            "played_at": played_at,
            "time_class": time_class,
            "color": color,
            "result": result,
            "eco": eco,
            "opening_name": opening_name,
            "opponent": opponent,
            "white_elo": white_elo,
            "black_elo": black_elo,
            "pgn": pgn_text,
        }
    
    except Exception as e:
        print(f"[Chess.com] Warning: Failed to parse game: {str(e)}")
        return None


def extract_game_id(url: str) -> str:
    """
    Extract game ID from Chess.com game URL.
    Example: https://www.chess.com/game/live/123456789 -> 123456789
    """
    if not url:
        return "unknown"
    
    # Try to extract numeric ID from URL
    match = re.search(r'/game/(?:live|daily)/(\d+)', url)
    if match:
        return match.group(1)
    
    # Fallback: use last part of URL
    parts = url.rstrip('/').split('/')
    if parts:
        return parts[-1]
    
    return "unknown"


def map_time_class(time_class: str) -> str:
    """
    Map Chess.com time_class to our standard values.
    """
    time_class = time_class.lower()
    
    # Chess.com uses: bullet, blitz, rapid, daily
    if time_class in ("bullet", "blitz", "rapid"):
        return time_class
    elif time_class == "daily":
        # Map daily correspondence to classical
        return "classical"
    else:
        return "unknown"


def map_result(game_json: dict, target_username: str, color: str) -> str:
    """
    Map Chess.com result codes to win/draw/loss.
    Chess.com stores results in white.result and black.result fields.
    """
    # Get the user's result field
    if color == "white":
        user_result = game_json.get("white", {}).get("result", "")
    else:
        user_result = game_json.get("black", {}).get("result", "")
    
    user_result = user_result.lower()
    
    # Win conditions
    if user_result in ("win", "checkmated", "resigned", "timeout", "abandoned", "bughousepartnerlose"):
        # Need to check if they won or their opponent had this result
        # If user's result is "win", they won
        if user_result == "win":
            return "win"
        # If opponent was checkmated/resigned/timeout, user won
        # This is stored in the user's result field as their opponent's loss reason
        # Actually, Chess.com structure is: winner's field says "win", loser's says loss reason
        # So if user_result is a loss reason, they lost
        return "loss"
    
    # Draw conditions
    if user_result in ("agreed", "stalemate", "repetition", "insufficient", 
                       "50move", "timevsinsufficient", "agreed"):
        return "draw"
    
    # If result field explicitly says these, it's a loss
    if user_result in ("lose", "checkmated", "resigned", "timeout"):
        return "loss"
    
    # Unknown result
    return "unknown"


# Regex: move number + one or three dots + optional space, only when followed by SAN-like (letter).
# Used to find where move notation starts so we can strip it (avoids cutting "version 2.0").
_MOVE_NOTATION_START = re.compile(r'\s+\d+\.(?:\.\.)?\s*')


def strip_move_notation_suffix(opening_name: str) -> str:
    """
    Remove any trailing move-notation suffix from an opening name.
    Chess.com often appends moves like "3...Cxd5 4.Nf3". We cut at the first
    move token (digit(s) + dot/dots + SAN-ish) and return the prefix, trimmed.
    """
    if not opening_name or opening_name == "Unknown":
        return opening_name
    match = _MOVE_NOTATION_START.search(opening_name)
    if match:
        return opening_name[: match.start()].strip()
    return opening_name


def extract_opening_from_pgn(pgn_text: str) -> tuple[str, str]:
    """
    Extract ECO code and opening name from PGN headers.
    Returns (eco, opening_name) tuple.
    
    Chess.com PGN headers may contain:
    - ECO: "A40" (standard ECO code)
    - ECOUrl: "https://www.chess.com/openings/Englund-Gambit-2-Dxe5-Nc6-3-Nf3-Qe7"
    """
    if not pgn_text:
        return "UNKNOWN", "Unknown"
    
    try:
        pgn_io = io.StringIO(pgn_text)
        game = chess.pgn.read_game(pgn_io)
        
        if game is None:
            return "UNKNOWN", "Unknown"
        
        headers = dict(game.headers)
        
        eco = headers.get("ECO", "UNKNOWN") or "UNKNOWN"
        
        # Validate ECO code format (letter + 2 digits)
        if eco != "UNKNOWN" and not (len(eco) >= 2 and eco[0].isalpha() and eco[1:3].isdigit()):
            eco = "UNKNOWN"
        
        # Get opening name
        opening_name = "Unknown"
        
        # Try ECOUrl first - Chess.com stores full URL here
        eco_url = headers.get("ECOUrl", "")
        if eco_url:
            # Extract the path part after /openings/
            # e.g., "https://www.chess.com/openings/Englund-Gambit-2-Dxe5-Nc6-3-Nf3-Qe7"
            # -> "Englund Gambit 2 Dxe5 Nc6 3 Nf3 Qe7"
            if "/openings/" in eco_url:
                opening_path = eco_url.split("/openings/")[-1]
                # Replace dashes with spaces and clean up
                opening_name = opening_path.replace("-", " ")
                # Title case but preserve move notation
                words = opening_name.split()
                formatted_words = []
                for word in words:
                    # Keep chess moves as-is (like Dxe5, Nc6, Nf3, Qe7, etc.)
                    if len(word) <= 4 and any(c.isdigit() for c in word):
                        formatted_words.append(word)
                    else:
                        formatted_words.append(word.title())
                opening_name = " ".join(formatted_words)
            else:
                # ECOUrl might just be the opening name without full URL
                opening_name = eco_url.replace("-", " ").title()
        
        # Fallback to Opening header if ECOUrl didn't work
        if opening_name == "Unknown":
            opening_name = headers.get("Opening", "Unknown") or "Unknown"
        
        # Normalize: strip trailing move notation (e.g. "3...Cxd5 4.Nf3") so names align with Lichess
        opening_name = strip_move_notation_suffix(opening_name)
        
        return eco, opening_name
    
    except Exception:
        return "UNKNOWN", "Unknown"
