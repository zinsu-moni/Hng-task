import argparse
import json
import os
import uuid
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy import select, update

from app.db.session import SessionLocal
from app.models.profile import Profile
from app.utils.uuid7 import uuid7


def chunks(items: List[Any], chunk_size: int) -> Iterable[List[Any]]:
    for i in range(0, len(items), chunk_size):
        yield items[i : i + chunk_size]


def parse_uuid(value: Optional[Any]) -> uuid.UUID:
    if value is None or value == "":
        return uuid7()
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def load_profiles_json(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict) and "profiles" in data:
        data = data["profiles"]

    if not isinstance(data, list):
        raise ValueError("JSON must be a list of profiles or an object with a 'profiles' key")

    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed profiles into PostgreSQL (id/name upsert).")
    parser.add_argument("--json-file", required=True, help="Path to JSON file containing profiles")
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=1000,
        help="Chunk size for IN queries (default: 1000)",
    )
    args = parser.parse_args()

    # Seed uses DATABASE_URL from env (see app/db/session.py).
    if not os.getenv("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL env var must be set to run the seed")

    raw_profiles = load_profiles_json(args.json_file)

    # Normalize/validate expected keys (minimal mapping; DB constraints enforce nullability).
    normalized: List[Dict[str, Any]] = []
    ids: Set[uuid.UUID] = set()
    names: Set[str] = set()
    name_to_id: Dict[str, uuid.UUID] = {}
    id_to_name: Dict[uuid.UUID, str] = {}

    for i, p in enumerate(raw_profiles):
        if "name" not in p:
            raise ValueError(f"Profile at index {i} is missing required key 'name'")

        record_id = parse_uuid(p.get("id"))
        name = str(p["name"])

        # Fail fast if the JSON has conflicting duplicates.
        if name in name_to_id and name_to_id[name] != record_id:
            raise ValueError(
                f"Duplicate name {name!r} in JSON with different ids "
                f"({name_to_id[name]} vs {record_id})"
            )
        if record_id in id_to_name and id_to_name[record_id] != name:
            raise ValueError(
                f"Duplicate id {record_id} in JSON with different names "
                f"({id_to_name[record_id]!r} vs {name!r})"
            )
        name_to_id[name] = record_id
        id_to_name[record_id] = name

        gender = p.get("gender")
        if gender is None:
            raise ValueError(f"Profile at index {i} is missing 'gender'")
        gender = str(gender).strip().lower()
        if gender not in {"male", "female"}:
            raise ValueError(f"Profile at index {i} has invalid gender={gender!r}")

        age_group = p.get("age_group")
        if age_group is None:
            raise ValueError(f"Profile at index {i} is missing 'age_group'")
        age_group = str(age_group).strip().lower()
        if age_group not in {"child", "teenager", "adult", "senior"}:
            raise ValueError(f"Profile at index {i} has invalid age_group={age_group!r}")

        country_id = p.get("country_id")
        if country_id is None:
            raise ValueError(f"Profile at index {i} is missing 'country_id'")
        country_id = str(country_id).strip().upper()
        if len(country_id) != 2:
            raise ValueError(f"Profile at index {i} has invalid country_id={country_id!r} (expected 2 chars)")

        # Type normalization (so DB writes are consistent).
        gender_probability = p.get("gender_probability")
        age = p.get("age")
        age_val = int(age) if age is not None else None
        country_name = p.get("country_name")
        country_probability = p.get("country_probability")

        missing = [
            ("gender_probability", gender_probability),
            ("age", age),
            ("age_group", age_group),
            ("country_id", country_id),
            ("country_name", country_name),
            ("country_probability", country_probability),
        ]
        for field_name, field_value in missing:
            if field_value is None:
                raise ValueError(f"Profile at index {i} is missing {field_name!r}")

        normalized.append(
            {
                "id": record_id,
                "name": name,
                "gender": gender,
                "gender_probability": float(gender_probability),
                "age": age_val,
                "age_group": age_group,
                "country_id": country_id,
                "country_name": str(country_name),
                "country_probability": float(country_probability),
            }
        )
        ids.add(record_id)
        names.add(name)

    inserted = 0
    updated_by_name = 0
    updated_by_id = 0

    with SessionLocal.begin() as session:
        # Snapshot existing rows so we can upsert deterministically.
        existing_by_id: Dict[uuid.UUID, uuid.UUID] = {}
        existing_by_name: Dict[str, uuid.UUID] = {}

        id_list = list(ids)
        name_list = list(names)

        for id_chunk in chunks(id_list, args.chunk_size):
            rows = session.execute(select(Profile.id, Profile.name).where(Profile.id.in_(id_chunk))).all()
            for row in rows:
                existing_by_id[row.id] = row.id
                existing_by_name[row.name] = row.id

        for name_chunk in chunks(name_list, args.chunk_size):
            rows = session.execute(select(Profile.id, Profile.name).where(Profile.name.in_(name_chunk))).all()
            for row in rows:
                existing_by_id[row.id] = row.id
                existing_by_name[row.name] = row.id

        # Upsert logic:
        # - If name exists, update by name.
        # - Else if id exists, update by id.
        # - Else insert new row.
        for rec in normalized:
            id_value: uuid.UUID = rec["id"]
            name: str = rec["name"]

            values = {
                "gender": rec["gender"],
                "gender_probability": rec["gender_probability"],
                "age": rec["age"],
                "age_group": rec["age_group"],
                "country_id": rec["country_id"],
                "country_name": rec["country_name"],
                "country_probability": rec["country_probability"],
            }

            if name in existing_by_name:
                session.execute(
                    update(Profile)
                    .where(Profile.name == name)
                    .values(**values)
                )
                updated_by_name += 1
                continue

            if id_value in existing_by_id:
                session.execute(
                    update(Profile)
                    .where(Profile.id == id_value)
                    .values(name=name, **values)
                )
                updated_by_id += 1
                continue

            session.add(Profile(id=id_value, name=name, **values))
            inserted += 1

    print(
        f"Seed complete: inserted={inserted}, updated_by_name={updated_by_name}, updated_by_id={updated_by_id}"
    )


if __name__ == "__main__":
    main()

