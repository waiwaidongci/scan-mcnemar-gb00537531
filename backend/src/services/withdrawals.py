"""财务撤回误入验收的票据：按批号和票号认定，停用受影响的报告并按原门槛重算。"""
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from src.constants.enums import Review, Status
from src.models.jobs import Delivery, Run
from src.models.source import Dataset, Withdrawal
from src.services.audit import record


def withdrawn_keys(db):
    return {(item.batch_id, item.document_id) for item in db.scalars(select(Withdrawal))}


def affecting(rows, keys):
    """数据批次中已被撤回的 (批号, 票号) 集合。撤回表只增不减，集合只会扩大。"""
    return keys & {(row['batch_id'], row['document_id']) for row in rows}


def apply(rows, affecting_keys):
    """计算前剔除撤回票。旧文件再次导入同一批次时，剔除在每次计算时重新生效。"""
    kept = [row for row in rows if (row['batch_id'], row['document_id']) not in affecting_keys]
    return kept, len(rows) - len(kept)


def _insert(db, batch_id, document_id, reason, actor):
    lookup = select(Withdrawal).where(Withdrawal.batch_id == batch_id,
                                      Withdrawal.document_id == document_id)
    existing = db.scalar(lookup)
    if existing:
        return existing, False
    item = Withdrawal(batch_id=batch_id, document_id=document_id, reason=reason or None, actor=actor)
    try:
        with db.begin_nested():
            db.add(item)
            db.flush()
    except IntegrityError:
        # 并发撤回同一张票：唯一约束生效，以先提交者为准，重复请求幂等返回。
        existing = db.scalar(lookup.with_for_update())
        if existing:
            return existing, False
        raise
    return item, True


def _supersede(db, job, item):
    """立即停用含撤回票的报告：清空结果与导出，原复核作废，按原参数重新排队。"""
    job.revision += 1
    job.status = Status.QUEUED
    job.attempt = 0
    job.lease_until = None
    job.result = None
    job.export_file = None
    job.error = None
    job.finished_at = None
    voided = job.review != Review.PENDING
    job.review = Review.PENDING
    job.review_note = None
    job.decision = None
    db.execute(delete(Delivery).where(Delivery.run_id == job.id))
    record(db, item.actor, 'analysis.superseded', job.id, revision=job.revision,
           ticket=f'{item.batch_id}/{item.document_id}', review_voided=voided)


def create(db, batch_id, document_id, reason, actor):
    item, created = _insert(db, batch_id, document_id, reason, actor)
    if not created:
        return item, False, []
    # 同一事务内认定撤回并停用受影响报告，行锁与后台写入互斥，并发撤回互不漏算。
    runs = db.scalars(select(Run).where(Run.status.in_([Status.RUNNING, Status.COMPLETE]))
                      .with_for_update()).all()
    rows_by_dataset = {dataset.id: dataset.rows for dataset in db.scalars(select(Dataset))}
    affected = []
    for job in runs:
        if (item.batch_id, item.document_id) in {(row['batch_id'], row['document_id'])
                                                 for row in rows_by_dataset.get(job.dataset_id, [])}:
            _supersede(db, job, item)
            affected.append(job.id)
    record(db, actor, 'ticket.withdrawn', f'{item.batch_id}/{item.document_id}',
           reason=item.reason, affected_runs=affected)
    return item, True, affected
