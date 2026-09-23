from fastapi import FastAPI

app = FastAPI()

items: list[dict] = []


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/items")
def list_items():
    return items


@app.post("/items")
def create_item(item: dict):
    items.append(item)
    return item
