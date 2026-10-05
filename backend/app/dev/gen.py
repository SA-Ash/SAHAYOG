import argparse
import json

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.entities import User
from app.models.intelligence import ScenarioTruth
from app.schemas.intelligence import ScenarioInput
from app.services.scenarios import ScenarioGenerator


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preset", choices=["easy", "hard", "multi-victim"], default="hard")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--load", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as db:
        scenario = ScenarioGenerator.generate(db, ScenarioInput(preset=args.preset, seed=args.seed))
        output = {
            "scenario_id": str(scenario.id),
            "truth": db.get(ScenarioTruth, scenario.id).truth_json,
        }
        if args.load:
            actor = db.scalar(
                select(User).where(User.role == "INVESTIGATOR", User.is_active.is_(True))
            )
            if not actor:
                parser.error("Seed an investigator first")
            output["case_ids"] = [str(c.id) for c in ScenarioGenerator.load(db, scenario, actor)]
        print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
