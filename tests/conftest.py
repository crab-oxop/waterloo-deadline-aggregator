"""Point the app's data dir at a throwaway folder before any aggregator module is imported, so
no test can read or write the real database, browser profile, logs or prefs."""
import os
import tempfile

os.environ["WATERLOO_DEADLINES_DATA"] = tempfile.mkdtemp(prefix="wd-test-data-")
