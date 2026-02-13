import asyncio
import json
import websockets
import requests

async def test():
    uri = "ws://localhost:8000/api/events"
    events = []
    
    async with websockets.connect(uri) as ws:
        msg = await ws.recv()
        print("Connected:", json.loads(msg))
        
        async def recv():
            try:
                while True:
                    m = await ws.recv()
                    events.append(json.loads(m))
                    print("Event:", json.loads(m))
            except: pass
        
        t = asyncio.create_task(recv())
        
        resp = requests.post("http://localhost:8000/api/chat", json={"message": "review PR 171 in dvnuo/engineering-flow-platform"}, timeout=60)
        print("Chat response received")
        
        await asyncio.sleep(10)
        t.cancel()
        
        print(f"Total events: {len(events)}")
        for e in events:
            print(f"  - {e.get('type')}")

asyncio.run(test())
