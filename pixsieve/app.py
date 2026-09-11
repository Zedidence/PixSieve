#!/usr/bin/env python3
"""
PixSieve - GUI Application
========================================
A web-based interface for reviewing and managing duplicate images.

Run with: python -m pixsieve.app
Or: python pixsieve/app.py

Options:
    -q, --quiet     Quiet mode - suppress all output except errors
    -v, --verbose   Verbose mode - show all Flask request logs
    -p, --port      Port to run on (default: 5000)
    --no-browser    Don't auto-open browser

Author: Zach
"""

import argparse
import os
import sys
import atexit
import webbrowser
import threading
import logging
from typing import Optional

from flask import Flask
from flasgger import Swagger

from .api import api, operations_bp
from .state import scan_state

# Swagger / OpenAPI configuration
_SWAGGER_CONFIG = {
    'title': 'PixSieve API',
    'uiversion': 3,
    'openapi': '3.0.2',
    'specs': [
        {
            'endpoint': 'apispec',
            'route': '/apispec.json',
            'rule_filter': lambda rule: rule.rule.startswith('/api/'),
            'model_filter': lambda tag: True,
        }
    ],
    'static_url_path': '/flasgger_static',
    'swagger_ui': True,
    'specs_route': '/docs/',
    'headers': [],
}

_SWAGGER_TEMPLATE = {
    'info': {
        'title': 'PixSieve API',
        'description': (
            'REST API for PixSieve — a web-based duplicate image finder. '
            'Interactive docs are available at <a href="/docs/">/docs/</a>.'
        ),
        'version': '1.0.0',
    },
    'servers': [{'url': 'http://localhost:5000', 'description': 'Local dev server'}],
}


# Logging levels
LOG_QUIET = 0    # No output except errors
LOG_MINIMAL = 1  # Startup info only (default)
LOG_VERBOSE = 2  # All Flask request logs


def configure_scan_logging(log_level: int = LOG_MINIMAL, log_file: Optional[str] = None) -> None:
    """
    Configure logging for pixsieve's own loggers (scanner, api.orchestrator,
    database, etc.) — separate from werkzeug's HTTP request logging, which
    is controlled independently in create_app()/main().

    Without this, GUI mode had NO log handler at all for the scan pipeline:
    every logger.info()/logger.warning() call in the scanner and database
    modules was silently dropped, which is part of why a stalled scan gave
    zero visibility into what it was doing.

    Args:
        log_level: LOG_QUIET / LOG_MINIMAL / LOG_VERBOSE
        log_file: Optional path to also write logs to, for scans left
            running unattended.
    """
    level = {
        LOG_QUIET: logging.WARNING,
        LOG_MINIMAL: logging.INFO,
        LOG_VERBOSE: logging.DEBUG,
    }.get(log_level, logging.INFO)

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding='utf-8'))

    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(name)s - %(message)s',
        datefmt='%H:%M:%S',
    )

    # Scope to the 'pixsieve' logger namespace (all scanner/database/api
    # loggers are children of it) and stop propagation to root so this
    # doesn't interact with werkzeug's separately-configured root handlers.
    pixsieve_logger = logging.getLogger('pixsieve')
    pixsieve_logger.setLevel(level)
    pixsieve_logger.propagate = False
    pixsieve_logger.handlers.clear()
    for handler in handlers:
        handler.setFormatter(formatter)
        pixsieve_logger.addHandler(handler)

    if log_file:
        pixsieve_logger.info(f"Logging to file: {log_file}")


def create_app(log_level: int = LOG_MINIMAL) -> Flask:
    """
    Create and configure the Flask application.
    
    Args:
        log_level: Logging verbosity level
        
    Returns:
        Configured Flask app instance
    """
    # Get the package directory for templates
    package_dir = os.path.dirname(os.path.abspath(__file__))
    template_dir = os.path.join(package_dir, 'templates')
    
    app = Flask(__name__, template_folder=template_dir)
    # Random per-process, not hardcoded: flask.session isn't used today, but
    # a fixed, source-controlled key would let anyone who's read the source
    # forge session data if a future feature ever adds session/flash/CSRF use.
    app.secret_key = os.urandom(24)
    # No request body size cap otherwise exists; /api/image/save in particular
    # accepts an arbitrary base64 data URL, so bound it to something generous
    # enough for a full-resolution photo edit but not unbounded.
    app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024  # 64 MB
    # Always re-read templates from disk — this is a locally-run, single-user
    # tool under active development; template caching only causes confusing
    # "my edit isn't showing up" symptoms with no corresponding benefit.
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    # Avoid long-lived browser caching of static JS/CSS during development.
    # (A restart of this server has no effect on a browser's HTTP cache —
    # only this header does.)
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
    
    # Configure logging based on level
    if log_level < LOG_VERBOSE:
        # Suppress Flask's default request logging
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.ERROR if log_level == LOG_QUIET else logging.WARNING)
    
    # Register routes
    app.register_blueprint(api)
    app.register_blueprint(operations_bp)

    # Register Swagger UI (available at /docs/)
    Swagger(app, config=_SWAGGER_CONFIG, template=_SWAGGER_TEMPLATE)

    return app


def cleanup_on_exit():
    """Clean up state file on exit if scan wasn't complete."""
    if scan_state.status not in ('complete', 'idle'):
        scan_state.clear_file()


def suppress_flask_banner():
    """Suppress Flask's development server banner and startup messages."""
    try:
        import flask.cli
        flask.cli.show_server_banner = lambda *args, **kwargs: None
    except (ImportError, AttributeError):
        pass
    
    # Suppress werkzeug's startup log messages
    import logging
    logging.getLogger('werkzeug').setLevel(logging.ERROR)


def main():
    """Main entry point for the GUI application."""
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description='PixSieve - GUI',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        '-q', '--quiet',
        action='store_true',
        help='Quiet mode - suppress all output except errors'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Verbose mode - show all Flask request logs'
    )
    parser.add_argument(
        '-p', '--port',
        type=int,
        default=5000,
        help='Port to run the server on (default: 5000)'
    )
    parser.add_argument(
        '--no-browser',
        action='store_true',
        help='Do not automatically open browser'
    )
    parser.add_argument(
        '--log-file',
        type=str,
        default=None,
        help='Also write scan/server logs to this file (recommended for long, unattended scans)'
    )

    args = parser.parse_args()
    
    # Determine log level
    if args.quiet:
        log_level = LOG_QUIET
    elif args.verbose:
        log_level = LOG_VERBOSE
    else:
        log_level = LOG_MINIMAL
    
    port = args.port
    url = f'http://localhost:{port}'
    
    # Print startup message (unless quiet)
    if log_level >= LOG_MINIMAL:
        print()
        print("  +======================================+")
        print("  |            PIXSIEVE - GUI            |")
        print("  +======================================+")
        print()

        # Try to restore previous state
        if scan_state.load() and scan_state.status == 'complete' and scan_state.groups:
            print(f"  Previous session: {scan_state.directory}")
            print(f"     {len(scan_state.groups)} duplicate groups found")
            print()

        print(f"  Server running at: {url}")
        if not args.no_browser:
            print("     Opening in browser...")
        print()
        print("  Press Ctrl+C to stop")
        print()
    
    # Configure scan-pipeline logging (separate from werkzeug's request logs)
    configure_scan_logging(log_level, args.log_file)

    # Register cleanup handler
    atexit.register(cleanup_on_exit)
    
    # Suppress Flask banner for non-verbose modes
    if log_level < LOG_VERBOSE:
        suppress_flask_banner()
    
    # Create the app
    app = create_app(log_level)
    
    # Open browser after short delay (unless disabled)
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    
    # Configure werkzeug logging
    if log_level < LOG_VERBOSE:
        logging.getLogger('werkzeug').setLevel(logging.ERROR)
    
    # Run Flask
    try:
        app.run(
            host='127.0.0.1',
            port=port,
            debug=False,
            threaded=True,
            use_reloader=False
        )
    except KeyboardInterrupt:
        if log_level >= LOG_MINIMAL:
            print("\n  Server stopped\n")


if __name__ == '__main__':
    main()