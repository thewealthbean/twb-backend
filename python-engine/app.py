from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def root():
    return {"status": "Python API running"}