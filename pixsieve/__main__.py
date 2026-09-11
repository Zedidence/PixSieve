"""
Allow running the package with: python -m pixsieve

By default, launches the GUI. Use 'cli' subcommand for command-line interface.

Examples:
    python -m pixsieve                     # Launch GUI
    python -m pixsieve gui                 # Launch GUI (explicit)
    python -m pixsieve cli                 # Launch CLI (interactive)
    python -m pixsieve cli /path/to/photos # CLI with path
"""

import sys


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'cli':
        # Remove 'cli' from argv so argparse in cli.py doesn't see it
        sys.argv.pop(1)
        from .cli import main as cli_main
        cli_main()
    elif len(sys.argv) > 1 and sys.argv[1] == 'gui':
        # Remove 'gui' from argv
        sys.argv.pop(1)
        from .app import main as gui_main
        gui_main()
    else:
        # Default to GUI
        from .app import main as gui_main
        gui_main()


if __name__ == '__main__':
    main()
