from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from src.config.database import connection
from src.constants.enums import Role
from src.controllers.auth import allow, current
from src.models.identity import User
from src.models.source import Withdrawal
from src.schemas.requests import WithdrawalRequest
from src.services import withdrawals
from src.utils import views
from src.utils.wakeup import wake

router = APIRouter()


@router.get('/withdrawals')
def index(db: Session = Depends(connection), actor: User = Depends(current)):
    items = db.scalars(select(Withdrawal).order_by(Withdrawal.id.desc()).limit(200))
    return [views.withdrawal(item) for item in items]


@router.post('/withdrawals', status_code=201)
def create(body: WithdrawalRequest, db: Session = Depends(connection),
           actor: User = Depends(allow(Role.ADMIN))):
    item, created, affected = withdrawals.create(db, body.batch_id, body.document_id,
                                                 body.reason, actor.username)
    db.commit()
    if affected:
        wake()
    return {**views.withdrawal(item), 'created': created, 'affected_runs': affected}
