# server/app/app.py
"""Flask application factory. Routes are added in later tasks."""
from flask import Flask


def create_app(config):
    app = Flask(__name__)
    return app
