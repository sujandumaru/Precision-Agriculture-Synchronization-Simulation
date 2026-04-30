#!/usr/bin/env python3

import json
import argparse
from pathlib import Path

def read_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    args = parser.parse_args()

    config = read_config(args.config)
    print("Simulation Configuration:")
    print(json.dumps(config, indent=4))

if __name__ == "__main__":
    main()