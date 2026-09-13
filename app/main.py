from fastapi import FastAPI

app = FastAPI(title="Hookdaemon", version="0.1.0")


@app.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}
