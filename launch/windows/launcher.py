import os
import sys
import json
import time
import subprocess
import threading
import ctypes

# Enable ANSI escape processing on Windows Console
COLOR_SYSTEM = "\033[97;1m"  # Bold White
COLOR_ASTRON = "\033[96m"    # Cyan
COLOR_UBERDOG = "\033[92m"   # Green
COLOR_AI = "\033[93m"        # Yellow
COLOR_CLIENT = "\033[95m"    # Magenta
COLOR_RESET = "\033[0m"

def enable_ansi():
    if os.name == 'nt':
        try:
            kernel32 = ctypes.windll.kernel32
            h_stdout = kernel32.GetStdHandle(-11) # STD_OUTPUT_HANDLE
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(h_stdout, ctypes.byref(mode)):
                kernel32.SetConsoleMode(h_stdout, mode.value | 0x0004) # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        except Exception:
            pass

# Thread-safe printer
print_lock = threading.Lock()

def safe_print(prefix, color_code, text):
    with print_lock:
        encoding = sys.stdout.encoding or 'utf-8'
        for line in text.splitlines():
            cleaned = line.encode(encoding, errors='replace').decode(encoding)
            sys.stdout.write(f"{color_code}{prefix:<10} | {cleaned}{COLOR_RESET}\n")
        sys.stdout.flush()

# Stream reader for subprocess output
def stream_reader(pipe, prefix, color_code):
    try:
        for line in iter(pipe.readline, b''):
            try:
                line_str = line.decode('utf-8', errors='replace')
            except Exception:
                line_str = line.decode('cp1252', errors='replace')
            safe_print(prefix, color_code, line_str.rstrip('\r\n'))
    except Exception as e:
        safe_print("[System]", COLOR_SYSTEM, f"Error reading output from {prefix}: {e}")

def configure_launcher(config, config_path, force_prompt=False):
    if not force_prompt:
        safe_print("[System]", COLOR_SYSTEM, "--------------------------------------------------------")
        safe_print("[System]", COLOR_SYSTEM, "Current saved settings:")
        safe_print("[System]", COLOR_SYSTEM, f"  Player Name:    {config['TTOFF_LOGIN_TOKEN']}")
        safe_print("[System]", COLOR_SYSTEM, f"  Game Server:    {config['TTOFF_GAME_SERVER']}")
        safe_print("[System]", COLOR_SYSTEM, f"  District Name:  {config['DISTRICT_NAME']}")
        safe_print("[System]", COLOR_SYSTEM, f"  Astron IP:      {config['ASTRON_IP']}")
        safe_print("[System]", COLOR_SYSTEM, "--------------------------------------------------------")

        try:
            use_saved = input("Use these settings? [Y/n]: ").strip().lower()
        except KeyboardInterrupt:
            raise
        except Exception:
            use_saved = 'y'
        if use_saved not in ('n', 'no'):
            return config

    # Prompt for each value
    new_config = {}
    try:
        val = input(f"Enter player name [{config['TTOFF_LOGIN_TOKEN']}]: ").strip()
        new_config['TTOFF_LOGIN_TOKEN'] = val if val else config['TTOFF_LOGIN_TOKEN']

        val = input(f"Enter game server IP [{config['TTOFF_GAME_SERVER']}]: ").strip()
        new_config['TTOFF_GAME_SERVER'] = val if val else config['TTOFF_GAME_SERVER']

        val = input(f"Enter district name [{config['DISTRICT_NAME']}]: ").strip()
        new_config['DISTRICT_NAME'] = val if val else config['DISTRICT_NAME']

        val = input(f"Enter Astron IP [{config['ASTRON_IP']}]: ").strip()
        new_config['ASTRON_IP'] = val if val else config['ASTRON_IP']
    except KeyboardInterrupt:
        raise
    except Exception as e:
        safe_print("[System]", COLOR_SYSTEM, f"Error reading input: {e}. Using defaults.")
        return config

    try:
        with open(config_path, "w") as f:
            json.dump(new_config, f, indent=4)
        safe_print("[System]", COLOR_SYSTEM, "Settings saved to config file.")
    except Exception as e:
        safe_print("[System]", COLOR_SYSTEM, f"Could not save config file: {e}")

    return new_config

def main():
    enable_ansi()

    # Compute directories. This script lives in <repo>/launch/windows, so the
    # repository root is two levels up.
    script_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(os.path.dirname(script_dir))

    # Locate ppython executable
    ppython_path = sys.executable

    # Load configuration
    config_path = os.path.join(script_dir, "launcher_config.json")
    defaults = {
        "TTOFF_LOGIN_TOKEN": "player1",
        "TTOFF_GAME_SERVER": "127.0.0.1",
        "DISTRICT_NAME": "Archipelago Avenue",
        "ASTRON_IP": "127.0.0.1:7199"
    }

    config = defaults.copy()
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                saved = json.load(f)
                for k, v in saved.items():
                    config[k] = v
        except Exception as e:
            safe_print("[System]", COLOR_SYSTEM, f"Error loading config file: {e}")

    # Prompt user
    safe_print("[System]", COLOR_SYSTEM, "========================================================")
    safe_print("[System]", COLOR_SYSTEM, "          TOONTOWN ARCHIPELAGO UNIFIED LAUNCHER          ")
    safe_print("[System]", COLOR_SYSTEM, "========================================================")

    try:
        config = configure_launcher(config, config_path, force_prompt=False)
    except KeyboardInterrupt:
        safe_print("[System]", COLOR_SYSTEM, "Aborted.")
        sys.exit(0)

    # Pip install verification
    safe_print("[System]", COLOR_SYSTEM, "Verifying dependency installation...")
    requirements_path = os.path.join(root_dir, "requirements.txt")
    try:
        subprocess.check_call([ppython_path, "-m", "pip", "install", "-q", "-r", requirements_path], cwd=root_dir)
    except Exception as e:
        safe_print("[System]", COLOR_SYSTEM, f"Warning: Python dependency check returned error: {e}")

    # Build commands and environments.
    # Every service is launched through the shared launch.launcher.launch
    # entrypoint, dispatching on SERVICE_TO_RUN (same as the other .bat files).
    launch_cmd = [ppython_path, "-m", "launch.launcher.launch"]

    # Astron command
    astron_dir = os.path.join(root_dir, "astron")
    astrond_exe = os.path.join(astron_dir, "astrond.exe")
    astrond_bin = os.path.join(astron_dir, "astrond")
    if os.path.exists(astrond_exe):
        astron_cmd = [astrond_exe, "--loglevel", "info", "config/astrond.yml"]
    elif os.path.exists(astrond_bin):
        astron_cmd = [astrond_bin, "--loglevel", "info", "config/astrond.yml"]
    else:
        safe_print("[System]", COLOR_SYSTEM, "ERROR: Astron binary not found in astron/ directory!")
        sys.exit(1)

    # UD Setup
    ud_env = os.environ.copy()
    ud_env.update({
        "SERVICE_TO_RUN": "UD",
        "BASE_CHANNEL": "1000000",
        "MAX_CHANNELS": "999999",
        "STATESERVER": "4002",
        "ASTRON_IP": config["ASTRON_IP"],
        "EVENTLOGGER_IP": "127.0.0.1:7197",
        "WANT_ERROR_REPORTING": "true"
    })
    ud_cmd = list(launch_cmd)

    # AI Setup
    ai_env = os.environ.copy()
    ai_env.update({
        "SERVICE_TO_RUN": "AI",
        "BASE_CHANNEL": "401000000",
        "MAX_CHANNELS": "999999",
        "STATESERVER": "4002",
        "DISTRICT_NAME": config["DISTRICT_NAME"],
        "ASTRON_IP": config["ASTRON_IP"],
        "EVENTLOGGER_IP": "127.0.0.1:7197",
        "WANT_ERROR_REPORTING": "true"
    })
    ai_cmd = list(launch_cmd)

    # Client Setup
    client_env = os.environ.copy()
    client_env.update({
        "SERVICE_TO_RUN": "CLIENT",
        "TTOFF_LOGIN_TOKEN": config["TTOFF_LOGIN_TOKEN"],
        "TTOFF_GAME_SERVER": config["TTOFF_GAME_SERVER"]
    })
    client_cmd = list(launch_cmd)

    active_processes = {}

    def cleanup():
        safe_print("[System]", COLOR_SYSTEM, "Shutting down all background processes...")
        # Terminate gently first
        for name, p in list(active_processes.items()):
            if p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass
        time.sleep(1.0)
        # Kill if still alive
        for name, p in list(active_processes.items()):
            if p.poll() is None:
                try:
                    p.kill()
                except Exception:
                    pass
        active_processes.clear()

    try:
        # Start Astron
        p = subprocess.Popen(astron_cmd, cwd=astron_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        active_processes["Astron"] = p
        t = threading.Thread(target=stream_reader, args=(p.stdout, "[Astron]", COLOR_ASTRON))
        t.daemon = True
        t.start()
        time.sleep(1.0)

        # Start Uberdog
        p = subprocess.Popen(ud_cmd, cwd=root_dir, env=ud_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        active_processes["Uberdog"] = p
        t = threading.Thread(target=stream_reader, args=(p.stdout, "[Uberdog]", COLOR_UBERDOG))
        t.daemon = True
        t.start()
        time.sleep(1.0)

        # Start AI
        p = subprocess.Popen(ai_cmd, cwd=root_dir, env=ai_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        active_processes["AI"] = p
        t = threading.Thread(target=stream_reader, args=(p.stdout, "[AI]", COLOR_AI))
        t.daemon = True
        t.start()
        time.sleep(1.0)

        # Client loop (allows restarting)
        while True:
            # Check if servers are still running
            servers_alive = True
            for name in ["Astron", "Uberdog", "AI"]:
                if name in active_processes and active_processes[name].poll() is not None:
                    safe_print("[System]", COLOR_SYSTEM, f"WARNING: {name} server is not running (exited early).")
                    servers_alive = False

            # Start client
            safe_print("[System]", COLOR_SYSTEM, f"Starting game client for '{client_env['TTOFF_LOGIN_TOKEN']}'...")
            p_client = subprocess.Popen(client_cmd, cwd=root_dir, env=client_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            active_processes["Client"] = p_client
            t_client = threading.Thread(target=stream_reader, args=(p_client.stdout, "[Client]", COLOR_CLIENT))
            t_client.daemon = True
            t_client.start()

            # Wait for client to exit
            while p_client.poll() is None:
                time.sleep(0.5)
                # Periodically verify server processes
                for name in ["Astron", "Uberdog", "AI"]:
                    proc = active_processes.get(name)
                    if proc and proc.poll() is not None:
                        if name in active_processes:
                            safe_print("[System]", COLOR_SYSTEM, f"WARNING: {name} process has stopped running!")
                            del active_processes[name]

            safe_print("[System]", COLOR_SYSTEM, "Client process has exited.")
            if "Client" in active_processes:
                del active_processes["Client"]

            # Provide options to relaunch client or quit
            safe_print("[System]", COLOR_SYSTEM, "Options: [R]elaunch Client | [C]hange Settings & Relaunch | [Enter] to exit all")
            try:
                choice = input("Enter choice: ").strip().lower()
            except KeyboardInterrupt:
                break
            except Exception:
                choice = ''

            if choice == 'r':
                continue
            elif choice == 'c':
                # Force prompt for config
                try:
                    config = configure_launcher(config, config_path, force_prompt=True)
                except KeyboardInterrupt:
                    break
                client_env.update({
                    "TTOFF_LOGIN_TOKEN": config["TTOFF_LOGIN_TOKEN"],
                    "TTOFF_GAME_SERVER": config["TTOFF_GAME_SERVER"]
                })
                continue
            else:
                break

    except KeyboardInterrupt:
        safe_print("[System]", COLOR_SYSTEM, "Interrupted by user.")
    finally:
        cleanup()
        safe_print("[System]", COLOR_SYSTEM, "Unified launcher finished.")

if __name__ == "__main__":
    main()
