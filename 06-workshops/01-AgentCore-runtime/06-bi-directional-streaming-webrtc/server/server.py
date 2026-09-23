import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse

app = FastAPI()


@app.get("/")
async def serve_index():
    return FileResponse("index.html")


if __name__ == "__main__":
    uvicorn.run(app, host="localhost", port=7860)
