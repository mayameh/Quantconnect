import asyncio
import logging
import os
import signal
import sys
from dataclasses import dataclass

from ib_insync import IB


@dataclass
class Settings:
    host: str
    port: int
    client_id: int
    account: str
    app_env: str
    log_level: str


def load_settings() -> Settings:
    return Settings(
        host=os.getenv("IB_HOST", "127.0.0.1"),
        port=int(os.getenv("IB_PORT", "4002")),
        client_id=int(os.getenv("IB_CLIENT_ID", "US5014500")),
        account=os.getenv("IB_ACCOUNT", ""),
        app_env=os.getenv("APP_ENV", "paper"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("/home/ibtrade/trading/logs/app.log"),
        ],
    )


class TradingApp:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.log = logging.getLogger("trading_app")
        self.ib = IB()
        self._stop = False

    async def connect_loop(self) -> None:
        retry_delay = 5

        while not self._stop:
            try:
                self.log.info(
                    "Connecting to IB host=%s port=%s clientId=%s env=%s",
                    self.settings.host,
                    self.settings.port,
                    self.settings.client_id,
                    self.settings.app_env,
                )

                await self.ib.connectAsync(
                    host=self.settings.host,
                    port=self.settings.port,
                    clientId=self.settings.client_id,
                    timeout=10,
                    readonly=False,
                    account=self.settings.account or "",
                )

                self.log.info("Connected to IB")

                self.ib.disconnectedEvent += self.on_disconnected

                await self.on_connected()
                await self.run_forever()

            except Exception as exc:
                self.log.exception("Connection/run failure: %s", exc)

            if self._stop:
                break

            self.log.warning("Disconnected. Retrying in %s seconds...", retry_delay)
            await asyncio.sleep(retry_delay)

    async def on_connected(self) -> None:
        self.log.info("Requesting managed accounts...")
        accounts = self.ib.managedAccounts()
        self.log.info("Managed accounts: %s", accounts)

        self.log.info("Requesting positions...")
        positions = self.ib.positions()
        for p in positions:
            self.log.info(
                "Position account=%s symbol=%s qty=%s avgCost=%s",
                p.account,
                getattr(p.contract, "symbol", "?"),
                p.position,
                p.avgCost,
            )

        self.log.info("Requesting open orders...")
        open_trades = self.ib.openTrades()
        for t in open_trades:
            self.log.info("Open trade: %s", t)

    async def run_forever(self) -> None:
        while not self._stop and self.ib.isConnected():
            self.log.info("Heartbeat: connected=%s", self.ib.isConnected())
            await asyncio.sleep(30)

    def on_disconnected(self) -> None:
        self.log.warning("IB disconnected event received")

    async def shutdown(self) -> None:
        self._stop = True
        if self.ib.isConnected():
            self.log.info("Disconnecting cleanly from IB")
            self.ib.disconnect()


async def amain() -> int:
    settings = load_settings()
    configure_logging(settings.log_level)

    app = TradingApp(settings)
    loop = asyncio.get_running_loop()

    stop_event = asyncio.Event()

    def _handle_signal(*_args):
        logging.getLogger("trading_app").info("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    task = asyncio.create_task(app.connect_loop())

    await stop_event.wait()
    await app.shutdown()

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))