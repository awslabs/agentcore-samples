import logging
import subprocess

from config_utils import get_server_ports

# Configure logging with basicConfig
logging.basicConfig(
    level=logging.INFO,  # Set the log level to INFO
    # Define log message format
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)


def _stop_servers():
    """Stop all running stub servers"""
    # Get ports from OpenAPI specifications
    port_config = get_server_ports()

    # Create lists of ports and names
    ports = list(port_config.values())
    server_names = [name.title() for name in port_config.keys()]

    for port, name in zip(ports, server_names):
        try:
            # Find processes using the port
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"], capture_output=True, text=True
            )

            if result.returncode == 0 and result.stdout.strip():
                pids = result.stdout.strip().split("\n")
                for pid in pids:
                    if pid:
                        logging.info(
                            f"Stopping {name} server (PID: {pid}) on port {port}"
                        )
                        subprocess.run(["kill", pid], check=False)
            else:
                logging.info(f"No {name} server found on port {port}")

        except FileNotFoundError:
            # lsof not available, try netstat approach
            try:
                result = subprocess.run(
                    ["netstat", "-tlnp"], capture_output=True, text=True
                )

                for line in result.stdout.split("\n"):
                    if f":{port}" in line and "LISTEN" in line:
                        # Extract PID from netstat output
                        parts = line.split()
                        if len(parts) > 6:
                            pid_info = parts[6]
                            if "/" in pid_info:
                                pid = pid_info.split("/")[0]
                                if pid.isdigit():
                                    logging.info(
                                        f"Stopping {name} server (PID: {pid}) on port {port}"
                                    )
                                    subprocess.run(["kill", pid], check=False)
                                    break

            except Exception as e:
                logging.error(f"Error stopping {name} server: {str(e)}")


def main():
    """Main entry point"""
    logging.info("Stopping all DevOps Multi-Agent Demo servers...")
    _stop_servers()
    logging.info("All servers stopped.")


if __name__ == "__main__":
    main()
