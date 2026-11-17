from fastapi import APIRouter
from app.services import catalog_service as svc

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard")
def dashboard():
    return svc.dashboard()
