# Profile Intelligence Service

A REST API that enriches a name with gender, age, and nationality predictions.

## Base URL
https://hng-project-1-production.up.railway.app/

## Endpoints

### POST /api/profiles
Creates a new profile by enriching a name with external API data.

**Request:**
```json
{"name": "John"}
```

**Response:**
```json
{
  "status": "success",
  "message": "Profile created successfully",
  "data": {
    "id": "...",
    "name": "John",
    "gender": "male",
    "gender_probability": 0.98,
    "sample_size": 100,
    "age": 35,
    "age_group": "adult",
    "country_id": "US",
    "country_probability": 0.12,
    "created_at": "2026-04-17T12:00:00+00:00"
  }
}
```

### GET /api/profiles/{id}
Returns a single profile by ID.

### GET /api/profiles
Returns all profiles. Optional filters: `name`, `gender`, `age_group`, `country_id`.

### DELETE /api/profiles/{id}
Deletes a profile by ID. Returns 204.

## Stack
- FastAPI
- PostgreSQL (Supabase)
- SQLAlchemy async
- Deployed on Railway
