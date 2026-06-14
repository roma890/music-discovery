"""Flask web server for the Music Discovery Agent."""

import json
from flask import Flask, render_template, request, Response, stream_with_context
from agent import discover_playlist

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/discover", methods=["POST"])
def discover():
    vibe = (request.json or {}).get("vibe", "").strip()
    if not vibe:
        return {"error": "No vibe provided."}, 400

    def generate():
        try:
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
