import logging
from datetime import date, timedelta
from sqlalchemy import and_, or_, select
from src.config.database import Session
from src.constants.enums import Status
from src.domain.analysis import analyze
from src.domain.spec import SPEC
from src.models.jobs import Delivery, Run, Schedule
from src.models.source import Dataset
from src.services import withdrawals
from src.services.audit import record
from src.utils.exports import write_report
from src.utils.time import now

log = logging.getLogger(__name__)


def materialize_due():
    if not SPEC.get('scheduled'):
        return
    stamp = now()
    with Session.begin() as db:
        schedules = db.scalars(select(Schedule).where(Schedule.enabled.is_(True), Schedule.next_at <= stamp)
                                .order_by(Schedule.next_at).limit(20).with_for_update(skip_locked=True))
        for item in schedules:
            due = item.next_at
            dataset_id = item.dataset_id
            params = dict(item.params)
            if item.repeat_daily:
                elapsed_days = (stamp - due).days
                due += timedelta(days=elapsed_days)
                period_key = SPEC['schedule_date_param']
                report_day = date.fromisoformat(params[period_key]) + timedelta(days=elapsed_days)
                params[period_key] = report_day.isoformat()
                item.params = {**params, period_key: (report_day + timedelta(days=1)).isoformat()}
                dataset_id = db.scalar(select(Dataset.id).order_by(Dataset.id.desc()).limit(1)) or dataset_id
                item.next_at = due + timedelta(days=1)
            else:
                item.enabled = False
            db.add(Run(dataset_id=dataset_id, schedule_id=item.id, scheduled_at=due,
                       name=item.name, params=params, author=item.author))


def claim():
    stamp = now()
    with Session.begin() as db:
        query = select(Run).where(or_(Run.status == Status.QUEUED,
                                      and_(Run.status == Status.RUNNING, Run.lease_until < stamp)))
        job = db.scalar(query.order_by(Run.id).limit(1).with_for_update(skip_locked=True))
        if job is None:
            return None
        if job.attempt >= 3:
            job.status = Status.FAILED
            job.error = '后台进程多次中断，请发起新的运行'
            job.finished_at = stamp
            record(db, 'worker', 'analysis.failed', job.id, reason=job.error)
            return None
        job.status = Status.RUNNING
        job.attempt += 1
        job.lease_until = stamp + timedelta(minutes=3)
        records = db.get(Dataset, job.dataset_id).rows
        # 撤回票在每次领取任务时剔除，旧文件重新导入也不会把撤回票算回去。
        affecting = withdrawals.affecting(records, withdrawals.withdrawn_keys(db))
        kept, excluded = withdrawals.apply(records, affecting)
        return job.id, job.attempt, job.revision, kept, job.params, excluded, affecting


def _annotate_withdrawal(result, excluded, affecting):
    result['note'] += f'财务已撤回 {excluded} 张误入验收的票据，本次计算与导出均未计入。'
    result['metrics'].append({'label': '财务撤回剔除', 'value': excluded,
                              'detail': '按扫描批次与票据编号认定'})
    result['tables'].append({
        'title': '财务撤回票据（未计入）',
        'columns': [{'key': 'batch_id', 'label': '扫描批次'}, {'key': 'document_id', 'label': '票据编号'}],
        'rows': [{'batch_id': batch_id, 'document_id': document_id}
                 for batch_id, document_id in sorted(affecting)],
    })


def execute(lease):
    job_id, attempt, revision, rows, params, excluded, affecting = lease
    result = export = failure = None
    try:
        result = analyze(rows, params)
        if excluded:
            _annotate_withdrawal(result, excluded, affecting)
        export = write_report(job_id, revision, result)
    except ValueError as error:
        failure = str(error)[:500]
    except Exception:
        log.exception('Analysis %s failed', job_id)
        failure = '计算失败，请检查字段口径后重试'
    with Session.begin() as db:
        job = db.get(Run, job_id, with_for_update=True)
        if job.attempt != attempt or job.revision != revision or job.status != Status.RUNNING:
            return
        if not failure:
            current = withdrawals.affecting(db.get(Dataset, job.dataset_id).rows,
                                            withdrawals.withdrawn_keys(db))
            if current != affecting:
                # 计算期间出现新的撤回：结果已过时，重新排队按最新撤回集重算。
                job.status = Status.QUEUED
                job.lease_until = None
                record(db, 'worker', 'analysis.requeued', job.id, revision=revision)
                return
        job.lease_until = None
        job.finished_at = now()
        job.status = Status.FAILED if failure else Status.COMPLETE
        job.result = None if failure else result
        job.export_file = export
        job.error = failure
        record(db, 'worker', f'analysis.{job.status}', job.id, attempt=attempt,
               revision=revision, reason=failure)
        if job.schedule_id:
            plan = db.get(Schedule, job.schedule_id)
            ready = not failure and result.get('ready', True)
            detail = '已保存 CSV 到站内收件箱' if ready else (failure or result['headline'])
            receipt = Delivery(run_id=job.id, recipient=plan.recipient, state='delivered' if ready else 'blocked', detail=detail[:500])
            db.add(receipt)
            record(db, 'worker', f'delivery.{receipt.state}', job.id, recipient=plan.recipient)
