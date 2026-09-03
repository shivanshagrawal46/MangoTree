"""Re-read only the forbidden-engine and needs_human pages."""
from mangotree.config.settings import SETTINGS
from mangotree.extract.runner import ExtractionRunner
from mangotree.storage.mongo import get_mongo


def main() -> None:
    mongo = get_mongo()
    mongo.ping()
    runner = ExtractionRunner(
        mongo,
        api_key=SETTINGS.anthropic_api_key,
        openai_api_key=SETTINGS.openai_api_key,
    )
    summary = runner.reocr_failed_pages(
        include_blocked=False,          # already recovered by GPT-5; don't re-bill
        include_low_confidence=False,   # handled via needs_human below
        include_forbidden_engine=True,
        confidence_floor=0.5,
    )
    print("\n=== RE-OCR SUMMARY ===")
    for k, v in summary.items():
        print(f"  {k:24s} {v}")


if __name__ == "__main__":
    main()
