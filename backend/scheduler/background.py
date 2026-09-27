import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from backend.services.docker_service import (
    sample_cpu,
    sample_gpu,
    sample_container_stats,
    sample_profile_disk_usage,
)
from backend.services.automation_service import (
    heartbeat_scheduler_leases,
    dispatch_pending_queue,
)

scheduler = AsyncIOScheduler()

def setup_background_tasks():
    # Initial samples
    sample_cpu()
    sample_gpu()
    sample_container_stats()
    sample_profile_disk_usage()

    # Periodic jobs
    scheduler.add_job(sample_cpu, "interval", seconds=2, id="sample_cpu", replace_existing=True)
    scheduler.add_job(sample_gpu, "interval", seconds=2, id="sample_gpu", replace_existing=True)
    scheduler.add_job(sample_container_stats, "interval", seconds=4, id="sample_container_stats", replace_existing=True)
    scheduler.add_job(sample_profile_disk_usage, "interval", seconds=10, id="sample_profile_disk_usage", replace_existing=True)
    scheduler.add_job(heartbeat_scheduler_leases, "interval", seconds=15, id="heartbeat_scheduler_leases", replace_existing=True)
    scheduler.add_job(dispatch_pending_queue, "interval", seconds=25, id="dispatch_pending_queue", replace_existing=True)

    scheduler.start()

def shutdown_background_tasks():
    if scheduler.running:
        scheduler.shutdown(wait=False)
