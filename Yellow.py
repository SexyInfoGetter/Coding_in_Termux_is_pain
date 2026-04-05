import asyncio
import websockets
import json
import logging
from datetime import datetime

#============== ADDED: Logging configuration ==============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
#============== END ADDED ==============

#============== ADDED: Persistence filename ==============
BLUES_FILE = "blues.json"
#============== END ADDED ==============

clients = {}  # id → {"ws": websocket, "role": "red"/"blue", "name": str, "status": "online"/"offline", "last_seen": timestamp}

#============== ADDED: Load Blues from persistence ==============
def load_blues():
    """Load previously connected Blues from file"""
    try:
        with open(BLUES_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.error(f"Error loading blues: {e}")
        return {}
#============== END ADDED ==============

#============== ADDED: Save Blues to persistence ==============
def save_blues():
    """Save Blues metadata to file"""
    try:
        blues_data = {}
        for client_id, client_info in clients.items():
            if client_info["role"] == "blue":
                blues_data[client_id] = {
                    "name": client_info["name"],
                    "status": client_info["status"],
                    "last_seen": client_info["last_seen"]
                }
        
        with open(BLUES_FILE, 'w') as f:
            json.dump(blues_data, f, indent=2)
        logger.debug("Blues saved to persistence")
    except Exception as e:
        logger.error(f"Error saving blues: {e}")
#============== END ADDED ==============

#============== ADDED: Get active Blues list ==============
def get_active_blues():
    """Return list of all online Blues"""
    return [
        {
            "id": client_id,
            "name": client_info["name"],
            "status": "online"
        }
        for client_id, client_info in clients.items()
        if client_info["role"] == "blue" and client_info["status"] == "online"
    ]
#============== END ADDED ==============

#============== ADDED: Broadcast Blues list to Red ==============
async def broadcast_blues_to_red():
    """Send active Blues list to Red controller"""
    active_blues = get_active_blues()
    message = json.dumps({
        "type": "blues_list",
        "blues": active_blues
    })
    
    # Find Red and send list
    for client_id, client_info in clients.items():
        if client_info["role"] == "red":
            try:
                await client_info["ws"].send(message)
                logger.debug(f"Sent Blues list to {client_id}")
            except Exception as e:
                logger.error(f"Error sending Blues list to Red: {e}")
#============== END ADDED ==============

#============== MODIFIED: Handler with metadata and error handling ==============
async def handler(ws):
    client_id = None
    role = None
    
    try:
        # First message = registration
        #============== MODIFIED: Better error handling ==============
        try:
            msg = await ws.recv()
            data = json.loads(msg)
        except json.JSONDecodeError:
            logger.error("Invalid JSON in registration")
            await ws.send(json.dumps({"type": "error", "message": "Invalid JSON in registration"}))
            return
        except Exception as e:
            logger.error(f"Error receiving registration: {e}")
            return
        #============== END MODIFIED ==============

        #============== MODIFIED: Validate required fields ==============
        if "id" not in data or "role" not in data:
            logger.error("Registration missing id or role")
            await ws.send(json.dumps({"type": "error", "message": "Missing id or role"}))
            return
        #============== END MODIFIED ==============

        client_id = data["id"]
        role = data["role"]
        name = data.get("name", client_id)  #============== MODIFIED: Get name from registration ==============

        #============== MODIFIED: Store metadata instead of just websocket ==============
        clients[client_id] = {
            "ws": ws,
            "role": role,
            "name": name,
            "status": "online",
            "last_seen": datetime.now().isoformat()
        }
        #============== END MODIFIED ==============

        logger.info(f"{client_id} ({role}) connected - {name}")

        #============== ADDED: If Blue connects, update persistence and notify Red ==============
        if role == "blue":
            save_blues()
            await broadcast_blues_to_red()
        #============== END ADDED ==============

        #============== ADDED: Send initial Blues list to Red ==============
        if role == "red":
            active_blues = get_active_blues()
            await ws.send(json.dumps({
                "type": "blues_list",
                "blues": active_blues
            }))
        #============== END ADDED ==============

        #============== MODIFIED: Message handling with better error handling and metadata update ==============
        async for message in ws:
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                logger.error(f"Invalid JSON from {client_id}")
                continue

            #============== ADDED: Update last_seen timestamp ==============
            if client_id in clients:
                clients[client_id]["last_seen"] = datetime.now().isoformat()
            #============== END ADDED ==============

            #============== ADDED: Handle ping/pong ==============
            if data.get("type") == "ping":
                try:
                    await ws.send(json.dumps({"type": "pong"}))
                except Exception as e:
                    logger.error(f"Error sending pong to {client_id}: {e}")
                continue
            #============== END ADDED ==============

            target = data.get("target")
            command = data.get("command", "")

            #============== MODIFIED: Better routing with error response ==============
            if target in clients and clients[target]["status"] == "online":
                try:
                    await clients[target]["ws"].send(message)
                    logger.info(f"{client_id} → {target}: {command}")
                except Exception as e:
                    logger.error(f"Error routing message to {target}: {e}")
                    # Send error back to sender
                    try:
                        await ws.send(json.dumps({
                            "type": "error",
                            "message": f"Target {target} disconnected",
                            "target": target
                        }))
                    except:
                        pass
            else:
                #============== MODIFIED: Send error response instead of silent failure ==============
                logger.warning(f"Target {target} not found or offline")
                try:
                    await ws.send(json.dumps({
                        "type": "error",
                        "message": f"Target {target} is offline",
                        "target": target
                    }))
                except Exception as e:
                    logger.error(f"Error sending error response: {e}")
                #============== END MODIFIED ==============

    #============== MODIFIED: Specific exception handling ==============
    except websockets.exceptions.ConnectionClosed:
        logger.info(f"Connection closed by {client_id}")
    except Exception as e:
        logger.error(f"Unexpected error in handler for {client_id}: {e}")
    #============== END MODIFIED ==============

    finally:
        # cleanup
        #============== MODIFIED: Better cleanup with metadata update ==============
        for k, v in list(clients.items()):
            if v["ws"] == ws:
                logger.info(f"{k} ({v['role']}) disconnected")
                
                #============== ADDED: Mark as offline instead of deleting (for persistence) ==============
                if v["role"] == "blue":
                    clients[k]["status"] = "offline"
                    save_blues()
                    await broadcast_blues_to_red()
                else:
                    del clients[k]
                #============== END ADDED ==============
                break
        #============== END MODIFIED ==============

async def main():
    #============== ADDED: Load Blues on startup ==============
    load_blues()
    logger.info("Loaded previously connected Blues")
    #============== END ADDED ==============

    async with websockets.serve(handler, "0.0.0.0", 8765):
        logger.info("Relay running on port 8765")
        await asyncio.Future()

asyncio.run(main())