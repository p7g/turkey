import os

# One worker for every program the suite compiles and runs, the compiler
# included, unless a test asks for more: the suite already runs a process per
# core, and each spawning a worker per core as well would be that squared.
os.environ.setdefault("TURKEY_WORKERS", "1")
