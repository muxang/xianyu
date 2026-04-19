from fastapi import FastAPI

app = FastAPI(title="Xianyu AI Service")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
