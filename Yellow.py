import asyncio
import websockets
import json

clients = {}  # id → websocket

async def handler(ws):
    try:
        # First message = registration
        msg = await ws.recv()
        data = json.loads(msg)

        client_id = data["id"]
        role = data["role"]

        clients[client_id] = ws
        print(f"{client_id} ({role}) connected")

        async for message in ws:
            data = json.loads(message)

            target = data.get("target")

            if target in clients:
                await clients[target].send(message)
                print(f"{client_id} → {target}: {data.get('command')}")
            else:
                print(f"Target {target} not found")

    except:
        pass
    finally:
        # cleanup
        for k, v in list(clients.items()):
            if v == ws:
                print(f"{k} disconnected")
                del clients[k]

async def main():
    async with websockets.serve(handler, "0.0.0.0", 8765):
        print("Relay running on port 8765")
        await asyncio.Future()

asyncio.run(main())