import os

# One worker for every program the suite compiles and runs, the compiler
# included, unless a test asks for more: the suite already runs a process per
# core, and each spawning a worker per core as well would be that squared.
os.environ.setdefault("TURKEY_WORKERS", "1")
# The compiler checks its own name resolution, and the spans and comments of
# every file it parses, on everything the suite compiles, the bootstrap included.
os.environ.setdefault("TURKEY_CHECK_BINDERS", "1")
os.environ.setdefault("TURKEY_CHECK_SPANS", "1")
# The compiler under test is cached under the temporary directory, where no
# `lib/` lies above it, so the suite names the library: `lib` relative to each
# process's working directory, which is the repository root unless a test
# says otherwise. A test of the lookup itself removes the variable.
os.environ.setdefault("TURKEY_LIB", "lib")
