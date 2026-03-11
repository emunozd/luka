from fastapi import FastAPI

from app.routers import auth, facturas, gastos, reportes

app = FastAPI(title="LUKA API", version="1.0.0")

app.include_router(auth.router)
app.include_router(gastos.router)
app.include_router(facturas.router)
app.include_router(reportes.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "luka-api"}
