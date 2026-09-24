from pathlib import Path

import yaml

from vi_dubber.translate import LocalTranslator, load_glossary
from vi_dubber.types import Segment


def main() -> None:
    config = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    translator = LocalTranslator(config["translation"], Path("work/smoke-llm"))
    with translator.running() as client:
        segments = [
            Segment(
                id=0,
                start=0.0,
                end=4.2,
                text="Price retraces into the FVG and then sweeps liquidity below the order block.",
            )
        ]
        result = client.translate_segments(segments, load_glossary(Path("glossary.yaml")))[0].vi
        print(result.encode("unicode_escape").decode("ascii"))


if __name__ == "__main__":
    main()
