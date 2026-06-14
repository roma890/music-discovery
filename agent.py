#!/usr/bin/env python3
"""Music Discovery Agent — describe any vibe, get a perfect playlist."""

import json
import os
import re
import sys
import textwrap

import anthropic
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic()
MODEL = "claude-opus-4-8"

# ---------------------------------------------------------------------------
# Optional Spotify support
# ---------------------------------------------------------------------------
try:
    import spotipy
    from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth

    SPOTIFY_AVAILABLE = True
except ImportError:
    SPOTIFY_AVAILABLE = False

_spotify_client = None


def get_spotify():
    global _spotify_client
    if _spotify_client is not None:
        return _spotify_client

    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    placeholders = {"your_spotify_client_id", "your_spotify_client_secret", ""}
    if not (SPOTIFY_AVAILABLE and client_id and client_secret
            and client_id not in placeholders and client_secret not in placeholders):
        return None

    redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback")
    try:
        _spotify_client = spotipy.Spotify(
            auth_manager=SpotifyOAuth(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
                scope="playlist-modify-public playlist-modify-private",
            )
        )
        _spotify_client.current_user()
    except Exception:
        _spotify_client = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=client_id, client_secret=client_secret
            )
        )
    return _spotify_client


# ---------------------------------------------------------------------------
# Spotify tool definitions
# ---------------------------------------------------------------------------
SPOTIFY_TOOLS = [
    {
        "name": "search_spotify",
        "description": "Search Spotify for tracks. Returns track id, title, artist, album, year, url, popularity.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_audio_features",
        "description": "Get Spotify audio features (energy, valence, tempo, danceability, acousticness, instrumentalness) for track IDs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "track_ids": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["track_ids"],
        },
    },
    {
        "name": "get_recommendations",
        "description": "Get Spotify recommendations using genre seeds and audio-feature targets.",
        "input_schema": {
            "type": "object",
            "properties": {
                "seed_genres": {"type": "array", "items": {"type": "string"}},
                "target_energy": {"type": "number"},
                "target_valence": {"type": "number"},
                "target_tempo": {"type": "number"},
                "target_danceability": {"type": "number"},
                "target_acousticness": {"type": "number"},
                "target_instrumentalness": {"type": "number"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["seed_genres"],
        },
    },
    {
        "name": "create_playlist",
        "description": "Create a Spotify playlist and add the selected tracks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "track_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["name", "track_ids"],
        },
    },
]


def _run_spotify_tool(name: str, params: dict, sp) -> dict:
    if name == "search_spotify":
        results = sp.search(q=params["query"], type="track", limit=params.get("limit", 10))
        return {
            "tracks": [
                {
                    "id": t["id"],
                    "title": t["name"],
                    "artist": t["artists"][0]["name"],
                    "album": t["album"]["name"],
                    "year": (t["album"]["release_date"] or "")[:4] or None,
                    "url": t["external_urls"]["spotify"],
                    "popularity": t["popularity"],
                }
                for t in results["tracks"]["items"]
            ]
        }

    if name == "get_audio_features":
        feats = sp.audio_features(params["track_ids"])
        return {
            "features": [
                {
                    "id": f["id"],
                    "energy": f["energy"],
                    "valence": f["valence"],
                    "tempo": round(f["tempo"]),
                    "danceability": f["danceability"],
                    "acousticness": f["acousticness"],
                    "instrumentalness": f["instrumentalness"],
                }
                for f in feats
                if f
            ]
        }

    if name == "get_recommendations":
        kwargs = {k: v for k, v in params.items() if k != "seed_genres"}
        results = sp.recommendations(seed_genres=params["seed_genres"][:5], **kwargs)
        return {
            "tracks": [
                {
                    "id": t["id"],
                    "title": t["name"],
                    "artist": t["artists"][0]["name"],
                    "album": t["album"]["name"],
                    "year": (t["album"]["release_date"] or "")[:4] or None,
                    "url": t["external_urls"]["spotify"],
                }
                for t in results["tracks"]
            ]
        }

    if name == "create_playlist":
        user_id = sp.current_user()["id"]
        pl = sp.user_playlist_create(
            user=user_id,
            name=params["name"],
            public=True,
            description=params.get("description", ""),
        )
        sp.playlist_add_items(pl["id"], [f"spotify:track:{tid}" for tid in params["track_ids"]])
        return {"playlist_url": pl["external_urls"]["spotify"], "name": params["name"]}

    return {"error": f"unknown tool: {name}"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _extract_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    match = re.search(r"(\[[\s\S]*\]|\{[\s\S]*\})", text)
    if match:
        text = match.group(1)
    return json.loads(text)


# ---------------------------------------------------------------------------
# Public API — used by both CLI and web app
# ---------------------------------------------------------------------------
def discover_playlist(vibe: str) -> dict:
    """Single call: analyze vibe + recommend 10 songs. Returns full playlist dict."""
    r = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=(
            "You are an expert music curator with encyclopedic knowledge across genres, eras, and global scenes. "
            "Recommend real, specific songs — every pick must feel earned."
        ),
        messages=[
            {
                "role": "user",
                "content": textwrap.dedent(f"""
                    Vibe: "{vibe}"

                    Return a single JSON object:
                    {{
                      "playlist_name": "short evocative title",
                      "vibe_summary": "one sentence capturing the musical essence",
                      "mood": ["adjectives"],
                      "energy": <0.0-1.0>,
                      "genres": ["genre list"],
                      "tracks": [
                        {{
                          "title": "Song Title",
                          "artist": "Artist Name",
                          "album": "Album Name",
                          "year": 2003,
                          "explanation": "2-3 sentences on WHY this fits — specific sonic qualities, mood, how it captures the vibe"
                        }}
                      ]
                    }}

                    Include exactly 10 tracks. Spread across eras: at least 3 tracks from the last 5 years (2021–2025), 3 from the 2010s, and the rest from earlier decades. Mix iconic tracks with deeper cuts. Return only the JSON.
                """).strip(),
            }
        ],
    )
    text = "".join(b.text for b in r.content if hasattr(b, "text"))
    return _extract_json(text)


# ---------------------------------------------------------------------------
# CLI output
# ---------------------------------------------------------------------------
def _hr(char="─", width=62):
    return char * width


def _print_playlist(vibe_data: dict, songs: list) -> None:
    name = vibe_data.get("playlist_name", "Your Playlist")
    summary = vibe_data.get("vibe_summary", "")

    print(_hr("═"))
    print(f"  {name.upper()}")
    print(_hr("═"))
    print(f"\n  {summary}\n")
    print(_hr())

    for i, s in enumerate(songs, 1):
        year_str = f" ({s['year']})" if s.get("year") else ""
        album_str = f"{s.get('album', '')}{year_str}" if s.get("album") else ""
        print(f"\n{i:2}. {s['title']}  —  {s['artist']}")
        if album_str:
            print(f"    {album_str}")
        explanation = textwrap.fill(
            s.get("explanation", ""), width=58,
            initial_indent="    ", subsequent_indent="    "
        )
        print(explanation)

    print(f"\n{_hr()}\n")


def _claude_only(vibe: str) -> None:
    print(f'\n  Vibe: "{vibe}"\n')
    print("  Discovering playlist...")
    result = discover_playlist(vibe)
    print(f"  Essence : {result.get('vibe_summary', '')}")
    print(f"  Genres  : {', '.join(result.get('genres', []))}\n")
    _print_playlist(result, result.get("tracks", []))


def _spotify_mode(vibe: str, sp) -> None:
    print(f'\n  Vibe: "{vibe}"\n')
    print("  Searching Spotify...\n")

    system = textwrap.dedent("""
        You are an expert music curator and discovery agent.

        Workflow:
        1. Analyze the vibe to extract musical DNA.
        2. Call search_spotify with 3-5 varied queries to gather candidates.
        3. Call get_recommendations with genre seeds and audio-feature targets.
        4. Call get_audio_features on promising candidates to verify they match.
        5. Select the 10 best tracks (mix well-known + deeper cuts).
        6. Call create_playlist to save to Spotify.
        7. Present the playlist with a 2-3 sentence explanation per track.
    """).strip()

    messages = [{"role": "user", "content": f'Discover music for this vibe: "{vibe}"'}]

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=8000,
            thinking={"type": "adaptive"},
            system=system,
            tools=SPOTIFY_TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text") and block.text.strip():
                    print(block.text)
            break

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    label = json.dumps(block.input)[:70]
                    print(f"  → {block.name}({label}{'...' if len(json.dumps(block.input)) > 70 else ''})")
                    try:
                        result = _run_spotify_tool(block.name, block.input, sp)
                    except Exception as exc:
                        result = {"error": str(exc)}
                    tool_results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result)}
                    )
            messages.append({"role": "user", "content": tool_results})
        else:
            print(f"Unexpected stop_reason: {response.stop_reason}")
            break


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    if len(sys.argv) > 1:
        vibe = " ".join(sys.argv[1:])
    else:
        print("\nMusic Discovery Agent  •  powered by Claude\n")
        vibe = input("Describe your vibe: ").strip()
        if not vibe:
            sys.exit("No vibe provided.")

    sp = get_spotify()
    if sp:
        print("  Spotify connected — full discovery mode")
        _spotify_mode(vibe, sp)
    else:
        _claude_only(vibe)


if __name__ == "__main__":
    main()
