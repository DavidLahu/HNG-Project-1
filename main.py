from fastapi import Request, FastAPI, Depends, HTTPException
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pathlib import Path
from pydantic import BaseModel
from datetime import timezone, datetime
from uuid6 import uuid7
import os
import httpx
import asyncio
import asyncpg

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")
DATABASE_URL = os.getenv("DATABASE_URL").replace("postgresql+asyncpg://", "postgresql://")

async def get_db():
    conn = await asyncpg.connect(DATABASE_URL, ssl="require")
    try:
        yield conn
    finally:
        await conn.close()

app = FastAPI()

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"status": "error", "message": "Unprocessable Entity: Invalid input"}
    )

@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return JSONResponse(
        status_code=404,
        content={"status": "error", "message": "Not Found"}
    )

@app.exception_handler(405)
async def method_not_allowed_handler(request: Request, exc):
    return JSONResponse(
        status_code=405,
        content={"status": "error", "message": "Method Not Allowed"}
    )

@app.exception_handler(500)
async def internal_error_handler(request: Request, exc):
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": "Internal server error"}
    )

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
async def profile(input: UserInput, db=Depends(get_db)):
    cleaned_username = input.name.strip()
    if not cleaned_username:
        raise HTTPException(status_code=400, detail={
            "status": "error",
            "message": "Missing or empty name"
        })

    existing = await db.fetchrow(
        "SELECT * FROM profiles WHERE LOWER(name) = LOWER($1)",
        cleaned_username
    )

    if existing:
        row = dict(existing)
        row["created_at"] = row["created_at"].isoformat()
        return {"status": "success", "message": "Profile already exists", "data": row}

    enriched = await enrich_text(cleaned_username)
    created_at = datetime.now(timezone.utc)

    await db.execute(
        """
        INSERT INTO profiles (id, name, gender, gender_probability, sample_size,
                              age, age_group, country_id, country_probability, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        enriched["id"], enriched["name"], enriched["gender"],
        enriched["gender_probability"], enriched["sample_size"],
        enriched["age"], enriched["age_group"], enriched["country_id"],
        enriched["country_probability"], created_at
    )

    return {
        "status": "success",
        "message": "Profile created successfully",
        "data": {
            **enriched,
            "created_at": created_at.isoformat()
        }
    }


@app.get("/api/profiles/{id}", status_code=200)
async def get_user(id: str, db=Depends(get_db)):
    row = await db.fetchrow(
        "SELECT * FROM profiles WHERE id = $1", id
    )

    if not row:
        raise HTTPException(status_code=404, detail={
            "status": "error",
            "message": "Not Found: Profile not found"
        })

    data = dict(row)
    data["created_at"] = data["created_at"].isoformat()
    return {"status": "success", "data": data}


@app.get("/api/profiles", status_code=200)
async def get_all_profiles(
    name: str | None = None,
    gender: str | None = None,
    country_id: str | None = None,
    age_group: str | None = None,
    db=Depends(get_db)
):
    conditions = []
    params = []

    if name:
        params.append(f"%{name}%")
        conditions.append(f"LOWER(name) LIKE LOWER(${len(params)})")
    if gender:
        params.append(gender)
        conditions.append(f"LOWER(gender) = LOWER(${len(params)})")
    if age_group:
        params.append(age_group)
        conditions.append(f"LOWER(age_group) = LOWER(${len(params)})")
    if country_id:
        params.append(country_id)
        conditions.append(f"LOWER(country_id) = LOWER(${len(params)})")

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    query = f"SELECT id, name, gender, age, age_group, country_id FROM profiles {where_clause}"

    rows = await db.fetch(query, *params)

    return {
        "status": "success",
        "count": len(rows),
        "data": [dict(r) for r in rows]
    }


@app.delete("/api/profiles/{id}", status_code=204)
async def deletion(id: str, db=Depends(get_db)):
    row = await db.fetchrow(
        "SELECT id FROM profiles WHERE id = $1", id
    )

    if not row:
        raise HTTPException(status_code=404, detail={
            "status": "error",
            "message": "Profile not found"
        })

    await db.execute(
        "DELETE FROM profiles WHERE id = $1", id
    )
    return None