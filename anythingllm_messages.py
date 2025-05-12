"""
AnythingLLM Voice Monitor v1.0
A tool to monitor AnythingLLM chat messages and convert them to speech using F5-TTS.
"""
import requests
import time
import json
import os
import glob
import threading
import select
import sys
import subprocess  # Add this import at the top level
from datetime import datetime
from gradio_client import Client  # For F5TTS
from playsound3 import playsound   # For playing audio

# Global variables for TTS
FIRSTIME = True
sound = None
f5tts_client = "http://127.0.0.1:7860/"  # F5TTS server address
f5tts_remove_silence = False  # Changed from f5tts_remore_silence and fixed the typo
f5tts_cross_fade = 0.15  # Updated default value
f5tts_nfe = 16  # Updated default value
f5tts_speed = 1.0
audio_player = "playsound"  # Options: "playsound" or "default_media_player"
show_checking = False  # Add this line to control visibility of checking process
monitor_by = "timestamp"  # Options: "id" or "timestamp"
f5tts_save_audio = "nosave"  # Options: "nosave" or "save"

# Variables for TTS timing calculations
tts_timing_data = []  # List to store character count and processing time pairs
tts_processed_count = 0  # Counter for number of TTS processes performed


def handle_file(file_path):
    """Helper function to handle file paths for TTS."""
    # Format the file data as expected by Gradio
    return {
        "path": file_path,
        "orig_name": os.path.basename(file_path),
        "meta": {"_type": "gradio.FileData"}
    }


def get_reference_audio_path():
    """Get the path to the reference audio directory based on OS."""
    # First, try to use a subdirectory of the current working directory
    program_dir = os.getcwd()
    program_ref_dir = os.path.join(program_dir, "referenc")

    # Check if this directory exists or create it
    if not os.path.exists(program_ref_dir):
        try:
            os.makedirs(program_ref_dir)
            print(
                f"Created reference directory in program folder: {program_ref_dir}")
        except Exception as e:
            print(f"Error creating reference directory in program folder: {e}")

    # Prioritize the program directory
    return program_ref_dir


def scan_reference_files():
    """Scan for available reference audio/text file pairs."""
    ref_dir = get_reference_audio_path()

    # Get all audio files (supporting common formats)
    audio_files = []
    for ext in ['.wav', '.mp3', '.ogg', '.flac']:
        audio_files.extend(glob.glob(os.path.join(ref_dir, f"*{ext}")))

    # If no audio files found, print a helpful message
    if not audio_files:
        print(f"No audio files found in {ref_dir}")
        print("Please add audio files with matching .txt files containing reference text.")
        print("Example: If you have 'voice.mp3', create 'voice.txt' with some sample text.")
        return []

    # Filter to only include files that have matching text files
    valid_pairs = []
    files_without_text = []

    for audio_file in audio_files:
        base_name = os.path.splitext(audio_file)[0]
        text_file = f"{base_name}.txt"

        if os.path.exists(text_file):
            audio_name = os.path.basename(audio_file)
            try:
                with open(text_file, 'r', encoding='utf-8') as f:
                    text_content = f.read().strip()

                # Only include if text file has content
                if text_content:
                    valid_pairs.append({
                        'name': audio_name,
                        'audio_path': audio_file,
                        'text_path': text_file,
                        'text_content': text_content
                    })
                else:
                    files_without_text.append(
                        f"{audio_name} (empty text file)")
            except Exception as e:
                print(f"Error reading text file for {audio_name}: {e}")
                files_without_text.append(f"{audio_name} (error reading text)")
        else:
            files_without_text.append(os.path.basename(audio_file))

    # If we found audio files but no valid pairs, explain why
    if audio_files and not valid_pairs:
        print(
            f"Found {len(audio_files)} audio files but no valid pairs with text files.")
        print("The following audio files are missing matching text files:")
        for file in files_without_text:
            print(f"  - {file}")

    return valid_pairs


def get_app_directory(app_name):
    """
    Get the directory where the application is installed or running from.

    Args:
        app_name (str): Name of the application

    Returns:
        str: Path to the application directory
    """
    # First try to get the directory where the script is running from
    program_dir = os.path.dirname(os.path.abspath(__file__))

    # Check if we're running from a bundled executable
    if getattr(sys, 'frozen', False):
        program_dir = os.path.dirname(sys.executable)

    # Create a subdirectory for app data
    app_dir = os.path.join(program_dir, app_name)

    # Check if the directory is writable
    try:
        # Try to create a test file to verify write access
        test_file = os.path.join(program_dir, '.write_test')
        with open(test_file, 'w') as f:
            f.write('test')
        os.remove(test_file)

        # If we reach here, directory is writable, create the app dir if needed
        if not os.path.exists(app_dir):
            os.makedirs(app_dir, exist_ok=True)

        return app_dir

    except (PermissionError, OSError, IOError):
        # Fall back to user directory if installation directory isn't writable
        print(f"Note: Cannot write to installation directory: {program_dir}")
        print("Falling back to user directory.")

        # Use traditional user directories as fallback
        home_dir = os.path.expanduser("~")
        if os.name == 'nt':  # Windows
            app_dir = os.path.join(os.getenv('APPDATA'), app_name)
        elif sys.platform == 'darwin':  # macOS
            app_dir = os.path.join(home_dir, 'Library',
                                   'Application Support', app_name)
        else:  # Linux and other Unix-like systems
            app_dir = os.path.join(home_dir, f".{app_name.lower()}")

        # Create the directory if it doesn't exist
        if not os.path.exists(app_dir):
            os.makedirs(app_dir, exist_ok=True)

        return app_dir


def open_file_with_default_app(file_path):
    """
    Open a file with the default application for its file type.

    Args:
        file_path (str): Path to the file to open
    """
    if os.name == 'nt':  # Windows
        os.startfile(file_path)
    elif sys.platform == 'darwin':  # macOS
        subprocess.run(['open', file_path], check=False)
    else:  # Linux
        subprocess.run(['xdg-open', file_path], check=False)


class NonBlockingConsole:
    """Improved non-blocking input handler that works cross-platform without PyWin32."""

    def __init__(self):
        self.timeout = 0.1  # 100ms timeout

        # Set up terminal for Unix systems
        if os.name != 'nt':
            try:
                import termios
                # Store the terminal settings so we can restore them
                self.fd = sys.stdin.fileno()
                self.old_settings = termios.tcgetattr(self.fd)
                # This is to ensure we can restore the terminal properly
                import atexit
                atexit.register(self.cleanup)
            except (ImportError, IOError):
                # Not all Unix-like systems support termios
                pass

    def cleanup(self):
        """Restore terminal settings on exit for Unix systems"""
        if os.name != 'nt':
            try:
                import termios
                termios.tcsetattr(self.fd, termios.TCSADRAIN,
                                  self.old_settings)
            except (ImportError, AttributeError, IOError):
                pass

    def check_input(self):
        """Check if any key is pressed. Works on Windows, Linux and macOS."""
        if os.name == 'nt':  # Windows
            try:
                import msvcrt
                if msvcrt.kbhit():
                    return msvcrt.getch().decode('utf-8', errors='ignore')
                return None
            except ImportError:
                return None
        else:  # Unix-like systems
            try:
                import termios
                import tty

                # Change terminal settings
                tty.setcbreak(sys.stdin.fileno())

                # Check if data is available to read
                if select.select([sys.stdin], [], [], self.timeout)[0]:
                    key = sys.stdin.read(1)
                    return key
                return None
            except (ImportError, IOError):
                # Fallback for systems without termios
                if select.select([sys.stdin], [], [], self.timeout)[0]:
                    return sys.stdin.read(1)
                return None


class AnythingLLMMonitor:
    def __init__(self, config):
        """
        Initialize the AnythingLLM monitor.

        Args:
            config (dict): Configuration dictionary
        """
        self.base_url = config['base_url'].rstrip('/')
        self.api_key = config['api_key']
        self.check_interval = config['check_interval']
        self.seen_responses = set()
        self.highest_chat_id = 0
        self.latest_timestamp = ""
        self.data_file = "seen_responses.json"
        self.first_run = True
        self.running = True
        self.menu_active = False
        # Make sure we're using the value from config
        self.monitor_by = config['monitor_by']  # Options: "id" or "timestamp"
        self.f5tts_selected_ref = "not chosen"  # Selected reference audio
        self.console = NonBlockingConsole()
        self.consecutive_failures = 0  # Track connection failures
        self.max_failures = 10  # Exit after this many consecutive failures
        # Show checking process flag
        self.show_checking = config['show_checking']

        # Store the full config in this instance
        self.config = config  # This line was missing or incorrectly implemented

        # Set global TTS variables from config
        global f5tts_client, f5tts_remove_silence, f5tts_cross_fade, f5tts_nfe, f5tts_speed, audio_player, f5tts_save_audio
        f5tts_client = config['f5tts_client']
        f5tts_remove_silence = config['f5tts_remove_silence']
        f5tts_cross_fade = config['f5tts_cross_fade']
        f5tts_nfe = config['f5tts_nfe']
        f5tts_speed = config['f5tts_speed']
        audio_player = config['audio_player']
        f5tts_save_audio = config['f5tts_save_audio']

        # Check if API key is set
        if self.api_key == "your anythingllm api key" or not self.api_key:
            print("\n" + "!" * 60)
            print("WARNING: AnythingLLM API key not set!")
            print("Please edit config_f5tts_any.txt and set your API key.")
            print("!" * 60 + "\n")
            input("Press Enter to continue anyway or Ctrl+C to exit...")

        # Load previously seen responses if available
        self._load_seen_responses()

    def _get_headers(self):
        """Generate headers for API requests."""
        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        return headers

    def _load_seen_responses(self):
        """Load previously seen response IDs and monitoring settings from file."""
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, 'r') as f:
                    data = json.load(f)
                    self.seen_responses = set(data.get('responses', []))
                    self.highest_chat_id = data.get('highest_chat_id', 0)
                    self.latest_timestamp = data.get('latest_timestamp', "")
                    self.monitor_by = data.get('monitor_by', "id")
                    self.f5tts_selected_ref = data.get(
                        'f5tts_selected_ref', "not chosen")

                    # Load TTS settings or use defaults
                    global f5tts_client, f5tts_remove_silence, f5tts_cross_fade, f5tts_nfe, f5tts_speed, audio_player, f5tts_save_audio
                    f5tts_client = data.get(
                        'f5tts_client', "http://127.0.0.1:7860/")
                    f5tts_remove_silence = data.get(
                        'f5tts_remove_silence', False)
                    f5tts_cross_fade = data.get('f5tts_cross_fade', 0.15)
                    f5tts_nfe = data.get('f5tts_nfe', 16)
                    f5tts_speed = data.get('f5tts_speed', 1.0)
                    audio_player = data.get('audio_player', "playsound")
                    f5tts_save_audio = data.get('f5tts_save_audio', "nosave")

                    self.max_failures = data.get('max_failures', 10)
                    # Load show_checking setting
                    self.show_checking = data.get('show_checking', False)

                print(
                    f"Loaded {len(self.seen_responses)} previously seen responses")
                print(
                    f"Highest chat ID from previous run: {self.highest_chat_id}")
                if self.latest_timestamp:
                    print(
                        f"Latest timestamp from previous run: {self.latest_timestamp}")
                print(f"Monitoring new messages by: {self.monitor_by}")
                print(f"F5-TTS reference audio: {self.f5tts_selected_ref}")
                print(
                    f"Show checking process: {'On' if self.show_checking else 'Off'}")

                # Update global TTS variables if a reference is selected
                global f5tts_ref_audio, f5tts_ref_text
                if self.f5tts_selected_ref != "not chosen":
                    ref_files = scan_reference_files()
                    for ref in ref_files:
                        if ref['name'] == self.f5tts_selected_ref:
                            f5tts_ref_audio = ref['audio_path']
                            f5tts_ref_text = ref['text_content']
                            break

            except Exception as e:
                print(f"Error loading seen responses: {e}")

    def _save_seen_responses(self):
        """Save seen response IDs and monitoring settings to file."""
        try:
            with open(self.data_file, 'w') as f:
                json.dump({
                    'responses': list(self.seen_responses),
                    'highest_chat_id': self.highest_chat_id,
                    'latest_timestamp': self.latest_timestamp,
                    'monitor_by': self.monitor_by,
                    'f5tts_selected_ref': self.f5tts_selected_ref,
                    'f5tts_client': f5tts_client,
                    'f5tts_remove_silence': f5tts_remove_silence,
                    'f5tts_cross_fade': f5tts_cross_fade,
                    'f5tts_nfe': f5tts_nfe,
                    'f5tts_speed': f5tts_speed,
                    'audio_player': audio_player,
                    'show_checking': self.show_checking,
                    'f5tts_save_audio': f5tts_save_audio,
                    'last_updated': datetime.now().isoformat()
                }, f)

            # Also update the config file with current settings
            self.config.update({
                'base_url': self.base_url,
                'api_key': self.api_key,
                'check_interval': self.check_interval,
                'monitor_by': self.monitor_by,
                'f5tts_client': f5tts_client,
                'f5tts_remove_silence': f5tts_remove_silence,
                'f5tts_cross_fade': f5tts_cross_fade,
                'f5tts_nfe': f5tts_nfe,
                'f5tts_speed': f5tts_speed,
                'audio_player': audio_player,
                'show_checking': self.show_checking,
                'f5tts_save_audio': f5tts_save_audio
            })
            save_config(self.config)

        except Exception as e:
            print(f"Error saving seen responses: {e}")

    def _check_failure_threshold(self):
        """Check if consecutive failures have reached the threshold to exit."""
        if self.consecutive_failures >= self.max_failures:
            print(
                f"\nError: Connection to AnythingLLM failed {self.consecutive_failures} times in a row.")
            print("AnythingLLM may be offline or unreachable. Exiting program.")
            self._save_seen_responses()  # Save data before exiting
            self.running = False  # Signal the main loop to exit

    def fetch_responses(self):
        """Fetch responses from the AnythingLLM API."""
        try:
            # Use the successful endpoint from your testing
            url = f"{self.base_url}/v1/admin/workspace-chats"

            if self.show_checking:
                print(f"Fetching from: {url}")

            response = requests.post(url, headers=self._get_headers(), json={})

            if response.status_code == 200:
                # Reset the failure counter on success
                self.consecutive_failures = 0
                data = response.json()

                if self.show_checking:
                    print(
                        f"Received data with {len(data.get('chats', []))} chats")

                return data
            else:
                # Increment failure counter on error responses
                self.consecutive_failures += 1
                print(
                    f"Error fetching workspace chats: {response.status_code} - {response.text}")
                self._check_failure_threshold()
                return None
        except Exception as e:
            # Increment failure counter on exceptions
            self.consecutive_failures += 1
            print(f"Exception while fetching responses: {e}")
            self._check_failure_threshold()
            return None

    def process_new_responses(self, responses_data):
        """Process and identify new responses."""
        if not responses_data:
            return []

        new_responses = []

        # Process the chats array from the response
        chats = responses_data.get('chats', [])

        if self.show_checking:
            print(f"Processing {len(chats)} chats")

        # Find the highest values in the current batch
        current_max_id = 0
        current_latest_time = self.latest_timestamp

        for chat in chats:
            chat_id = chat.get('id', 0)
            if chat_id > current_max_id:
                current_max_id = chat_id

            timestamp = chat.get('createdAt', "")
            if timestamp > current_latest_time:
                current_latest_time = timestamp

            if self.show_checking:
                print(f"Chat ID: {chat_id}, Timestamp: {timestamp}")

        # On first run, just record the highest values and don't show any messages
        if self.first_run:
            self.highest_chat_id = max(self.highest_chat_id, current_max_id)
            self.latest_timestamp = current_latest_time
            self.first_run = False
            print(
                f"First run - recorded highest chat ID: {self.highest_chat_id}")
            print(
                f"First run - recorded latest timestamp: {self.latest_timestamp}")
            return []

        # Process chats based on monitoring method
        for chat in chats:
            chat_id = chat.get('id', 0)
            timestamp = chat.get('createdAt', "")

            # Skip if this chat doesn't meet our criteria based on monitoring method
            if self.monitor_by == "id" and chat_id <= self.highest_chat_id:
                continue
            elif self.monitor_by == "timestamp" and timestamp <= self.latest_timestamp:
                continue
            elif self.monitor_by == "both" and chat_id <= self.highest_chat_id and timestamp <= self.latest_timestamp:
                continue

            workspace_info = chat.get('workspace', {})
            workspace_slug = workspace_info.get('slug', 'unknown')
            workspace_name = workspace_info.get('name', 'Unknown Workspace')

            # Create a unique ID for this chat
            response_id = f"{workspace_slug}:{chat_id}"

            # Only process if this is a new response we haven't seen before
            if response_id not in self.seen_responses:
                # Parse the response JSON which is stored as a string
                response_json_str = chat.get('response', '{}')
                try:
                    response_obj = json.loads(response_json_str)
                    response_text = response_obj.get('text', '')

                    new_responses.append({
                        'workspace': workspace_slug,
                        'workspace_name': workspace_name,
                        'chat_id': chat_id,
                        'prompt': chat.get('prompt', ''),
                        'content': response_text,
                        'timestamp': timestamp
                    })

                    self.seen_responses.add(response_id)

                    # Update tracking values if needed
                    if chat_id > self.highest_chat_id:
                        self.highest_chat_id = chat_id
                    if timestamp > self.latest_timestamp:
                        self.latest_timestamp = timestamp

                except json.JSONDecodeError as e:
                    print(
                        f"Error parsing response JSON for chat {chat_id}: {e}")

        return new_responses

    def notify_new_responses(self, new_responses):
        """Notify about new responses and perform TTS."""
        if not new_responses:
            return

        print(f"\n{'='*60}")
        print(
            f"Found {len(new_responses)} new AI responses at {datetime.now().isoformat()}")
        print(f"{'='*60}")

        for idx, response in enumerate(new_responses, 1):
            print(f"\n--- Response {idx} ---")
            ai_reply = response['content']
            print(f"Response: {ai_reply}")
            print("-" * 40)

            # Pass the full response to process_tts
            self.process_tts(ai_reply, response)

    def process_tts(self, ai_reply, response_content=None):
        """Process Text-to-Speech for a response."""
        global f5tts_client, f5tts_ref_audio, f5tts_ref_text, f5tts_remove_silence, f5tts_cross_fade, f5tts_nfe, f5tts_speed, audio_player, f5tts_save_audio
        global tts_timing_data, tts_processed_count

        # Skip TTS if no reference audio is selected
        if self.f5tts_selected_ref == "not chosen" or f5tts_ref_audio == "not chosen":
            print("F5-TTS reference audio not selected. Skipping TTS.")
            return

        # Count words and characters for timing info
        number_of_words = len(ai_reply.split())
        char_count = len(ai_reply)

        # Calculate estimated processing time based on historical data
        estimated_time = None
        if tts_timing_data:
            # Use simple linear regression if we have enough data points
            if len(tts_timing_data) >= 3:
                # Calculate average time per character
                total_chars = sum(item[0] for item in tts_timing_data)
                total_time = sum(item[1] for item in tts_timing_data)
                avg_time_per_char = total_time / total_chars
                estimated_time = avg_time_per_char * char_count
                print(
                    f"I have calculated that this is going to take approximately {estimated_time:.1f} seconds.")
            else:
                # Simple estimation based on most recent processing
                recent_time_per_char = tts_timing_data[-1][1] / \
                    tts_timing_data[-1][0]
                estimated_time = recent_time_per_char * char_count
                print(
                    f"Based on recent processing, this will take approximately {estimated_time:.1f} seconds.")
        else:
            # First time processing
            print("First time running F5-TTS. Timing how long it takes...")

        # Recalibrate timing if the character count is between 3000-4000 or never done before
        should_recalibrate = (
            char_count >= 3000 and char_count <= 4000) or not tts_timing_data
        if should_recalibrate:
            print(
                "Character count in calibration range. Will update timing model after processing.")

        start_time = time.time()

        try:
            # 5 seconds or adjust as needed
            client = Client(f5tts_client)
            result = client.predict(
                ref_audio_input=handle_file(f5tts_ref_audio),
                ref_text_input=f5tts_ref_text,
                gen_text_input=ai_reply,
                remove_silence=f5tts_remove_silence,
                cross_fade_duration_slider=float(f5tts_cross_fade),
                nfe_slider=int(f5tts_nfe),
                speed_slider=float(f5tts_speed),
                api_name="/basic_tts",
            )
            source_audio_path = result[0]
            end_time = time.time()
            elapsed_time = end_time - start_time

            # Update timing data
            if should_recalibrate:
                tts_timing_data.append((char_count, elapsed_time))
                # Keep only the last 5 timing data points to adapt to changes in system performance
                if len(tts_timing_data) > 5:
                    tts_timing_data.pop(0)

            tts_processed_count += 1

            print(
                f"{number_of_words} words ({char_count} characters) took {elapsed_time:.1f} seconds.")

            if estimated_time is not None:
                error_percentage = abs(
                    estimated_time - elapsed_time) / elapsed_time * 100
                print(f"Estimation accuracy: {100 - error_percentage:.1f}%")

            global FIRSTIME
            global sound

            # Stop currently playing sound if needed
            if FIRSTIME is False:
                if sound is not None and hasattr(sound, 'is_alive') and sound.is_alive():
                    print(
                        "Sound is still playing! Stopping it before playing new sound.")
                    try:
                        # Force stop the sound
                        if hasattr(sound, 'stop'):
                            sound.stop()

                        # If we're using playsound on Windows, we may need a different approach
                        if os.name == 'nt' and audio_player == "playsound":
                            import ctypes
                            # Try to use winmm to stop all sounds
                            try:
                                winmm = ctypes.WinDLL('winmm')
                                winmm.PlaySoundW(None, 0, 0)
                            except:
                                pass

                        # Make sure to wait for the sound to actually stop
                        time.sleep(0.5)

                        # Set sound to None to avoid referencing stopped thread
                        sound = None

                    except Exception as e:
                        print(f"Error stopping sound: {e}")

            # Determine which file to use for playback
            playback_file = None

            # CASE 1: Save mode is enabled - use saved file for both purposes
            if f5tts_save_audio == "save" and response_content:
                try:
                    # Create a filename from the first 30 chars of content and current time
                    content_prefix = response_content.get('prompt', '')[
                        :30].strip()
                    if not content_prefix:  # Fallback if prompt not available
                        content_prefix = "ai_response"

                    # Clean filename (remove invalid characters)
                    content_prefix = ''.join(
                        c for c in content_prefix if c.isalnum() or c.isspace())
                    content_prefix = content_prefix.replace(' ', '_').lower()

                    # Add timestamp
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

                    # Get the app directory
                    app_dir = get_app_directory("anythingllm")

                    # Create a "saved" subdirectory
                    saved_dir = os.path.join(app_dir, "saved")

                    # Ensure directory exists
                    os.makedirs(saved_dir, exist_ok=True)

                    # Create save filename
                    save_filename = os.path.join(
                        saved_dir, f"{content_prefix}_{timestamp}.wav")

                    # Import shutil for file operations
                    import shutil

                    # Always use copy2 instead of move/replace for cross-drive compatibility
                    try:
                        shutil.copy2(source_audio_path, save_filename)

                        if os.path.exists(save_filename):
                            playback_file = save_filename  # Use saved file for playback
                            print(f"Saved audio file to: {save_filename}")
                        else:
                            print(
                                f"Warning: Saved file {save_filename} was not created successfully")
                            # Fall back to original file if save failed
                            playback_file = source_audio_path

                    except PermissionError:
                        # Use alternative filename with _02 suffix
                        alt_save_filename = os.path.join(
                            saved_dir, f"{content_prefix}_{timestamp}_02.wav")
                        print(
                            f"Permission denied for original save file. Using alternative file name: {alt_save_filename}")

                        try:
                            shutil.copy2(source_audio_path, alt_save_filename)
                            if os.path.exists(alt_save_filename):
                                playback_file = alt_save_filename  # Use alternative saved file for playback
                                print(
                                    f"Saved audio file to: {alt_save_filename}")
                            else:
                                print(
                                    f"Warning: Alternative saved file {alt_save_filename} was not created successfully")
                                # Fall back to original file if save failed
                                playback_file = source_audio_path
                        except Exception as inner_e:
                            print(
                                f"Error saving to alternative file: {inner_e}")
                            playback_file = source_audio_path

                except Exception as e:
                    print(f"Error saving audio file: {e}")
                    # Fall back to original file if save failed
                    playback_file = source_audio_path

            # CASE 2: Save mode is disabled - use anything_tts.wav or anything_tts02.wav
            else:
                try:
                    # Get the app directory
                    app_dir = get_app_directory("anythingllm")

                    destination_filename = os.path.join(
                        app_dir, "anything_tts.wav")
                    os.makedirs(os.path.dirname(
                        destination_filename), exist_ok=True)

                    # Use shutil.copy2 instead of os.replace for cross-drive compatibility
                    import shutil
                    shutil.copy2(source_audio_path, destination_filename)
                    playback_file = destination_filename
                except PermissionError:
                    # If we get permission error
                    alt_destination_filename = os.path.join(
                        app_dir, "anything_tts02.wav")
                    print(
                        f"Permission denied for playback file. Using alternative file name: {alt_destination_filename}")

                    os.makedirs(os.path.dirname(
                        alt_destination_filename), exist_ok=True)

                    # Use shutil.copy2 instead of os.replace
                    import shutil
                    shutil.copy2(source_audio_path, alt_destination_filename)
                    playback_file = alt_destination_filename
                except Exception as e:
                    print(f"Error copying audio file: {e}")
                    # If all else fails, use the original file
                    playback_file = source_audio_path

            # Now play the determined file
            if playback_file:
                if audio_player == "playsound":
                    sound = play_audio_cross_platform(
                        playback_file, block=False)
                else:  # default_media_player
                    # Use the system's default media player
                    open_file_with_default_app(playback_file)
                    print("Playing audio with system default media player")
            else:
                print("No valid playback file was created - cannot play audio")

            FIRSTIME = False
        except Exception as e:
            end_time = time.time()
            elapsed_time = end_time - start_time
            print(
                f"Tried with error, lost {elapsed_time:.1f} seconds.")
            print(
                f"Error in TTS processing: {e}. You have to have F5-tts installed and running in the background. Skipping TTS.")

    def show_menu(self):
        """Display the settings menu and handle user input."""
        self.menu_active = True

        # Save the original console state
        original_console = self.console
        # Temporarily disable the non-blocking console while in menu
        self.console = None

        # Declare all globals at the beginning of the method
        global f5tts_client, f5tts_remove_silence, f5tts_cross_fade, f5tts_nfe, f5tts_speed, audio_player, f5tts_save_audio

        try:
            while self.menu_active and self.running:
                os.system('cls' if os.name == 'nt' else 'clear')
                print("\n=== AnythingLLM Monitor Settings ===")
                print(f"1. Check Interval: {self.check_interval} seconds")
                print(
                    f"2. Check for new messages by: {self.monitor_by.upper()}")
                print(f"3. F5-TTS reference audio: {self.f5tts_selected_ref}")
                print(f"4. F5-TTS settings:")
                print(
                    f"   - Server URL: {f5tts_client} (default: http://127.0.0.1:7860/)")
                print(
                    f"   - Remove silence: {f5tts_remove_silence} (default: False)")
                print(f"   - Cross-fade: {f5tts_cross_fade} (default: 0.15)")
                print(f"   - NFE value: {f5tts_nfe} (default: 16)")
                print(f"   - Speed: {f5tts_speed} (default: 1.0)")
                print(f"5. Audio player: {audio_player}")
                print(
                    f"6. Show checking: {'On' if self.show_checking else 'Off'}")
                print(f"7. Save the F5-TTS audio: {f5tts_save_audio}")
                print(f"8. Reset tracking (highest ID and latest timestamp)")
                print(f"9. Save and exit menu")
                print(f"10. Exit program")

                try:
                    choice = input("\nEnter your choice (1-10): ")

                    if choice == '1':
                        try:
                            new_interval = int(
                                input("Enter new check interval (1-10 seconds): "))
                            if 1 <= new_interval <= 10:
                                self.check_interval = new_interval
                                print(
                                    f"Check interval updated to {self.check_interval} seconds")
                            else:
                                print("Interval must be between 1 and 10 seconds")
                        except ValueError:
                            print("Please enter a valid number")
                        input("Press Enter to continue...")

                    elif choice == '2':
                        print("\nSelect how to check for new messages:")
                        print(
                            "1. By ID (show messages with higher ID than last seen)")
                        print("2. By timestamp (show messages newer than last seen)")
                        print(
                            "3. By both (show messages with either higher ID or newer timestamp)")

                        monitor_choice = input("Enter choice (1-3): ")
                        if monitor_choice == '1':
                            self.monitor_by = "id"
                            print("Will monitor by message ID")
                        elif monitor_choice == '2':
                            self.monitor_by = "timestamp"
                            print("Will monitor by message timestamp")
                        elif monitor_choice == '3':
                            self.monitor_by = "both"
                            print("Will monitor by both ID and timestamp")
                        else:
                            print("Invalid choice, keeping current setting")
                        input("Press Enter to continue...")

                    elif choice == '3':
                        # Scan for reference audio files
                        ref_files = scan_reference_files()

                        if not ref_files:
                            print(
                                f"\nNo valid reference audio files found in {get_reference_audio_path()}")
                            print("Would you like to:")
                            print("1. Create a sample README file with instructions")
                            print(
                                "2. Open the reference directory to add files manually")
                            print("3. Continue without reference audio")

                            option = input("Enter choice (1-3): ")

                            if option == '1':
                                create_sample_reference_files()
                                input("Press Enter to continue...")
                            elif option == '2':
                                ref_dir = get_reference_audio_path()
                                if os.path.exists(ref_dir):
                                    open_file_with_default_app(ref_dir)
                                    print(
                                        f"Opened reference directory: {ref_dir}")
                                else:
                                    print(
                                        f"Reference directory doesn't exist: {ref_dir}")
                                input("Press Enter to continue...")
                            continue

                        print("\nAvailable reference audio files:")
                        print("0. None (disable TTS)")
                        for idx, ref in enumerate(ref_files, 1):
                            print(
                                f"{idx}. {ref['name']} - Text: \"{ref['text_content'][:40]}{'...' if len(ref['text_content']) > 40 else ''}\"")

                        try:
                            ref_choice = int(
                                input(f"\nSelect reference audio (0-{len(ref_files)}): "))
                            if ref_choice == 0:
                                self.f5tts_selected_ref = "not chosen"
                                global f5tts_ref_audio, f5tts_ref_text
                                f5tts_ref_audio = "not chosen"
                                f5tts_ref_text = ""
                                print(
                                    "F5-TTS disabled - no reference audio selected")
                            elif 1 <= ref_choice <= len(ref_files):
                                selected = ref_files[ref_choice-1]
                                self.f5tts_selected_ref = selected['name']
                                f5tts_ref_audio = selected['audio_path']
                                f5tts_ref_text = selected['text_content']
                                print(
                                    f"Selected reference audio: {selected['name']}")
                            else:
                                print("Invalid choice")
                        except ValueError:
                            print("Please enter a valid number")

                        input("Press Enter to continue...")

                    elif choice == '4':
                        print("\nF5-TTS Settings:")
                        print("1. Server URL")
                        print("2. Remove silence")
                        print("3. Cross-fade duration")
                        print("4. NFE value")
                        print("5. Speed")
                        print("6. Back to main menu")

                        setting_choice = input(
                            "\nSelect setting to change (1-6): ")

                        if setting_choice == '1':
                            new_url = input(
                                f"Enter new F5-TTS server URL (current: {f5tts_client}): ")
                            if new_url:
                                f5tts_client = new_url
                                print(f"Server URL updated to: {f5tts_client}")

                        elif setting_choice == '2':
                            print(
                                f"Remove silence is currently: {f5tts_remove_silence}")
                            toggle = input("Toggle (y/n)? ").lower()
                            if toggle == 'y':
                                f5tts_remove_silence = not f5tts_remove_silence
                                print(
                                    f"Remove silence set to: {f5tts_remove_silence}")

                        elif setting_choice == '3':
                            try:
                                new_fade = float(
                                    input(f"Enter new cross-fade duration (0.0-1.0, current: {f5tts_cross_fade}): "))
                                if 0.0 <= new_fade <= 1.0:
                                    f5tts_cross_fade = new_fade
                                    print(
                                        f"Cross-fade duration set to: {f5tts_cross_fade}")
                                else:
                                    print("Value must be between 0.0 and 1.0")
                            except ValueError:
                                print("Please enter a valid number")

                        elif setting_choice == '4':
                            try:
                                new_nfe = int(
                                    input(f"Enter new NFE value (4-64, current: {f5tts_nfe}): "))
                                if 4 <= new_nfe <= 64 and new_nfe % 2 == 0:
                                    f5tts_nfe = new_nfe
                                    print(f"NFE value set to: {f5tts_nfe}")
                                else:
                                    print(
                                        "Value must be between 4 and 64 and be an even number")
                            except ValueError:
                                print("Please enter a valid number")

                        elif setting_choice == '5':
                            try:
                                new_speed = float(
                                    input(f"Enter new speed (0.5-2.0, current: {f5tts_speed}): "))
                                if 0.5 <= new_speed <= 2.0:
                                    f5tts_speed = new_speed
                                    print(f"Speed set to: {f5tts_speed}")
                                else:
                                    print("Value must be between 0.5 and 2.0")
                            except ValueError:
                                print("Please enter a valid number")

                        input("Press Enter to continue...")

                    elif choice == '5':
                        print("\nSelect audio player:")
                        print("1. Playsound (in-app playback)")
                        print(
                            "2. Default media player (system's default audio player)")

                        player_choice = input("Enter choice (1-2): ")
                        if player_choice == '1':
                            audio_player = "playsound"
                            print("Selected player: Playsound (in-app)")
                        elif player_choice == '2':
                            audio_player = "default_media_player"
                            print("Selected player: System default media player")
                        else:
                            print("Invalid choice, keeping current setting")
                        input("Press Enter to continue...")

                    elif choice == '6':
                        print("\nShow checking process:")
                        print(
                            f"Currently: {'On' if self.show_checking else 'Off'}")
                        toggle = input("Toggle (y/n)? ").lower()
                        if toggle == 'y':
                            self.show_checking = not self.show_checking
                            print(
                                f"Show checking process set to: {'On' if self.show_checking else 'Off'}")
                        input("Press Enter to continue...")

                    elif choice == '7':
                        print("\nSave F5-TTS audio:")
                        print("1. Don't save (temporary files only)")
                        print("2. Save audio files")

                        save_choice = input("Enter choice (1-2): ")
                        if save_choice == '1':
                            f5tts_save_audio = "nosave"
                            print("F5-TTS audio will not be saved permanently")
                        elif save_choice == '2':
                            f5tts_save_audio = "save"
                            print("F5-TTS audio will be saved as permanent files")
                        else:
                            print("Invalid choice, keeping current setting")
                        input("Press Enter to continue...")

                    elif choice == '8':
                        print("\nReset tracking:")
                        print(
                            f"Current highest chat ID: {self.highest_chat_id}")
                        print(
                            f"Current latest timestamp: {self.latest_timestamp}")
                        confirm = input(
                            "Are you sure you want to reset tracking? (y/n): ").lower()
                        if confirm == 'y':
                            self.highest_chat_id = 0
                            self.latest_timestamp = ""
                            self.seen_responses = set()
                            print(
                                "Tracking has been reset. Next check will establish new baselines.")
                        else:
                            print("Reset cancelled")
                        input("Press Enter to continue...")

                    elif choice == '9':
                        print("Saving settings and exiting menu...")
                        self._save_seen_responses()
                        self.menu_active = False

                    elif choice == '10':
                        confirm = input(
                            "Are you sure you want to exit the program? (y/n): ").lower()
                        if confirm == 'y':
                            print("Exiting program...")
                            self._save_seen_responses()
                            self.running = False
                            self.menu_active = False
                        else:
                            print("Exit cancelled")
                            input("Press Enter to continue...")

                    else:
                        print(
                            "Invalid choice. Please enter a number between 1 and 10.")
                        input("Press Enter to continue...")

                except Exception as e:
                    print(f"Error in menu: {e}")
                    import traceback
                    traceback.print_exc()
                    input("Press Enter to continue...")
        finally:
            # Restore the original console setup
            self.console = original_console

        # Clear the screen before returning to monitoring
        os.system('cls' if os.name == 'nt' else 'clear')
        print(f"Monitoring with check interval: {self.check_interval} seconds")
        print(f"Checking for new messages by: {self.monitor_by.upper()}")
        if self.monitor_by == "id":
            print(
                f"Only showing responses with chat ID > {self.highest_chat_id}")
        elif self.monitor_by == "timestamp":
            print(
                f"Only showing responses created after: {self.latest_timestamp}")
        elif self.monitor_by == "both":
            print(
                f"Showing responses with either chat ID > {self.highest_chat_id} OR created after: {self.latest_timestamp}")
        print(f"Press 's' at any time to access settings menu")

    def key_listener(self):
        """Listen for keyboard input to access the settings menu."""
        while self.running:
            if self.menu_active:
                # Skip checking for input if menu is already active
                time.sleep(0.1)
                continue

            # Check for key presses using our non-blocking console
            key = self.console.check_input()
            if key:
                if key.lower() == 's':
                    print("\nOpening settings menu...")
                    self.show_menu()
                # Could add more key commands here

            # Small sleep to prevent high CPU usage
            time.sleep(0.1)

    def run(self):
        """Main method to start monitoring."""
        print(
            f"Starting AnythingLLM Monitor with check interval: {self.check_interval} seconds")
        print(f"Checking for new messages by: {self.monitor_by.upper()}")
        print(f"F5-TTS reference audio: {self.f5tts_selected_ref}")
        print(f"Press 's' at any time to access settings menu")

        # Start keyboard listener in a separate thread
        listener_thread = threading.Thread(
            target=self.key_listener, daemon=True)
        listener_thread.start()

        try:
            while self.running:
                # Skip API calls if menu is active
                if not self.menu_active:
                    # Fetch and process responses
                    responses_data = self.fetch_responses()
                    new_responses = self.process_new_responses(responses_data)

                    # Notify if new responses found
                    if new_responses:
                        self.notify_new_responses(new_responses)
                        self._save_seen_responses()

                # Wait for next check
                time.sleep(self.check_interval)

        except KeyboardInterrupt:
            print("\nMonitor stopped by user.")
            self.running = False
            self._save_seen_responses()
        except Exception as e:
            print(f"Error in monitor: {e}")
            import traceback
            traceback.print_exc()
            self.running = False
            self._save_seen_responses()


def load_config():
    """Load configuration from config file."""
    config = {
        'base_url': "http://localhost:3001/api",
        'api_key': "your anythingllm api key",
        'check_interval': 5,
        'f5tts_client': "http://127.0.0.1:7860/",
        'f5tts_remove_silence': False,
        'f5tts_cross_fade': 0.15,
        'f5tts_nfe': 16,
        'f5tts_speed': 1.0,
        'audio_player': "playsound",
        'show_checking': False,
        'monitor_by': "timestamp",
        'f5tts_save_audio': "nosave"  # Add default value
    }

    config_file = "config_f5tts_any.txt"

    # Create default config file if it doesn't exist
    if not os.path.exists(config_file):
        try:
            with open(config_file, 'w') as f:
                for key, value in config.items():
                    f.write(f"{key}={value}\n")
            print(f"Created default configuration file: {config_file}")
            print("Please edit this file to set your AnythingLLM API key.")
        except Exception as e:
            print(f"Error creating configuration file: {e}")

    # Load settings from file
    try:
        with open(config_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    try:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()

                        # Convert value types appropriately
                        if value.lower() == 'true':
                            config[key] = True
                        elif value.lower() == 'false':
                            config[key] = False
                        elif value.replace('.', '', 1).isdigit():
                            if '.' in value:
                                config[key] = float(value)
                            else:
                                config[key] = int(value)
                        else:
                            config[key] = value
                    except ValueError:
                        # Skip malformed lines
                        pass
    except Exception as e:
        print(f"Error reading configuration file: {e}")

    return config


def save_config(config):
    """Save configuration to config file."""
    config_file = "config_f5tts_any.txt"
    try:
        with open(config_file, 'w') as f:
            for key, value in config.items():
                f.write(f"{key}={value}\n")
        print(f"Configuration saved to {config_file}")
    except Exception as e:
        print(f"Error saving configuration: {e}")


def play_audio_cross_platform(file_path, block=False):
    """Cross-platform audio playback function that doesn't rely on PyWin32"""
    import subprocess
    import threading
    import time
    import signal

    # Define global variable for tracking audio players
    global _audio_players
    # Initialize it if it doesn't exist yet
    if not '_audio_players' in globals() or _audio_players is None:
        _audio_players = []

    class AudioPlayer(threading.Thread):
        def __init__(self, file_path):
            super().__init__(daemon=True)
            self.file_path = file_path
            self.process = None
            self._stop_event = threading.Event()
            self._stopped = False

            # Stop any existing players
            global _audio_players
            # Create a copy of the list to safely iterate
            for player in list(_audio_players):
                if player != self and player.is_alive():
                    try:
                        player.stop()
                    except:
                        pass

            # Add self to players list
            _audio_players.append(self)

        def stop(self):
            """Stop the audio playback"""
            self._stop_event.set()
            self._stopped = True

            # Platform-specific stopping
            if os.name == 'nt':  # Windows
                # Try to stop using winsound if possible
                try:
                    import winsound
                    winsound.PlaySound(None, winsound.SND_PURGE)
                except:
                    pass

            # Try to stop the process
            self._stop_process()

        def _stop_process(self):
            """Helper method to stop the process and reduce nesting"""
            if not self.process:
                return

            try:
                # Give the process a SIGTERM first
                if hasattr(signal, 'SIGTERM'):
                    try:
                        self.process.send_signal(signal.SIGTERM)
                        # Give it a moment to terminate
                        time.sleep(0.1)
                    except:
                        pass

                # If it's still running, terminate it
                if self.process.poll() is None:
                    self.process.terminate()
                    time.sleep(0.1)

                # If still running, force kill
                if self.process.poll() is None:
                    self._force_kill_process()
            except Exception as e:
                print(f"Error terminating audio process: {e}")

        def _force_kill_process(self):
            """Helper method to forcibly kill the process"""
            if os.name == 'nt':
                # On Windows, use taskkill to forcefully terminate the process
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(self.process.pid)],
                               shell=True, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
            else:
                # On Unix, use kill -9
                self.process.kill()

        def is_alive(self):
            # Check if thread is alive and not stopped
            if self._stopped:
                return False

            # Check if process is still running
            if self.process and self.process.poll() is None:
                return True

            return super().is_alive()

        def run(self):
            """Play the audio file"""
            if self._stop_event.is_set():
                return

            try:
                # Platform-specific playback
                self._play_audio_file()

                # Wait for the process to complete if it exists and block is True
                if block and self.process:
                    self.process.wait()
            except Exception as e:
                print(f"Error playing audio: {e}")
            finally:
                # Cleanup when done
                if self in _audio_players:
                    _audio_players.remove(self)

        def _play_audio_file(self):
            """Platform-specific audio playback implementation"""
            if os.name == 'nt':  # Windows
                # First try with winsound if available
                try:
                    import winsound
                    winsound.PlaySound(
                        self.file_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
                except ImportError:
                    # If winsound is not available, use playsound
                    try:
                        from playsound3 import playsound
                        # Start in a separate thread if non-blocking
                        if not block:
                            threading.Thread(
                                target=playsound,
                                args=(self.file_path,),
                                daemon=True
                            ).start()
                        else:
                            playsound(self.file_path)
                    except ImportError:
                        # Fall back to default media player
                        self.process = subprocess.Popen(['start', '', self.file_path],
                                                        shell=True)

            elif sys.platform == 'darwin':  # macOS
                self.process = subprocess.Popen(['afplay', self.file_path],
                                                stdout=subprocess.DEVNULL,
                                                stderr=subprocess.DEVNULL)

            else:  # Linux and other Unix
                # Try different players until one works
                players = ['aplay', 'paplay', 'mpg123', 'mpg321']
                for player in players:
                    try:
                        self.process = subprocess.Popen([player, self.file_path],
                                                        stdout=subprocess.DEVNULL,
                                                        stderr=subprocess.DEVNULL)
                        if self.process.poll() is None:  # Process started successfully
                            break
                    except FileNotFoundError:
                        continue

    # Create and start the player
    player = AudioPlayer(file_path)
    player.start()

    # If blocking, wait for the player to finish
    if block:
        player.join()

    return player


def create_sample_reference_files():
    """Create a sample reference audio/text pair if none exist."""
    ref_dir = get_reference_audio_path()

    # Ensure directory exists
    if not os.path.exists(ref_dir):
        try:
            os.makedirs(ref_dir)
            print(f"Created reference directory: {ref_dir}")
        except Exception as e:
            print(f"Error creating reference directory: {e}")
            return False

    # Path for sample text file
    sample_text_path = os.path.join(ref_dir, "README.txt")

    # Create sample instructions
    sample_text = """
REFERENCE AUDIO INSTRUCTIONS:

1. Place audio files (WAV, MP3, OGG, FLAC) in this directory.
2. For each audio file, create a matching text file with the same name.
   Example: For "voice.mp3", create "voice.txt"
3. The text file should contain the exact transcript of what is spoken in the audio file.

Note: The F5-TTS system uses these reference files to match the voice characteristics 
when generating speech for new text.
"""

    try:
        with open(sample_text_path, 'w', encoding='utf-8') as f:
            f.write(sample_text)
        print(f"Created sample instructions file at: {sample_text_path}")
        return True
    except Exception as e:
        print(f"Error creating sample file: {e}")
        return False


if __name__ == "__main__":
    config = load_config()
    monitor = AnythingLLMMonitor(config)
    monitor.run()
