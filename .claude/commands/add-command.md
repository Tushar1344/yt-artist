Add a new CLI subcommand to yt-artist: $ARGUMENTS

Steps:
1. Read src/yt_artist/cli.py — understand existing command pattern
2. Add argparse subparser in main() following existing patterns
3. Create _cmd_* handler: signature (args, storage, data_dir)
4. Add _hint() calls for next-step guidance
5. Write test in tests/test_cli.py following existing patterns (mock external calls)
6. Run `python -m pytest tests/test_cli.py -v`

Patterns: subparser with help, set_defaults(func=_cmd_name), handler docstring, _hint() after success, SystemExit for user errors.
