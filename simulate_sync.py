#!/usr/bin/env python3

import json
import argparse
from pathlib import Path

ENTITY_CLASSES = ("high", "medium", "low")

def build_entities(count: int, mix: dict[str, float]) -> list[str]:
    classes: list[str] = []
    remaining = count
    for entity_class in ENTITY_CLASSES[:-1]:
        n = int(round(count * mix[entity_class]))
        classes.extend([entity_class] * n)
        remaining -= n
    classes.extend(["low"] * remaining)
    return classes[:count]

def read_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    args = parser.parse_args()

    config = read_config(args.config)

    # Build entities based on the configuration
    entity_classes = build_entities(int(config["entity_count"]), config["entity_class_mix"])
    print("Built Entities:")
    for entity in entity_classes:
        print(f"  - {entity}")

if __name__ == "__main__":
    main()
