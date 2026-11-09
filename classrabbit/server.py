"""Run the local API and web interface: python -m classrabbit.server."""

import argparse
import uvicorn

from .api import create_app


def main():
    parser = argparse.ArgumentParser(description="Run the Classrabbit local app and API.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--database", default=".local/app.sqlite3")
    args = parser.parse_args()
    uvicorn.run(create_app(args.database), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
