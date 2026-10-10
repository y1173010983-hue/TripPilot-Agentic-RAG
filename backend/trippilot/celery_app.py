"""生产队列适配：Celery Beat 定期投递数据库待处理工作项，数据库保证领取幂等。"""
import logging

from celery import Celery
from celery.signals import worker_ready
from sqlalchemy import select
from .config import get_settings
from .database import session_factory
from .models import WorkItem
from .worker import run_one


logger = logging.getLogger(__name__)

celery = Celery("trippilot", broker=get_settings().redis_url)
celery.conf.update(task_acks_late=True, worker_prefetch_multiplier=1,
                   beat_schedule={"dispatch-durable-work": {"task": "trip.dispatch", "schedule": 3.0}})


@celery.task(name="trip.warm_embedding")
def warm_local_embedding():
    """作为普通队列任务预加载本地向量模型，避免在 Celery 启动回调中阻塞初始化。"""
    if get_settings().embedding_backend != "local":
        return
    from .retrieval.module1_local_embedding import create_local_embeddings

    create_local_embeddings(["TripPilot 本地向量模型启动预热"])
    logger.warning("local_embedding_model_ready")


@worker_ready.connect
def schedule_embedding_warmup(**_kwargs):
    """worker 就绪后只投递预热任务，回调本身立即返回。"""
    if get_settings().embedding_backend == "local":
        warm_local_embedding.delay()


@celery.task(name="trip.execute")
def execute_work(job_id):
    run_one(job_id)


@celery.task(name="trip.dispatch")
def dispatch_pending_work():
    with session_factory() as db:
        ids = list(db.scalars(select(WorkItem.id).where(WorkItem.status == "queued").limit(100)))
    for jid in ids:
        execute_work.delay(jid)
