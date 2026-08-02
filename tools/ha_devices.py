import asyncio, json, os, websockets

TOKEN = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiIxMWJmZDFjMDM0OTA0ZTg0ODM5"
         "MzhhMWUyYWFhMjU2ZSIsImlhdCI6MTc3NDMyNDcyNSwiZXhwIjoyMDg5Njg0NzI1fQ."
         "wm61O9K3DAPJ4aj21IyOjgiKMs_rncF15AbYQpg0FxA")
URL = "ws://192.168.1.160:8123/api/websocket"

async def main():
    async with websockets.connect(URL, max_size=None) as ws:
        await ws.recv()                                   # auth_required
        await ws.send(json.dumps({"type": "auth", "access_token": TOKEN}))
        auth = json.loads(await ws.recv())
        if auth.get("type") != "auth_ok":
            raise SystemExit(f"auth failed: {auth}")
        print(f"authenticated, HA {auth.get('ha_version')}")

        out = {}
        for i, t in enumerate(["config/device_registry/list",
                               "config/entity_registry/list",
                               "config/area_registry/list"], start=1):
            await ws.send(json.dumps({"id": i, "type": t}))
            while True:
                m = json.loads(await ws.recv())
                if m.get("id") == i and m.get("type") == "result":
                    out[t.split("/")[1]] = m.get("result", [])
                    break
        json.dump(out, open("ha_registry.json", "w"), indent=1)
        for k, v in out.items():
            print(f"  {k}: {len(v)}")

asyncio.run(main())
