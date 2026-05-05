UDP_TEL_PORT = 9001
UDP_VIS_PORT = 9003
TCP_CMD_PORT = 9002

UDP_MAX_TEL_BYTES = 1024
UDP_MAX_VIS_BYTES = 512
TCP_MAX_FRAME_BYTES = 4096

CMD_TIMEOUT_S = 0.5

# Default max payload size for the /api/frame JPEG ingest endpoint and the
# matching encoder cap on the vision side. Both processes default to this
# value and override via the BACKEND_VIDEO_MAX_JPEG_BYTES env var at startup.
# Keeping the default in one place avoids the failure mode where the backend
# accepts frames the vision side won't produce (or vice versa).
VIDEO_MAX_JPEG_BYTES_DEFAULT = 200_000
