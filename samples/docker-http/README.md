# Authored validation sample

Python 3.13.2 standard-library HTTP server. Linux/amd64 base pinned by verified digest.
Runs as UID/GID 10001, port 8080. `/health` returns marker `packager-python-v1`.
GET `/` is a fixed JSON response; other paths return 404. No static directory serving.
No third-party Python package, database, storage, login, or external service.
