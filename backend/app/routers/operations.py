from fastapi import APIRouter
from pydantic import BaseModel
from ..auth import AuthDep
from ..operations import history_status, restore

router = APIRouter(prefix="/api/looks", tags=["look history"], dependencies=[AuthDep])

class RestoreLook(BaseModel):
    checkpoint_id: int | None = None

@router.get("/history")
def history():
    return history_status()

@router.post("/restore")
def restore_look(payload: RestoreLook):
    return restore(payload.checkpoint_id)
