import subprocess
import sys
import shutil
from collections.abc import Sequence
from argparse import ArgumentParser, Namespace
from pathlib import Path
from typing import (Any, Callable, Dict, Iterable, List, NamedTuple, Optional,
                    TextIO, Union)

# --===========================================--#
# ----------------- Helper ----------------------#
# --===========================================--#
# -- source: https://github.com/mivade/arghelp --#
# --===========================================--#


class Arg(NamedTuple):
    """A single command-line argument."""

    name_or_flags: Iterable[str]  # name or flags of the argument
    kwargs: Dict[str, Any]  # keyword arguments to pass to `add_argument`


class Group(NamedTuple):
    """A mutually exclusive group of arguments."""

    args: Iterable[Arg]  # the arguments
    required: bool = False  # at least one option must be given


def arg(*name_or_flags, **kwargs) -> Arg:
    """
    Convenience function to properly format arguments to pass to the
    subcommand decorator.
    """
    return Arg(list(name_or_flags), kwargs)


class CLI(object):
    """
    A command-line application builder.

    :param args: Common command-line arguments
    """

    def __init__(self, args: Optional[Iterable[Arg]] = None):
        self.argparser = ArgumentParser()
        self.subparsers = None
        self._root_command = None  # type: Optional[Callable]

        if args is not None:
            for arg in args:
                self.argparser.add_argument(*arg.name_or_flags, **arg.kwargs)

    @property
    def help(self):
        """Returns the help message"""
        return self.argparser.print_help()

    @property
    def usage(self):
        """Returns the usage message"""
        return self.argparser.print_usage()

    @property
    def subcommand_count(self) -> int:
        """Returns the number of registered subcommands."""
        if self.subparsers is not None:
            return len(self.subparsers.choices)

        return 0

    def _add_arguments(
        self, parser: ArgumentParser, args: Iterable[Union[Arg, Group]]
    ) -> None:
        for arg in args:
            if isinstance(arg, Arg):
                parser.add_argument(*arg.name_or_flags, **arg.kwargs)
            elif isinstance(arg, Group):
                group = parser.add_mutually_exclusive_group(required=arg.required)

                for garg in arg.args:
                    group.add_argument(*garg.name_or_flags, **garg.kwargs)
            else:
                raise ValueError(f"Invalid type: {type(arg)}")

    def subcommand(self, args: Optional[Iterable[Union[Arg, Group]]] = None):
        """
        Decorator to define a new subcommand in a sanity-preserving way.

        Usage example::

            cli = CLI()

            @cli.subcommand([
                arg("-d", help="Enable debug mode", action="store_true"),
            ])
            def subcommand(args):
                print(args)

        Then on the command line::

            $ python cli.py subcommand -d

        Mutually-exclusive arguments can be expressed using a :class:`Group` of
        arguments::

            @cli.subcommand(
                [
                    arg("--verbose", "-v", action="store_true"),
                    Group(
                        [
                            arg("-x", action="store_true", help="x mode"),
                            arg("-y", action="store_true", help="y mode"),
                        ],
                        required=True,
                    ),
                ]
            )
            def required(args):
                print(args)
        """
        if self.subparsers is None:
            self.subparsers = self.argparser.add_subparsers(dest="subcommand")

        def decorator(func):
            name = func.__name__.replace("_", "-")
            parser = self.subparsers.add_parser(name, description=func.__doc__)

            if args is not None:
                self._add_arguments(parser, args)

            parser.set_defaults(_default_func=func)

        return decorator

    def root_command(self, args: Optional[Iterable[Arg]] = None):
        """
        Decorator to define the default action to take if no subcommands
        are given. The action must be a function taking a single argument which
        is the :class:`Namespace` object resulting from parsed options.

        :param args: Additional arguments to supply to the root command. Note
            that this is in addition to any common arguments supplied upon
            instantiation.
        """

        def decorator(func):
            if self._root_command is not None:
                raise RuntimeError("Only one root command can be defined")

            if args is not None:
                self._add_arguments(self.argparser, args)

            self._root_command = func

        return decorator

    def parse_args(
        self, args: Optional[List[str]] = None, namespace: Optional[Namespace] = None
    ) -> Namespace:
        """Parse and return command line arguments."""
        return self.argparser.parse_args(args, namespace)

    def run(self, args: Optional[List[str]] = None) -> None:
        """Parse command line arguments and run a subcommand."""
        args = self.parse_args(args=args)

        if self.subparsers is None:
            self._root_command(args)
            return

        if args.subcommand is None:
            if self._root_command is not None:
                self._root_command(args)
            else:
                self.argparser.print_help()
        else:
            args._default_func(args)


# -----------#
# -- Utils --#
# -----------#


def run_cmd(
    command: Sequence[str],
    *,
    check: bool = True,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    logger.info(f"executing: {' '.join(command)}...")

    try:
        return subprocess.run(
            command,
            check=check,
            capture_output=capture_output,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        logger.error(
            f"command failed with exit code "
            f"{error.returncode}: {' '.join(command)}"
        )
        raise
    except FileNotFoundError as error:
        logger.error(f"command not found: {command[0]}: {error}")
        raise


# ------------#
# -- Logger --#
# ------------#


class Logger(object):

    def __init__(self) -> None:
        self.ME = FILE_PATH.name

    def log(
        self,
        msg: str,
        prefix: str = " ",
        postfix: str = " ",
        out: TextIO = sys.stdout,
    ):
        """Print the given log message"""
        print(f"{self.ME}:{prefix}{msg}{postfix}", file=out)

    def error(self, msg, lineno: Optional[int] = None):
        """Print error message"""
        prefix = f"{lineno}: error: " if lineno else " error: "
        self.log(msg, prefix=prefix, out=sys.stderr)

    def warn(self, msg: str, lineno: Optional[int] = None):
        """Print warning message"""
        prefix = f"{lineno}: warning: " if lineno else " warning: "
        self.log(msg, prefix=prefix, out=sys.stderr)

    def info(self, msg: str) -> None:
        """Print info message"""
        self.log(msg)

    def openning(self) -> None:
        print("==============BEGIN configuring Medagg project==============")

    def closing(self) -> None:
        print("==============END configuring Medagg project==============")


# --------------#
# -- Handlers --#
# --------------#


def docker_build_handler(
    clean: bool,
    run_tests: bool,
) -> bool:
    logger.openning()

    compose_file = FILE_PATH.parent / "docker-compose.yml"

    if not compose_file.is_file():
        logger.error("could not find docker-compose.yml")
        return False

    project_name = FILE_PATH.parent.name

    compose = (
        "docker",
        "compose",
        "-f",
        str(compose_file),
        "-p",
        project_name,
    )

    if clean and DB_PATH.exists():
        logger.info("removing local database...")

        # Make sure that the db container is stopped
        run_cmd(
            (*compose, "down", "--remove-orphans"),
            check=False,
        )

        shutil.rmtree(str(DB_PATH))

    try:
        logger.info("starting database...")
        run_cmd(
            (
                *compose,
                "up",
                "--detach",
                "--wait",
                "db",
                "redis"
            )
        )

        logger.info("running backend migrations...")
        run_cmd(
            (
                *compose,
                "run",
                "--rm",
                "--build",
                "--no-deps",
                "migrate",
            )
        )

        logger.info("starting celery workers...")
        run_cmd(
            (
                *compose,
                "up",
                "--detach",
                "--build",
                "--wait",
                "celery-worker",
            )
        )

        if run_tests:
            logger.info("running backend verification...")
            run_cmd(
                (
                    *compose,
                    "--profile",
                    "test",
                    "run",
                    "--rm",
                    "--build",
                    "--no-deps",
                    "tests",
                )
            )

        logger.info("starting application services...")
        run_cmd(
            (
                *compose,
                "up",
                "--detach",
                "--build",
                "--wait",
                "medagg-app",
                "adminer",
            )
        )

    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.error("configuration failed; stopping containers")

        run_cmd(
            (
                *compose,
                "logs",
                "--no-color",
                "--timestamps",
            ),
            check=False,
        )

        run_cmd(
            (
                *compose,
                "down",
                "--remove-orphans",
            ),
            check=False,
        )

        return False

    logger.info("configuration completed; containers are running")
    logger.closing()
    return True


def local_build_handler(clean: Optional[bool]) -> bool:
    logger.warn(
        """local builds are not supported yet!
Use recommended options instead (run with `-h` flag to see the options)."""
    )
    return True


# -----------#
# -- Begin --#
# -----------#

FILE_PATH = Path(__file__)
DB_PATH = FILE_PATH.parent.joinpath("db/data")

logger = Logger()

cli = CLI(
    [
        arg(
            "-t",
            "--test",
            action="store_true",
            help="run checks and tests before starting the application",
        ),
        arg(
            "-c",
            "--clean",
            action="store_true",
            help="remove local database if present",
        ),
        arg(
            "-d",
            "--docker",
            action="store_true",
            help="configure Medagg project using Docker <- (recommended)",
        ),
        arg(
            "-l",
            "--local",
            action="store_true",
            help="configure Medagg project locally",
        ),
    ]
)


@cli.root_command()
def root(args):
    ret = True

    # General setup
    logger.info("pulling project libraries...")
    run_cmd(("git", "submodule", "update", "--init"))

    # Handle args/opts
    if args.docker:
        ret = docker_build_handler(
            clean=args.clean,
            run_tests=args.test,
        )
    elif args.local:
        ret = local_build_handler(args.clean)

    if not ret:
        logger.error("failed to run configure script")
        raise SystemExit(1)


if __name__ == "__main__":
    cli.run()
