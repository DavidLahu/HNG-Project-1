from dotenv import load_dotenv
from pathlib import Path
import os
import httpx
import asyncio
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import text

from datetime import timezone, datetime
from uuid6 import uuid7

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_async_engine(DATABASE_URL, echo=True)
AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_headers=["*"],
    allow_methods=["*"],
)

def classify_age_group(age):
    if age is None:
        return None
    elif age > 0 and age <= 12:
        return "child"
    elif age >= 13 and age <= 19:
        return "teenager"
    elif age >= 20 and age <= 59:
        return "adult"
    elif age > 60:
        return "senior"

async def enrich_text(name: str):
    async with httpx.AsyncClient() as client:
        gender_res, age_res, nation_res = await asyncio.gather(
            client.get(f"https://api.genderize.io?name={name}"),
            client.get(f"https://api.agify.io?name={name}"),
            client.get(f"https://api.nationalize.io?name={name}"),
        )

    gender_data = gender_res.json()
    age_data = age_res.json()
    nation_data = nation_res.json()

    if (gender_data["gender"] is None) or gender_data["count"] == 0:
        raise HTTPException(status_code=502, detail={
            "status": "502",
            "message": "Genderize returned an invalid response"
        })

    if age_data["age"] is None:
        raise HTTPException(status_code=502, detail={
            "status": "502",
            "message": "Agify returned an invalid response"
        })

    if not nation_data.get("country"):
        raise HTTPException(status_code=502, detail={
            "status": "502",
            "message": "Nationalize returned an invalid response"
        })

    age = age_data["age"]
    top_country = max(nation_data["country"], key=lambda c: c["probability"])

    return {
        "id": str(uuid7()),
        "name": name,
        "gender": gender_data["gender"],
        "gender_probability": gender_data["probability"],
        "sample_size": gender_data["count"],
        "age": age,
        "age_group": classify_age_group(age),
        "country_id": top_country["country_id"],
        "country_probability": top_country["probability"],
    }


class UserInput(BaseModel):
    name: str

@app.get("/")
async def root():
    return {"message": "Everything working fine."}


@app.post("/api/profiles", status_code=201)
async def profile(input: UserInput, db: AsyncSession = Depends(get_db)):
    cleaned_username = input.name.strip()
    if not cleaned_username:
        raise HTTPException(status_code=400, detail={
            "status": "error",
            "message": "Missing or empty name"
        })

    if not isinstance(cleaned_username, str):
        raise HTTPException(status_code=422, detail={
            "status": "error",
            "message": "Unprocessable Entity: Invalid Type"
        })

    result = await db.execute(
        text("SELECT * FROM profiles WHERE LOWER(name) = LOWER(:name)"),
        {"name": cleaned_username}
    )

    existing = result.fetchone()
    if existing:
        row = dict(existing._mapping)
        row["created_at"] = row["created_at"].isoformat()
        return {"status": "success", "message": "Profile already exists", "data": row}

    enriched = await enrich_text(cleaned_username)
    created_at = datetime.now(timezone.utc)

    await db.execute(
        text("""
            INSERT INTO profiles (id, name, gender, gender_probability, sample_size,
                                  age, age_group, country_id, country_probability, created_at)
            VALUES (:id, :name, :gender, :gender_probability, :sample_size,
                    :age, :age_group, :country_id, :country_probability, :created_at)
        """),
        {**enriched, "created_at": created_at}
    )
    await db.commit()

    return {
        "status": "success",
        "message": "Profile created successfully",
        "data": {
            **enriched,
            "created_at": created_at.isoformat()
        }
    }


@app.get("/api/profiles/{id}", status_code=200)
async def get_user(id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("SELECT * FROM profiles WHERE id = :id"), {"id": id}
    )

    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={
            "status": "error",
            "message": "Not Found: Profile not found"
        })
    data = dict(row._mapping)
    data["created_at"] = data["created_at"].isoformat()
    return {"status": "success", "data": data}


@app.get("/api/profiles", status_code=200)
async def get_all_profiles(name: str | None = None, gender: str | None = None, country_id: str | None = None, age_group: str | None = None, db: AsyncSession = Depends(get_db)):
    conditions = []
    params = {}

    if name:
        conditions.append("LOWER(name) LIKE LOWER(:name)")
        params["name"] = f"%{name}%"
    if gender:
        conditions.append("LOWER(gender) = LOWER(:gender)")
        params["gender"] = gender
    if age_group:
        conditions.append("LOWER(age_group) = LOWER(:age_group)")
        params["age_group"] = age_group
    if country_id:
        conditions.append("LOWER(country_id) = LOWER(:country_id)")
        params["country_id"] = country_id

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    query = f"SELECT id, name, gender, age, age_group, country_id FROM profiles {where_clause}"

    result = await db.execute(text(query), params)
    rows = result.fetchall()

    return {
        "status": "success",
        "count": len(rows),
        "data": [dict(r._mapping) for r in rows]
    }


@app.delete("/api/profiles/{id}", status_code=204)
async def deletion(id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("SELECT * FROM profiles WHERE id = :id"),
        {"id": id}
    )

    if not result.fetchone():
        raise HTTPException(status_code=404, detail={
            "status": "error",
            "message": "Profile not found"
        })
    await db.execute(
        text("DELETE FROM profiles WHERE id = :id"),
        {"id": id}
    )
    await db.commit()
    return None