"""Run the local API and web interface: python -m classrabbit.server."""

import argparse
import uvicorn

from .api import create_app


def main():
    parser = argparse.ArgumentParser(description="Run the Classrabbit local app and API.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--database")
    parser.add_argument("--source", choices=["public", "myucla"], default="public")
    args = parser.parse_args()
    source = None
    if args.source == "myucla":
        from .myucla import MyUCLASource
        source = MyUCLASource()
    database = args.database or (".local/myucla-app.sqlite3" if source else ".local/app.sqlite3")
    try:
        uvicorn.run(create_app(database, source), host="127.0.0.1", port=args.port)
    finally:
        if source:
            source.close()


if __name__ == "__main__":
    main()
