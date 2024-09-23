import logging
import logging.config
import warnings

from colorlog import ColoredFormatter

__all__ = (
    "setup_logging",
    "silence_warnings",
)


def setup_logging():
    formatter = ColoredFormatter(
        "[%(cyan)s%(asctime)s%(reset)s][%(threadName)s][%(blue)s%(name)s%(reset)s][%(log_color)s%(levelname)s%(reset)s]: %(message)s",
        datefmt=None,
        reset=True,
        log_colors={
            "DEBUG": "cyan",
            "INFO": "green",
            "WARNING": "yellow",
            "ERROR": "red",
            "CRITICAL": "red",
        },
    )
    stream = logging.StreamHandler()
    stream.setLevel(logging.INFO)
    stream.setFormatter(formatter)

    logging.basicConfig(level=logging.DEBUG, handlers=[stream])


def silence_warnings():
    warnings.filterwarnings("ignore", "Named tensors")
    # TODO should i ignore these??
    warnings.filterwarnings("ignore", "The values of tensor")
    warnings.filterwarnings("ignore", "Assuming 2D input is NC")
