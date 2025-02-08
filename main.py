from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel, Field
from typing import List, Optional
import pymongo
import datetime
from bson import ObjectId

# API Configuration
app = FastAPI(title="Day Out Planner API")

# MongoDB Configuration
MONGO_URI = "mongodb+srv://dayoutplanner:newcoolpassword@cluster0.wd4xi.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
client = pymongo.MongoClient(MONGO_URI)
db = client["dayoutplanner"]
users_collection = db["users"]
lobbies_collection = db["lobbies"]
active_lobbies_collection = db["active_lobbies"]

# Create indexes
lobbies_collection.create_index("lobby_id", unique=True)
users_collection.create_index("user_id", unique=True)

# Models
class UserBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=50)

class UserResponse(UserBase):
    user_id: str
    current_lobby_id: Optional[str] = None
    is_host: bool = False

class LobbyBase(BaseModel):
    lobby_id: str
    host_id: str
    users: List[dict]
    created_at: datetime.datetime
    status: str = "open"  # open or active

class LobbyResponse(LobbyBase):
    user_count: int

class LikeBase(BaseModel):
    place_id: int = Field(..., gt=0)

# User Management
@app.post("/users/create", response_model=UserResponse)
async def create_user(user: UserBase):
    """
    Create a new user
    Example request:
        POST /users/create
        {
            "name": "John Doe"
        }
    """
    user_id = str(ObjectId())
    user_data = {
        "user_id": user_id,
        "name": user.name,
        "current_lobby_id": None,
        "is_host": False,
        "created_at": datetime.datetime.now()
    }
    
    users_collection.insert_one(user_data)
    return UserResponse(**user_data)

# Lobby Management
@app.post("/lobbies/create")
async def create_lobby(user_id: str):
    """
    Create a new lobby
    Example request:
        POST /lobbies/create?user_id=123
    """
    user = users_collection.find_one({"user_id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if user.get("current_lobby_id"):
        raise HTTPException(status_code=400, detail="User already in a lobby")

    lobby_id = str(abs(hash(str(datetime.datetime.now()))))[:6]
    lobby_data = {
        "lobby_id": lobby_id,
        "host_id": user_id,
        "users": [{
            "user_id": user_id,
            "name": user["name"],
            "joined_at": datetime.datetime.now()
        }],
        "created_at": datetime.datetime.now(),
        "status": "open"
    }

    lobbies_collection.insert_one(lobby_data)
    users_collection.update_one(
        {"user_id": user_id},
        {"$set": {"current_lobby_id": lobby_id, "is_host": True}}
    )

    return {"lobby_id": lobby_id}

@app.post("/lobbies/{lobby_id}/join")
async def join_lobby(lobby_id: str, user_id: str):
    """
    Join an existing lobby
    Example request:
        POST /lobbies/123456/join?user_id=123
    """
    user = users_collection.find_one({"user_id": user_id})
    lobby = lobbies_collection.find_one({"lobby_id": lobby_id})

    if not user or not lobby:
        raise HTTPException(status_code=404, detail="User or lobby not found")

    if user.get("current_lobby_id"):
        raise HTTPException(status_code=400, detail="User already in a lobby")

    if lobby["status"] != "open":
        raise HTTPException(status_code=400, detail="Lobby is not open")

    # Add user to lobby
    lobbies_collection.update_one(
        {"lobby_id": lobby_id},
        {"$push": {"users": {
            "user_id": user_id,
            "name": user["name"],
            "joined_at": datetime.datetime.now()
        }}}
    )

    # Update user's lobby status
    users_collection.update_one(
        {"user_id": user_id},
        {"$set": {"current_lobby_id": lobby_id}}
    )

    return {"message": "Successfully joined lobby"}

@app.post("/lobbies/{lobby_id}/start")
async def start_lobby(lobby_id: str, user_id: str):
    """
    Start a lobby (host only)
    Example request:
        POST /lobbies/123456/start?user_id=123
    """
    user = users_collection.find_one({"user_id": user_id})
    lobby = lobbies_collection.find_one({"lobby_id": lobby_id})

    if not user or not lobby:
        raise HTTPException(status_code=404, detail="User or lobby not found")

    if lobby["host_id"] != user_id:
        raise HTTPException(status_code=403, detail="Only host can start lobby")

    # Move lobby to active collection
    active_lobby_data = {
        **lobby,
        "status": "active",
        "started_at": datetime.datetime.now(),
        "user_likes": {user["user_id"]: [] for user in lobby["users"]}
    }

    active_lobbies_collection.insert_one(active_lobby_data)
    lobbies_collection.delete_one({"lobby_id": lobby_id})

    return {"message": "Lobby started successfully"}

@app.post("/lobbies/{lobby_id}/like")
async def add_like(lobby_id: str, user_id: str, like: LikeBase):
    """
    Add a like to a place in active lobby
    Example request:
        POST /lobbies/123456/like?user_id=123
        {
            "place_id": 12345
        }
    """
    active_lobby = active_lobbies_collection.find_one({"lobby_id": lobby_id})
    if not active_lobby:
        raise HTTPException(status_code=404, detail="Active lobby not found")

    # Add like to user's likes
    active_lobbies_collection.update_one(
        {"lobby_id": lobby_id},
        {"$push": {f"user_likes.{user_id}": like.place_id}}
    )

    # Check for matches
    all_likes = active_lobby["user_likes"]
    common_likes = set(all_likes[list(all_likes.keys())[0]])
    for user_likes in all_likes.values():
        common_likes &= set(user_likes)

    return {
        "liked": True,
        "matches": list(common_likes) if common_likes else []
    }

@app.get("/lobbies/open")
async def get_open_lobbies():
    """
    Get list of open lobbies
    Example response:
        GET /lobbies/open
        {
            "lobbies": [
                {
                    "lobby_id": "123456",
                    "host_name": "John",
                    "user_count": 3,
                    "created_at": "2024-01-20T..."
                }
            ]
        }
    """
    lobbies = list(lobbies_collection.find(
        {"status": "open"},
        {"_id": 0}
    ).sort("created_at", -1))

    return {
        "lobbies": [{
            "lobby_id": lobby["lobby_id"],
            "host_name": next(u["name"] for u in lobby["users"] if u["user_id"] == lobby["host_id"]),
            "user_count": len(lobby["users"]),
            "created_at": lobby["created_at"]
        } for lobby in lobbies]
    }

@app.get("/lobbies/{lobby_id}")
async def get_lobby_details(lobby_id: str):
    """
    Get detailed information about a specific lobby
    Example response:
        GET /lobbies/123456
        {
            "lobby_id": "123456",
            "status": "open",
            "host_id": "123",
            "users": [...],
            "created_at": "2024-01-20T..."
        }
    """
    lobby = lobbies_collection.find_one({"lobby_id": lobby_id}, {"_id": 0})
    if not lobby:
        lobby = active_lobbies_collection.find_one({"lobby_id": lobby_id}, {"_id": 0})
    if not lobby:
        raise HTTPException(status_code=404, detail="Lobby not found")
    return lobby
