"""Phase 2: merge independent SME reviews into the human reference manifest.

Reads every review export in output/reviews/, measures human-to-human agreement on the
independent scores, takes the median as the provisional reference, and flags cases that
need joint review. Joint-review decisions go in output/reviews/joint_review.json as
{"CLM-004": {"rating": 1, "note": "..."}}; they replace the median for that case only.
Individual decisions are kept beside the final reference for audit.

Finally, the script assigns each case to the tuning or held-out split, stratified by risk.

Usage:
    python 04_merge_reviews.py [--demo] [--holdout-fraction 0.2]

Output:
    output/human_reference.json
"""

import argparse
import random
from datetime import datetime, timezone
from statistics import median_low

from common import (
    DEMO_DIR,
    DEMO_OUTPUT_DIR,
    HUMAN_REFERENCE_FILE,
    REVIEW_CASES_FILE,
    REVIEWS_DIR,
    read_json,
    write_json,
)
from metrics import exact_agreement, mean_pairwise_kappa, within_one

SPLIT_SEED = 7


def load_reviews(reviews_dir, case_ids: list[str], version: str) -> dict[str, dict]:
    reviews = {}
    for path in sorted(reviews_dir.glob("review-*.json")):
        review = read_json(path)
        reviewer = review["reviewer_id"]
        if reviewer in reviews:
            raise SystemExit(f"Duplicate reviewer ID {reviewer!r} in {path.name}")
        if review.get("ground_truth_version") != version:
            raise SystemExit(
                f"{path.name} was scored against ground truth {review.get('ground_truth_version')!r}, "
                f"but the locked version is {version!r}. Re-review against the locked bundle."
            )
        missing = [case_id for case_id in case_ids if not (review["ratings"].get(case_id) or {}).get("rating")]
        if missing:
            raise SystemExit(f"{reviewer} has not rated: {', '.join(missing)}")
        reviews[reviewer] = review["ratings"]
    if not reviews:
        raise SystemExit(f"No review-*.json files found in {reviews_dir}")
    return reviews


def joint_review_reasons(decisions: list[dict]) -> list[str]:
    ratings = [decision["rating"] for decision in decisions]
    critical = [bool(decision.get("critical_failure")) for decision in decisions]
    insufficient = [bool(decision.get("insufficient_evidence")) for decision in decisions]
    reasons = []
    if max(ratings) - min(ratings) > 1:
        reasons.append("scores differ by more than one point")
    if any(critical) and not all(critical):
        reasons.append("critical failure not flagged by every reviewer")
    if any(insufficient) and not all(insufficient):
        reasons.append("reviewers disagree on evidence sufficiency")
    return reasons


def assign_splits(cases: list[dict], holdout_fraction: float) -> dict[str, str]:
    """Stratify by risk so both splits contain every risk tier with at least two cases."""
    rng = random.Random(SPLIT_SEED)
    splits = {}
    for risk in sorted({case["risk"] for case in cases}):
        members = sorted(case["case_id"] for case in cases if case["risk"] == risk)
        rng.shuffle(members)
        holdout = max(1, round(len(members) * holdout_fraction)) if len(members) > 1 else 0
        for position, case_id in enumerate(members):
            splits[case_id] = "holdout" if position < holdout else "tuning"
    return splits


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--demo", action="store_true", help="Use the recorded demo run in data/demo/")
    parser.add_argument("--holdout-fraction", type=float, default=0.2)
    args = parser.parse_args()

    cases_file = DEMO_DIR / "review_cases.json" if args.demo else REVIEW_CASES_FILE
    reviews_dir = DEMO_DIR / "reviews" if args.demo else REVIEWS_DIR
    output_file = DEMO_OUTPUT_DIR / "human_reference.json" if args.demo else HUMAN_REFERENCE_FILE

    bundle = read_json(cases_file)
    cases = bundle["cases"]
    case_ids = [case["case_id"] for case in cases]
    reviews = load_reviews(reviews_dir, case_ids, bundle["ground_truth_version"])
    reviewers = sorted(reviews)
    joint_file = reviews_dir / "joint_review.json"
    joint = read_json(joint_file) if joint_file.exists() else {}

    by_reviewer = {reviewer: [reviews[reviewer][case_id]["rating"] for case_id in case_ids] for reviewer in reviewers}
    first, *others = reviewers
    agreement = {
        "reviewers": reviewers,
        "mean_pairwise_weighted_kappa": mean_pairwise_kappa(by_reviewer) if others else None,
        "exact_agreement_with_first_reviewer": {
            other: exact_agreement(by_reviewer[first], by_reviewer[other]) for other in others
        },
        "within_one_with_first_reviewer": {
            other: within_one(by_reviewer[first], by_reviewer[other]) for other in others
        },
    }

    splits = assign_splits(cases, args.holdout_fraction)
    records, unresolved = [], []
    for case in cases:
        case_id = case["case_id"]
        decisions = [{"reviewer_id": reviewer, **reviews[reviewer][case_id]} for reviewer in reviewers]
        reasons = joint_review_reasons(decisions) if others else []
        provisional = median_low(decision["rating"] for decision in decisions)
        resolution = joint.get(case_id)
        if reasons and not resolution:
            unresolved.append(case_id)
        records.append(
            {
                "case_id": case_id,
                "session_id": case["session_id"],
                "title": case["title"],
                "intent": case["intent"],
                "risk": case["risk"],
                "split": splits[case_id],
                "decisions": decisions,
                "provisional_reference": provisional,
                "joint_review_reasons": reasons,
                "joint_review": resolution,
                "human_reference": resolution["rating"] if resolution else provisional,
                "critical_failure": sum(bool(d.get("critical_failure")) for d in decisions) * 2 > len(decisions),
            }
        )

    write_json(
        output_file,
        {
            "schema_version": "1.0",
            "ground_truth_version": bundle["ground_truth_version"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "human_agreement": agreement,
            "cases": records,
        },
    )

    kappa = agreement["mean_pairwise_weighted_kappa"]
    print(f"  Reviewers: {', '.join(reviewers)}")
    print(f"  Human-to-human weighted kappa: {kappa:.2f}" if kappa is not None else "  Human-to-human kappa: n/a")
    for record in records:
        scores = "/".join(str(d["rating"]) for d in record["decisions"])
        flag = f"  joint review: {'; '.join(record['joint_review_reasons'])}" if record["joint_review_reasons"] else ""
        print(f"  {record['case_id']} [{record['split']:>7}] {scores} -> {record['human_reference']}{flag}")
    if unresolved:
        print(f"\n  WARNING: {', '.join(unresolved)} need joint review. Add decisions to {joint_file.name};")
        print("  until then the median is used as the reference.")


if __name__ == "__main__":
    main()
