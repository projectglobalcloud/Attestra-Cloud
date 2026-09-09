"""
Gunicorn settings for Attestra.

ONE WORKER, SEVERAL THREADS — and that is not a performance compromise, it is
a correctness requirement. The protection keys, the live protocol state and the
background scheduler all live inside one Python process by design (keys are
never written to disk). A second worker would be a second, empty universe: a
verification started in one would be invisible to the other, and a dataset
protected in one would read as "needs re-protection" in the other.

Threads give the concurrency instead. Protocol work happens in daemon threads
already, so requests stay short and the console polls for progress.
"""

import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"

workers = 1                 # see above — never raise this
threads = 8
worker_class = "gthread"

# A protection run on a large dataset can hold a request open while it sets up;
# the long work itself is in background threads, so this is headroom, not a
# routine wait.
timeout = 180
graceful_timeout = 30
keepalive = 5

accesslog = "-"
errorlog = "-"
loglevel = "info"
