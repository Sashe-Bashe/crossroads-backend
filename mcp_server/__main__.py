"""Run a local stdio server; no HTTP listener or shared remote credential."""

import argparse
import logging
import sys

from .client import Marketplace
from .config import Configuration, ConfigurationError
from .server import create_server


def main():
    parser = argparse.ArgumentParser(description="Neopact workspace MCP server (local stdio)")
    parser.add_argument("--transport", choices=["stdio"], default="stdio")
    parser.parse_args()
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    try:
        config = Configuration.from_environment()
    except ConfigurationError as exc:
        parser.error(str(exc))
    create_server(Marketplace(config)).run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
