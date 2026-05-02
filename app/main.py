from __future__ import annotations

import time

from app.config import Config
from app.health import HealthChecker
from app.input_source import ConsoleInputSource, GPIOInputSource
from app.logger_setup import setup_logger
from app.recorder import Recorder
from app.state_machine import RecorderStateMachine
from app.status_led import ConsoleStatusIndicator
from app.storage import StorageManager


def main() -> None:
    config = Config()
    logger = setup_logger(config.logs_dir)

    health = HealthChecker(config)
    storage = StorageManager(config)
    recorder = Recorder(
        sample_rate=config.sample_rate,
        channels=config.channels,
        sample_width_bytes=config.sample_width_bytes,
        chunk_duration_seconds=config.chunk_duration_seconds,
    )
    status_indicator = ConsoleStatusIndicator()

    if config.use_gpio_input:
        input_source = GPIOInputSource(
            button_gpio=config.button_gpio_pin,
            shutdown_hold_seconds=config.shutdown_hold_seconds,
        )
    else:
        input_source = ConsoleInputSource()

    machine = RecorderStateMachine(
        config=config,
        logger=logger,
        health=health,
        storage=storage,
        recorder=recorder,
        status_indicator=status_indicator,
    )

    machine.boot()
    input_source.start()

    if config.use_gpio_input:
        print(f"GPIO button input active on BCM GPIO{config.button_gpio_pin}")
        print(
            f"Short press = record toggle, hold {config.shutdown_hold_seconds:.0f}s = shutdown"
        )
    else:
        print("Commands: [r] button press, [x] recover from error, [s] shutdown, [q] quit")

    try:
        while True:
            try:
                machine.poll_runtime_health()
                machine.poll_error_recovery()
            except Exception as exc:
                logger.exception("Unhandled polling exception: %s", exc)

            cmd = input_source.get_next_command()

            if cmd == "q":
                print("Exiting.")
                break
            elif cmd == "r":
                machine.handle_button_press()
            elif cmd == "x":
                machine.try_recover()
            elif cmd == "s":
                shutdown_started = machine.request_shutdown()
                if shutdown_started:
                    print("Shutdown completed. It is now safe to remove storage or power down.")
                    break
                else:
                    print("Shutdown rejected in current state.")
            elif cmd is not None:
                print("Unknown command.")

            time.sleep(config.poll_interval_seconds)

    except KeyboardInterrupt:
        print("\nExiting.")
    finally:
        input_source.stop()


if __name__ == "__main__":
    main()