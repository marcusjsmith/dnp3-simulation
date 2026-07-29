"""Flask web UI and DNP3 master station entry point."""

import os
import signal
import sys

from flask import Flask, jsonify, render_template, request
from waitress import serve

from dnp3_client import (
    MasterState,
    send_close_command,
    send_trip_command,
    start_master,
)
from trace_log import trace_buffer

app = Flask(__name__)
state = MasterState()
master = None
stop_event = None

OUTSTATION_HOST = os.environ.get("OUTSTATION_HOST", "outstation")
OUTSTATION_PORT = int(os.environ.get("OUTSTATION_PORT", "20000"))
WEB_PORT = int(os.environ.get("WEB_PORT", "8080"))


@app.route("/")
def index():
    return render_template(
        "index.html",
        outstation_host=OUTSTATION_HOST,
        outstation_port=OUTSTATION_PORT,
    )


@app.route("/api/status")
def api_status():
    return jsonify(state.snapshot())


@app.route("/api/trace")
def api_trace():
    since = int(request.args.get("since", 0))
    return jsonify({"entries": trace_buffer.since(since), "total": trace_buffer.count()})


@app.route("/api/trace/clear", methods=["POST"])
def api_trace_clear():
    trace_buffer.clear()
    return jsonify({"ok": True})


@app.route("/api/command/trip", methods=["POST"])
def command_trip():
    if not state.dnp3_connected:
        return jsonify({"ok": False, "error": "DNP3 not connected"}), 503
    if not send_trip_command(master, state):
        return jsonify({"ok": False, "error": "Master busy or not connected"}), 503
    return jsonify({"ok": True, "state": state.snapshot()})


@app.route("/api/command/close", methods=["POST"])
def command_close():
    if not state.dnp3_connected:
        return jsonify({"ok": False, "error": "DNP3 not connected"}), 503
    if not send_close_command(master, state):
        return jsonify({"ok": False, "error": "Master busy or not connected"}), 503
    return jsonify({"ok": True, "state": state.snapshot()})


def shutdown_handler(signum, frame):
    global master, stop_event
    if stop_event:
        stop_event.set()
    if master:
        master.shutdown()
    sys.exit(0)


def main():
    global master, stop_event

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    master, stop_event, _ = start_master(state, OUTSTATION_HOST, OUTSTATION_PORT)
    serve(app, host="0.0.0.0", port=WEB_PORT, threads=4)


if __name__ == "__main__":
    main()
