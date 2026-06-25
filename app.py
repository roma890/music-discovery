"""Flask web server for the Music Discovery Agent."""

import json
import os
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from flask import Flask, render_template, request, Response, stream_with_context, redirect, session
from agent import discover_playlist, discover_playlist_spotify, get_spotify, save_to_spotify

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-music-discovery")

SPOTIFY_SCOPE    = "playlist-modify-public playlist-modify-private"
SPOTIFY_REDIRECT = "http://127.0.0.1:5001/spotify/callback"


def _spotify_oauth():
    return SpotifyOAuth(
        client_id=os.getenv("SPOTIFY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
        redirect_uri=SPOTIFY_REDIRECT,
        scope=SPOTIFY_SCOPE,
        cache_handler=spotipy.cache_handler.MemoryCacheHandler(),
        open_browser=False,
    )


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/spotify/login")
def spotify_login():
    auth_url = _spotify_oauth().get_authorize_url()
    return redirect(auth_url)


@app.route("/spotify/callback")
def spotify_callback():
    error = request.args.get("error")
    code  = request.args.get("code")
    if error or not code:
        return f"Spotify error: {error or 'no code returned'} — go back and try again", 400
    token_info = _spotify_oauth().get_access_token(code, as_dict=True)
    session["spotify_token"] = token_info
    return redirect("/?spotify_ready=true")


@app.route("/spotify/logout")
def spotify_logout():
    session.pop("spotify_token", None)
    return redirect("/")


@app.route("/store-pending", methods=["POST"])
def store_pending():
    session["pending_playlist"] = request.json or {}
    return {"ok": True}


@app.route("/save-playlist", methods=["POST"])
def save_playlist():
    token_info = session.get("spotify_token")
    if not token_info:
        return {"error": "Not authenticated"}, 401

    oauth = _spotify_oauth()
    if oauth.is_token_expired(token_info):
        token_info = oauth.refresh_access_token(token_info["refresh_token"])
        session["spotify_token"] = token_info

    sp_user = spotipy.Spotify(auth=token_info["access_token"])

    data = request.json or {}
    if not data.get("tracks"):
        data = session.get("pending_playlist", {})

    try:
        url = save_to_spotify(
            sp_user,
            data.get("tracks", []),
            data.get("playlist_name", "My Playlist"),
            data.get("vibe_summary", ""),
        )
        session.pop("pending_playlist", None)  # only clear on success
        return {"url": url}
    except Exception as exc:
        return {"error": str(exc)}, 500


@app.route("/discover", methods=["POST"])
def discover():
    vibe = (request.json or {}).get("vibe", "").strip()
    if not vibe:
        return {"error": "No vibe provided."}, 400

    def generate():
        try:
            sp = get_spotify()
            if sp:
                yield _event({"type": "status", "msg": "Analyzing cultural context..."})
                for event in discover_playlist_spotify(vibe, sp):
                    yield _event(event)
            else:
                yield _event({"type": "status", "msg": "Discovering your playlist..."})
                result = discover_playlist(vibe)
                yield _event({
                    "type": "done",
                    "playlist_name": result.get("playlist_name", "Your Playlist"),
                    "vibe_summary": result.get("vibe_summary", ""),
                    "genres": result.get("genres", []),
                    "mood": result.get("mood", []),
                    "tracks": result.get("tracks", []),
                })
        except Exception as exc:
            yield _event({"type": "error", "msg": str(exc)})

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _event(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


if __name__ == "__main__":
    app.run(debug=True, port=5001)
