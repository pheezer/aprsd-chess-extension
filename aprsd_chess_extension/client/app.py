"""The web chessboard: a small Flask app over a ClientGameManager."""

from __future__ import annotations

import json
import os
import queue

import flask

from aprsd_chess_extension.client.manager import ClientGameManager

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

KEEPALIVE_SECONDS = 15


def create_app(manager: ClientGameManager, title: str = "APRS Chess") -> flask.Flask:
    app = flask.Flask(
        "aprsd_chess_extension",
        static_folder=os.path.join(HERE, "web", "static"),
        static_url_path="/static",
        template_folder=os.path.join(HERE, "web", "templates"),
    )

    @app.get("/")
    def index():
        return flask.render_template(
            "index.html",
            title=title,
            player=manager.player,
            server=manager.server_callsign,
        )

    @app.get("/api/state")
    def state():
        return flask.jsonify(manager.snapshot())

    @app.get("/events")
    def events():
        subscriber = manager.subscribe()

        def stream():
            try:
                yield f"data: {json.dumps(manager.snapshot())}\n\n"
                while True:
                    try:
                        subscriber.get(timeout=KEEPALIVE_SECONDS)
                    except queue.Empty:
                        yield ": keepalive\n\n"
                        continue
                    # Coalesce bursts of updates into one snapshot.
                    while True:
                        try:
                            subscriber.get_nowait()
                        except queue.Empty:
                            break
                    yield f"data: {json.dumps(manager.snapshot())}\n\n"
            finally:
                manager.unsubscribe(subscriber)

        return flask.Response(
            stream(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    def action(fn):
        try:
            fn(flask.request.get_json(silent=True) or {})
        except ValueError as ex:
            return flask.jsonify({"ok": False, "error": str(ex)}), 400
        return flask.jsonify({"ok": True, "state": manager.snapshot()})

    @app.post("/api/new")
    def new_game():
        return action(
            lambda d: manager.start_game(d.get("mycall", ""), d.get("theircall", ""))
        )

    @app.post("/api/resume")
    def resume():
        return action(lambda d: manager.resume(d.get("code", "")))

    @app.post("/api/move")
    def move():
        return action(lambda d: manager.move(d.get("code", ""), d.get("uci", "")))

    @app.post("/api/resign")
    def resign():
        return action(lambda d: manager.resign(d.get("code", "")))

    @app.post("/api/draw")
    def draw():
        return action(lambda d: manager.draw(d.get("code", "")))

    @app.post("/api/sync")
    def sync():
        return action(lambda d: manager.sync(d.get("code", "")))

    @app.post("/api/retry")
    def retry():
        return action(lambda d: manager.retry(d.get("code", "")))

    @app.post("/api/cancel")
    def cancel():
        return action(lambda d: manager.cancel_pending(d.get("code", "")))

    @app.post("/api/forget")
    def forget():
        return action(lambda d: manager.forget(d.get("code", "")))

    @app.post("/api/list")
    def refresh_list():
        return action(lambda d: manager.refresh_list())

    return app
