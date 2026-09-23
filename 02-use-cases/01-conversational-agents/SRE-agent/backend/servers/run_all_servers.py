import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

from config_utils import get_server_ports

# Configure logging with basicConfig
logging.basicConfig(
    level=logging.INFO,  # Set the log level to INFO
    # Define log message format
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)


def _stream_output(process, name):
    """Stream output from a subprocess to console"""
    # Stream stdout
    for line in iter(process.stdout.readline, b""):
        if line:
            print(f"[{name}] {line.decode().rstrip()}")

    # Stream stderr
    for line in iter(process.stderr.readline, b""):
        if line:
            print(f"[{name} ERROR] {line.decode().rstrip()}", file=sys.stderr)


def _run_servers():
    """Run all stub servers concurrently"""
    # Get ports from OpenAPI specifications
    ports = get_server_ports()

    servers = [
        ("K8s Server", "k8s_server.py", ports.get("k8s")),
        ("Logs Server", "logs_server.py", ports.get("logs")),
        ("Metrics Server", "metrics_server.py", ports.get("metrics")),
        ("Runbooks Server", "runbooks_server.py", ports.get("runbooks")),
    ]

    # Filter out servers with missing ports
    valid_servers = []
    for name, script, port in servers:
        if port is not None:
            valid_servers.append((name, script, port))
        else:
            logging.error(f"Could not determine port for {name}, skipping")

    servers = valid_servers

    processes = []

    # Change to the project directory
    project_dir = Path(__file__).parent

    for name, script, port in servers:
        logging.info(f"Starting {name} on port {port}...")
        process = subprocess.Popen(
            [sys.executable, script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=project_dir,
            bufsize=1,  # Line buffered
            universal_newlines=False,  # Use binary mode for better control
        )
        processes.append((name, process))

        # Start a thread to stream the output
        output_thread = threading.Thread(
            target=_stream_output, args=(process, name), daemon=True
        )
        output_thread.start()

        time.sleep(2)  # Give each server time to start

    logging.info("\n" + "=" * 80)
    logging.info("All servers running. Press Ctrl+C to stop all servers.")
    logging.info("=" * 80 + "\n")
    logging.info("Test URLs:")
    for name, _, port in servers:
        name.split()[0].lower()
        logging.info(f"  {name:<15}: https://localhost:{port}/")

    logging.info("\nAPI Documentation (add /docs to any URL):")
    for name, _, port in servers:
        name.split()[0].lower()
        logging.info(f"  {name} Docs: https://localhost:{port}/docs")

    try:
        # Keep the script running
        while True:
            time.sleep(1)
            # Check if any process has died
            for name, process in processes:
                if process.poll() is not None:
                    logging.error(f"{name} has stopped unexpectedly!")
                    # The output has already been streamed by the thread
                    # Just note that the process has died
                    logging.error(f"{name} exited with code: {process.returncode}")
    except KeyboardInterrupt:
        logging.info("\n" + "=" * 80)
        logging.info("Stopping all servers...")
        logging.info("=" * 80)
        for name, process in processes:
            process.terminate()
            logging.info(f"Stopped {name}")
            # Wait a bit for graceful shutdown
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                # Force kill if it doesn't stop gracefully
                process.kill()
                logging.warning(f"Force killed {name}")


def main():
    """Main entry point"""
    try:
        _run_servers()
    except Exception as e:
        logging.error(f"Error running servers: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
