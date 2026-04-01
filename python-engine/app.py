from fastapi import FastAPI, UploadFile, File
import pandas as pd

app = FastAPI()

@app.get("/")
def root():
    return {"status": "Python API running"}

@app.post("/analyse/single")
async def analyze(file: UploadFile = File(...)):
    try:
        df = pd.read_excel(file.file)

        return {
            "status": "success",
            "rows": len(df),
            "columns": list(df.columns)
        }

    except Exception as e:
        return {
            "error": "Failed to process file",
            "details": str(e)
        }