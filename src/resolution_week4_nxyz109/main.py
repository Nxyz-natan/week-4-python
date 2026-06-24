import datetime
import os 
import secrets
import sqlite3
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

DB_PATH = os.environ.get("FLASHCARD_DB", "flashcards.db")

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db() -> None:
    conn = get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL UNIQUE,
                owner TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS decks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_key TEXT NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (owner_key) REFERENCES api_keys(key) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deck_id INTEGER NOT NULL,
                front TEXT NOT NULL,
                back TEXT NOT NULL,
                due_at TEXT NOT NULL DEFAULT (datetime('now')),
                interval_days INTEGER NOT NULL DEFAULT 0,
                ease REAL NOT NULL DEFAULT 2.5,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (deck_id) REFERENCES decks(id) ON DELETE CASCADE
            );
        """)
        conn.commit()
    finally:
        conn.close()

def create_api_key(owner: str) -> str:
    key = secrets.token_hex(16)
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO api_keys (key, owner) VALUES (?, ?)",
            (key,owner),
        )
        conn.commit()
    finally:
        conn.close()
    return key

async def verify_api_key(x_api_key: str = Header()) -> sqlite3.Row:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM api_keys WHERE key = ?", (x_api_key,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return row


def log_rating(card_id: int, rating: int, api_key: str) -> None:
    with open("rating.log", "a") as f:
        f.write(
            f"{datetime.datetime.now().isoformat()} - "
            f"card={card_id} rating={rating} key={api_key}\n"
        )

def update_deck_stats(deck_id: int) -> None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n, AVG(ease) AS avg_ease FROM cards WHERE deck_id = ?",
            (deck_id,),
        ).fetchone()
    finally:
        conn.close()
    with open("stats.log", "a") as f:
        f.write(
            f"{datetime.datetime.now().isoformat()} - "
            f"deck={deck_id} cards={row['n']} avg_ease ={row['avg_ease']:.3f}\n"
        )


class RegisterBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)

class DeckBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)


class CardBody(BaseModel):
    front: str = Field(..., min_length=1)
    back: str = Field(..., min_length=1)

class RateBody(BaseModel):
    rating: int = Field(..., ge=1, le=4)


def get_api_key_for_limiter(request: Request) -> str:
    return request.headers.get("x-api-key", "anonymous")

limiter = Limiter(key_func=get_api_key_for_limiter)

@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield

app = FastAPI(
    title="FLASH CARD STUDY API",
    description="I think you know how flash cards work",
    version="0.0.1",
    lifespan=lifespan,
)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded Try again later!"},
    )


@app.post("/register")
async def register(body: RegisterBody):
    key = create_api_key(body.name)
    return {
        "api_key": key,
        "message": "Save this before it vanishes",
    }

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/decks", dependencies=[Depends(verify_api_key)])
@limiter.limit("30/minute")
async def create_deck(request: Request, body: DeckBody, key_info=Depends(verify_api_key)):
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO decks (owner_key, name) VALUES (?, ?)",
            (key_info["key"], body.name),
        )
        conn.commit()
        deck_id = cur.lastrowid
    finally:
        conn.close()
    return {"id": deck_id, "name": body.name}


@app.get("/decks", dependencies=[Depends(verify_api_key)])
@limiter.limit("30/minute")
async def list_decks(request: Request, key_info=Depends(verify_api_key)):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, name, created_at FROM decks WHERE owner_key = ? ORDER BY id",
            (key_info["key"],),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]




@app.get("/decks/{deck_id}", dependencies=[Depends(verify_api_key)])
@limiter.limit("30/minute")
async def get_deck(request: Request, deck_id: int, key_info=Depends(verify_api_key)):
    conn = get_connection()
    try:
        deck = conn.execute(
            "SELECT id, name, created_at FROM decks WHERE id = ? AND owner_key = ?",
            (deck_id, key_info["key"]),
        ).fetchone()
        if deck is None:
            raise HTTPException(status_code=404, detail="Deck not found")
        cards = conn.execute(
            "SELECT id, front, back, due_at, interval_days, ease FROM cards WHERE deck_id = ? ORDER BY id",
            (deck_id,),
        ).fetchall()
    finally:
        conn.close()
    return {"deck": dict(deck), "cards": [dict(c) for c in cards]}
                            



@app.delete("/decks/{deck_id}", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
async def delete_deck(request: Request, deck_id: int, key_info=Depends(verify_api_key)):
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM decks WHERE id = ? AND owner_key = ?",
            (deck_id, key_info["key"]),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail= "Deck not found")
    finally:
        conn.close()
    return {"deleted": deck_id}




@app.post("/decks/{deck_id}/cards", dependencies=[Depends(verify_api_key)])
@limiter.limit("30/minute")
async def add_card(
    request: Request,
    deck_id: int,
    body: CardBody,
    key_info=Depends(verify_api_key),
):
    conn = get_connection()
    try:
        deck = conn.execute(
            "SELECT id FROM decks WHERE id = ? AND owner_key = ?",
            (deck_id, key_info["key"]),
        ).fetchone()
        if deck is None:
            raise HTTPException(status_code=404, detail="Deck not found")
        cur = conn.execute(
            "INSERT INTO cards (deck_id, front, back) VALUES (?, ?, ?)",
            (deck_id, body.front, body.back),
        )
        conn.commit()
        card_id = cur.lastrowid
    finally:
        conn.close()
    return {"id": card_id, "front": body.front, "back": body.back}



@app.get("/decks/{deck_id}/study", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
async def study_deck(
    request: Request,
    deck_id: int,
    limit: int = Query(5, ge=1, le=20),
    key_info=Depends(verify_api_key),
):
    conn = get_connection()
    try:
        deck = conn.execute(
            "SELECT id FROM decks WHERE id = ? AND owner_key = ?",
            (deck_id, key_info["key"]),
        ).fetchone()
        if deck is None:
            raise HTTPException(status_code=404, detail="Deck not found")
        rows = conn.execute(
            """
            SELECT id, front, back, due_at, interval_days, ease
            FROM cards
            WHERE deck_id = ? AND due_at <= datetime('now')
            ORDER BY RANDOM()
            LIMIT ?
            """,
            (deck_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return {"deck_id": deck_id, "due": [dict(r) for r in rows], "count": len(rows)}



@app.post("/cards/{card_id}/rate", dependencies=[Depends(verify_api_key)])
@limiter.limit("30/minute")
async def rate_card(
    request: Request,
    card_id: int,
    body: RateBody,
    background_tasks: BackgroundTasks,
    key_info= Depends(verify_api_key),
):
    conn = get_connection()
    try:
        card = conn.execute(
            """
            SELECT c.id, c.deck_id, c.interval_days, c.ease
            FROM cards c
            JOIN decks d ON d.id = c.deck_id
            WHERE c.id =  ? AND d.owner_key =  ?
            """,
            (card_id, key_info["key"]),
        ).fetchone()
        if card is None:
            raise HTTPException(status_code=404, detail="Card not found")

        interval = card["interval_days"]
        ease = card["ease"]
        rating = body.rating

        if rating <= 2:
            interval = 1
            ease = max(1.3, ease - 0.2)
            minutes = 10 if rating == 1 else 60 * 24
            due_at = (
                datetime.datetime.utcnow() + datetime.timedelta(minutes=minutes)
            ).isoformat(timespec="seconds")
        else:
            interval = max(1, round((interval if interval else 1) * ease))
            ease = min(3.0, ease + (0.1 if rating == 4 else 0.0))
            due_at = (
                datetime.datetime.utcnow() + datetime.timedelta(days=interval)
            ).isoformat(timespec="seconds")

        conn.execute(
            "UPDATE cards SET due_at = ?, interval_days = ?, ease = ? WHERE id = ?",
            (due_at, interval, ease, card_id),
        )
        conn.commit()
    finally:
        conn.close()

    background_tasks.add_task(log_rating, card_id, rating, key_info["key"])
    background_tasks.add_task(update_deck_stats, card["deck_id"])

    return {
        "card_id": card_id,
        "rating": rating,
        "next_due_at": due_at,
        "new_interval_days": interval,
        "new_ease": round(ease, 3),
    }


def main():
    import uvicorn
    uvicorn.run(
        "resolution_week4_nxyz109.main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    main()
