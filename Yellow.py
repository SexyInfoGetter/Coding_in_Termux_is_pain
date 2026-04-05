#!/usr/bin/env python3
"""
Yellow Relay Server
Central hub for Red controller and Blue agents
Manages connections, routes messages, persists blue data
"""

import asyncio
import websockets
import json
import os
from datetime import datetime
from typing import Dict, Set

# Configuration
RELAY_HOST = "0.0.0.0"  # Listen on all interfaces
RELAY_PORT = 5555
DATA_FILE = "blues_data.json"

# Global state
connected_blues: Dict[str, Dict] = {}  # {blue_id: {name, ws, last_seen, ...}}
red_connection = None  # Single Red controller connection


def load_blues_data():
    """Load persisted blues data from file"""
    global connected_blues
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
                # Load metadata but mark all as offline since they're reconnecting
                for blue in data.get("blues", []):
                    connected_blues[blue["id"]] = {
                        "name": blue.get("name", "Unknown"),
                        "os": blue.get("os", "Unknown"),
                        "status": "offline",
                        "last_seen": blue.get("last_seen", ""),
                        "ws": None
                    }
                print(f"[+] Loaded {len(connected_blues)} blues from persistence")
        except Exception as e:
            print(f"[-] Error loading data: {e}")
    else:
        print("[+] No previous blues data found")


def save_blues_data():
    """Persist blues data to file"""
    try:
        data = {
            "blues": [
                {
                    "id": blue_id,
                    "name": info["name"],
                    "os": info.get("os", "Unknown"),
                    "status": info["status"],
                    "last_seen": info["last_seen"]
                }
                for blue_id, info in connected_blues.items()
            ]
        }
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[-] Error saving data: {e}")


def get_active_blues():
    """Return list of currently online blues"""
    return [
        {
            "id": blue_id,
            "name": info["name"],
            "os": info.get("os", "Unknown"),
            "last_seen": info["last_seen"]
        }
        for blue_id, info in connected_blues.items()
        if info["status"] == "online"
    ]


async def handle_blue_connection(websocket, path):
    """Handle Blue agent connections"""
    global connected_blues
    
    blue_id = None
    try:
        # First message should be registration
        msg = await websocket.recv()
        data = json.loads(msg)
        
        if data.get("type") != "register":
            print(f"[-] Invalid first message from blue")
            return
        
        blue_id = data.get("id")
        blue_name = data.get("name", "Unknown")
        blue_os = data.get("os", "Unknown")
        
        if not blue_id:
            print(f"[-] Blue registration missing ID")
            return
        
        # Register or update blue
        connected_blues[blue_id] = {
            "name": blue_name,
            "os": blue_os,
            "status": "online",
            "last_seen": datetime.now().isoformat(),
            "ws": websocket
        }
        
        print(f"[+] Blue registered: {blue_id} ({blue_name})")
        save_blues_data()
        
        # Notify Red of the blue list update
        await notify_red_of_blues_update()
        
        # Keep connection alive and listen for messages
        while True:
            try:
                msg = await websocket.recv()
                data = json.loads(msg)
                
                # Handle heartbeat
                if data.get("type") == "heartbeat":
                    connected_blues[blue_id]["last_seen"] = datetime.now().isoformat()
                    continue
                
                # Handle responses from blue (to forward to red)
                if data.get("type") == "response":
                    if red_connection:
                        try:
                            await red_connection.send(json.dumps(data))
                        except Exception as e:
                            print(f"[-] Error sending response to red: {e}")
                    continue
                
                print(f"[+] Message from {blue_id}: {data.get('type')}")
                
            except websockets.exceptions.ConnectionClosed:
                break
            except json.JSONDecodeError:
                print(f"[-] Invalid JSON from {blue_id}")
    
    except Exception as e:
        print(f"[-] Error in blue connection: {e}")
    
    finally:
        # Mark blue as offline
        if blue_id and blue_id in connected_blues:
            connected_blues[blue_id]["status"] = "offline"
            connected_blues[blue_id]["ws"] = None
            print(f"[-] Blue disconnected: {blue_id}")
            save_blues_data()
            
            # Notify red of update
            await notify_red_of_blues_update()
            
            # If red was controlling this blue, send error
            if red_connection:
                try:
                    await red_connection.send(json.dumps({
                        "type": "error",
                        "message": f"Blue {blue_id} went offline"
                    }))
                except:
                    pass


async def handle_red_connection(websocket, path):
    """Handle Red controller connection"""
    global red_connection
    
    if red_connection:
        print("[-] Red already connected, rejecting new connection")
        return
    
    red_connection = websocket
    print(f"[+] Red controller connected")
    
    # Send initial blues list
    await send_blues_list(websocket)
    
    try:
        while True:
            msg = await websocket.recv()
            data = json.loads(msg)
            
            msg_type = data.get("type")
            
            # Red requests blues list
            if msg_type == "request_blues":
                await send_blues_list(websocket)
            
            # Red selects a blue and sends a command
            elif msg_type == "command":
                blue_id = data.get("to")
                
                if blue_id not in connected_blues:
                    await websocket.send(json.dumps({
                        "type": "error",
                        "message": f"Blue {blue_id} not found"
                    }))
                    continue
                
                blue_info = connected_blues[blue_id]
                
                if blue_info["status"] != "online":
                    await websocket.send(json.dumps({
                        "type": "error",
                        "message": f"Blue {blue_id} is offline"
                    }))
                    continue
                
                # Forward command to blue
                try:
                    await blue_info["ws"].send(json.dumps(data))
                    print(f"[+] Forwarded command to {blue_id}: {data.get('command')}")
                except Exception as e:
                    await websocket.send(json.dumps({
                        "type": "error",
                        "message": f"Failed to send command to {blue_id}: {str(e)}"
                    }))
            
            else:
                print(f"[!] Unknown message type from red: {msg_type}")
    
    except websockets.exceptions.ConnectionClosed:
        print("[-] Red controller disconnected")
    except Exception as e:
        print(f"[-] Error in red connection: {e}")
    
    finally:
        red_connection = None


async def send_blues_list(websocket):
    """Send current list of active blues to red"""
    try:
        await websocket.send(json.dumps({
            "type": "blues_list",
            "blues": get_active_blues(),
            "timestamp": datetime.now().isoformat()
        }))
    except Exception as e:
        print(f"[-] Error sending blues list: {e}")


async def notify_red_of_blues_update():
    """Notify red that the blues list changed"""
    if red_connection:
        try:
            await send_blues_list(red_connection)
            print("[+] Notified red of blues update")
        except Exception as e:
            print(f"[-] Error notifying red: {e}")


async def main():
    """Start the relay server"""
    
    # Load persisted blues data
    load_blues_data()
    
    print(f"\n{'='*50}")
    print(f"Yellow Relay Server Started")
    print(f"Listening on {RELAY_HOST}:{RELAY_PORT}")
    print(f"{'='*50}\n")
    
    # Start server with two handlers
    # First path: /blue for blue agents
    # Second path: /red for red controller
    
    async with websockets.serve(handle_blue_connection, RELAY_HOST, RELAY_PORT, path="/blue"):
        async with websockets.serve(handle_red_connection, RELAY_HOST, RELAY_PORT, path="/red"):
            print("[+] Relay server ready")
            await asyncio.Future()  # run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[!] Relay server shutting down...")