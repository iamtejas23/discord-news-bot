"""Periodic news publishing."""

import logging
import asyncio
from datetime import datetime, timedelta, timezone

from discord.ext import tasks

LOGGER = logging.getLogger(__name__)


class NewsScheduler:
    def __init__(self, publish, interval_minutes: int):
        self.publish = publish
        self.loop = tasks.loop(minutes=interval_minutes)(self._run)

    async def _run(self) -> None:
        try:
            await self.publish()
        except Exception:
            LOGGER.exception("Scheduled news publish failed")

    def start(self) -> None:
        if not self.loop.is_running():
            self.loop.start()
            LOGGER.info("News scheduler started")
        else:
            LOGGER.debug("News scheduler is already running")

    def is_running(self) -> bool:
        return self.loop.is_running()


class DailyDigestScheduler:
    def __init__(self, publish, hour_utc: int):
        self.publish = publish
        self.hour_utc = hour_utc
        self.loop = tasks.loop(hours=24)(self._run)
        self.loop.before_loop(self._wait_until_hour)

    async def _wait_until_hour(self) -> None:
        now = datetime.now(timezone.utc)
        target = now.replace(hour=self.hour_utc, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())

    async def _run(self) -> None:
        try:
            await self.publish()
        except Exception:
            LOGGER.exception("Daily digest publish failed")

    def start(self) -> None:
        if not self.loop.is_running():
            self.loop.start()
            LOGGER.info("Daily digest scheduler started for %02d:00 UTC", self.hour_utc)

    def is_running(self) -> bool:
        return self.loop.is_running()