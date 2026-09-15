"""Process entry point.

Runs uvicorn programmatically with uvicorn's default logging configuration
disabled (``log_config=None``). ``setup_logging()`` runs when ``app.main``
is imported and installs the JSON pipeline on the root logger, so every line
-- including uvicorn's startup lines -- goes through one formatter.
"""

import uvicorn

from app.main import app

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_config=None,
        access_log=False,
    )
