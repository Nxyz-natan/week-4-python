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
                           CREATE TABLE IF NOT EXIST api_keys (
                           id INTEGER PRIMARY KEY AUTOINCREMENT,
                           key TEXT NOT NULL UNIQUE,
                           owner TEXT NOT NULL,
                           created_at TEXT NOT NULL DEFUALT (datetime('now'))
                           );
                           
                           CREATE TABLE IF NOT EXISTS decks (
                           id INTEGER PRIMARY KEY AUTOINCREMENT,
                           owner_key TEXT NOT NULL,
                           name TEXT NOT NULL,
                            created_at TEXT NOT NULL DEFUALT (datetime('now')),
                           FOREIGN KEY (owner_key) REFERENCES api_keys(key) ON DELETE CASCADE
                           ):
                           
                           CREATE TABLEIF NOT EXISTS cards (
                           id INTEGER PRIMARY KEY AUTOINCREMENT,
                           deck_id INTEGER NOT NULL,
                           front TEXT NOT NULL,
                           back TEXT NOT NULL,
                           due_at TEXT NOT NULL DEFUALT (datetime('now')),
                           interval_days INTEGER NOT NULL DEFUALT 0,
                           ease REAL NOT NULL DEFUALT 2.5,
                           created_at TEXT NOT NULL DEFUALT (datetime('now')),
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

def update_deck_stats(deck_id: int) ->:
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
    description="I think you know how flash cards work"
    version="0.0.1",
    lifespan=lifespan,
)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def register(body: RegisterBody):
    key = create_api_key(body.name)
    return {
        "api_key": key,
        "message": "Save this it will vanish"
    }



                            