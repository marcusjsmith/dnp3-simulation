"""Flask web UI and DNP3 outstation entry point."""

import os
import signal
import sys

from flask import Flask, jsonify, render_template
from waitress import serve

from recloser import RecloserState, start_outstation

app = Flask(__name__)
state = RecloserState()
outstation = None
stop_event = None

DNP3_PORT = int(os.environ.get("DNP3_PORT", "20000"))
WEB_PORT = int(os.environ.get("WEB_PORT", "8080"))


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    return jsonify(state.snapshot())


@app.route("/api/local-trip", methods=["POST"])
def local_trip():
    state.trip("Local UI")
    if outstation:
        outstation._mark_dirty()
    return jsonify({"ok": True, "state": state.snapshot()})


@app.route("/api/local-close", methods=["POST"])
def local_close():
    state.close("Local UI")
    if outstation:
        outstation._mark_dirty()
    return jsonify({"ok": True, "state": state.snapshot()})


def shutdown_handler(signum, frame):
    global outstation, stop_event
    if stop_event:
        stop_event.set()
    if outstation:
        outstation.shutdown()
    sys.exit(0)


def main():
    global outstation, stop_event

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    outstation, stop_event, _ = start_outstation(state, port=DNP3_PORT)
    serve(app, host="0.0.0.0", port=WEB_PORT, threads=4)


if __name__ == "__main__":
    main()
