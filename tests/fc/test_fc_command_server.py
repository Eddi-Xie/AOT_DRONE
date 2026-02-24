import json
import socket
import struct
import time


def validate_command_server():
    HOST = "127.0.0.1"
    PORT = 9002

    # The message to send
    msg_dict = {"type": "CMD", "action": "ARM", "value": 1.0}
    payload = json.dumps(msg_dict).encode("utf-8")

    # Create the frame: [4-byte Big-Endian Length] + [JSON Payload]
    # ">I" means Big-Endian (>) Unsigned Int (I)
    header = struct.pack(">I", len(payload))
    frame = header + payload

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2.0)
            print(f"Connecting to CommandServer on {PORT}...")
            s.connect((HOST, PORT))

            print(f"Sending frame ({len(payload)} bytes of data)...")
            s.sendall(frame)

            # Brief pause to allow the FC thread to process
            time.sleep(0.5)
            print("Done.")

    except Exception as e:
        print(f"Validation failed: {e}")


if __name__ == "__main__":
    validate_command_server()
