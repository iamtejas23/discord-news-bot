"""Periodic news publishing."""

import logging

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