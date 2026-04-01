"""
api.py
======
FastAPI REST API for the F&O Trader Behavioral Engine.

Endpoints:
  POST /analyse/single      Upload one PnL Excel → returns full behavioral report
  POST /analyse/multiple    Upload up to 12 PnL files → returns multi-period report
  GET  /health              Service health check

Run locally:
  pip install fastapi uvicorn python-multipart
  uvicorn behavioral_engine.api:app --reload --port 8000

Production:
  uvicorn behavioral_engine.api:app --host 0.0.0.0 --port 8000 --workers 4
"""

from __future__ import annotations

import os
import uuid
import tempfile
import traceback
from typing import Optional

# FastAPI imports — install with: pip install fastapi uvicorn python-multipart
try:
    from fastapi import FastAPI, File, UploadFile, HTTPException, Query
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    print("FastAPI not installed. Run: pip install fastapi uvicorn python-multipart")

from analyzer import BehavioralEngine, analyse_file, analyse_files


# ─── create app ──────────────────────────────────────────────────────────────
if FASTAPI_AVAILABLE:
    app = FastAPI(
        title="F&O Trader Behavioral Engine",
        description=(
            "Upload Zerodha F&O PnL Excel statements and receive a "
            "detailed behavioral analysis identifying where and why you are losing money."
        ),
        version="1.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],       # tighten in production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── helpers ───────────────────────────────────────────────────────────────

    ALLOWED_EXTENSIONS = {".xlsx", ".xls"}
    MAX_FILE_SIZE_MB = 10

    def _validate_and_save(upload: UploadFile) -> str:
        """
        Validate the uploaded file (type, size) and save to a temp file.
        Returns the temp file path.
        Raises HTTPException on validation failure.
        """
        ext = os.path.splitext(upload.filename or "")[-1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file type '{ext}'. Only .xlsx and .xls are supported.",
            )

        content = upload.file.read()
        size_mb = len(content) / (1024 * 1024)
        if size_mb > MAX_FILE_SIZE_MB:
            raise HTTPException(
                status_code=413,
                detail=f"File too large ({size_mb:.1f} MB). Maximum allowed: {MAX_FILE_SIZE_MB} MB.",
            )

        # write to a named temp file so pandas/openpyxl can read it
        suffix = ext
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.write(content)
        tmp.flush()
        tmp.close()
        return tmp.name

    def _cleanup(paths: list[str]):
        for p in paths:
            try:
                os.unlink(p)
            except Exception:
                pass

    # ── endpoints ─────────────────────────────────────────────────────────────

    @app.get("/health")
    def health_check():
        return {"status": "ok", "service": "F&O Behavioral Engine", "version": "1.0.0"}


    @app.post("/analyse/single")
    async def analyse_single(
        file: UploadFile = File(..., description="Zerodha F&O PnL Excel file"),
        enabled_logics: Optional[str] = Query(
            default=None,
            description="Comma-separated logic IDs to run, e.g. 'L1,L2,L3'. Default: all."
        ),
    ):
        """
        Analyse a single Zerodha F&O PnL Excel file.

        Returns a complete behavioral report with:
        - Summary statistics (win rate, RR ratio, charges, etc.)
        - Health score (0–100) and grade (A–F)
        - All triggered behavioral mistakes ranked by severity
        - Detailed evidence and recommendations per mistake
        - Per-logic metrics
        """
        tmp_path = None
        try:
            tmp_path = _validate_and_save(file)

            logic_ids = None
            if enabled_logics:
                logic_ids = [x.strip().upper() for x in enabled_logics.split(",")]

            engine = BehavioralEngine(enabled_logics=logic_ids)
            report = engine.analyse(tmp_path)

            return JSONResponse(content=_make_serialisable(report))

        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except Exception:
            raise HTTPException(
                status_code=500,
                detail=f"Internal error during analysis: {traceback.format_exc()}"
            )
        finally:
            if tmp_path:
                _cleanup([tmp_path])


    @app.post("/analyse/multiple")
    async def analyse_multiple(
        files: list[UploadFile] = File(..., description="Up to 12 Zerodha F&O PnL Excel files"),
    ):
        """
        Analyse multiple PnL files (e.g. separate monthly statements).

        Returns individual period reports plus a cross-period trend analysis,
        identifying whether behavioral mistakes are improving or worsening over time.
        """
        if len(files) > 12:
            raise HTTPException(status_code=400, detail="Maximum 12 files per request.")

        tmp_paths = []
        try:
            for f in files:
                tmp_paths.append(_validate_and_save(f))

            engine = BehavioralEngine()
            report = engine.analyse_multiple(tmp_paths)

            return JSONResponse(content=_make_serialisable(report))

        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except Exception:
            raise HTTPException(
                status_code=500,
                detail=f"Internal error during analysis: {traceback.format_exc()}"
            )
        finally:
            _cleanup(tmp_paths)


def _make_serialisable(obj):
    """Recursively convert numpy / pandas types to JSON-native Python types."""
    import numpy as np

    if isinstance(obj, dict):
        return {k: _make_serialisable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_make_serialisable(v) for v in obj]
    elif isinstance(obj, (np.integer,)):
        return int(obj)
    elif isinstance(obj, (np.floating,)):
        return float(obj)
    elif isinstance(obj, (np.bool_,)):
        return bool(obj)
    elif isinstance(obj, float) and (obj != obj):    # NaN
        return None
    elif isinstance(obj, float) and obj == float("inf"):
        return 999999.0
    elif isinstance(obj, float) and obj == float("-inf"):
        return -999999.0
    else:
        return obj
